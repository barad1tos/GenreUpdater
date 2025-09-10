"""Cache circuit breaker implementation for resilient cache operations.

This module implements the circuit breaker pattern for cache operations,
providing automatic failure detection, recovery mechanisms, and metrics collection
to ensure cache system reliability.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar, TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.cache.cache_protocol import CacheProtocol

# Type variable for circuit breaker operation return types
CircuitResult = TypeVar("CircuitResult")


def _create_failure_list() -> list[float]:
    """Create typed failure list."""
    return []


class CircuitBreakerState(Enum):
    """Circuit breaker states for cache operations."""

    CLOSED = "closed"  # Normal operation, requests allowed
    OPEN = "open"  # Failure threshold exceeded, requests blocked
    HALF_OPEN = "half_open"  # Testing recovery, limited requests allowed


@dataclass
class CircuitBreakerConfig:
    """Configuration for cache circuit breaker behavior."""

    failure_threshold: int = 5  # Failures before opening circuit
    recovery_timeout_seconds: float = 60.0  # Time before attempting recovery
    success_threshold: int = 3  # Successes needed to close the circuit
    monitoring_window_seconds: float = 300.0  # Window for failure counting
    max_half_open_requests: int = 10  # Max requests in half-open state


@dataclass
class CircuitBreakerMetrics:
    """Metrics tracking for circuit breaker operations."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    circuit_opens: int = 0
    circuit_closes: int = 0
    blocked_requests: int = 0
    recovery_attempts: int = 0

    # Sliding window tracking
    recent_failures: list[float] = field(default_factory=_create_failure_list)
    recent_successes: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0

    def record_success(self) -> None:
        """Record a successful operation."""
        self.total_requests += 1
        self.successful_requests += 1
        self.recent_successes += 1
        self.last_success_time = time.time()

    def record_failure(self) -> None:
        """Record a failed operation."""
        self.total_requests += 1
        self.failed_requests += 1
        current_time = time.time()
        self.recent_failures.append(current_time)
        self.last_failure_time = current_time

    def record_circuit_open(self) -> None:
        """Record circuit opening."""
        self.circuit_opens += 1

    def record_circuit_close(self) -> None:
        """Record circuit closing."""
        self.circuit_closes += 1
        self.recent_successes = 0  # Reset success counter

    def record_blocked_request(self) -> None:
        """Record a blocked request."""
        self.blocked_requests += 1

    def record_recovery_attempt(self) -> None:
        """Record recovery attempt."""
        self.recovery_attempts += 1

    def get_recent_failure_count(self, window_seconds: float) -> int:
        """Get the failure count within the time window."""
        current_time = time.time()
        cutoff_time = current_time - window_seconds

        # Clean old failures
        self.recent_failures = [failure_time for failure_time in self.recent_failures if failure_time > cutoff_time]

        return len(self.recent_failures)

    def get_success_rate(self) -> float:
        """Calculate overall success rate."""
        if self.total_requests == 0:
            return 1.0
        return self.successful_requests / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary format."""
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "circuit_opens": self.circuit_opens,
            "circuit_closes": self.circuit_closes,
            "blocked_requests": self.blocked_requests,
            "recovery_attempts": self.recovery_attempts,
            "success_rate": self.get_success_rate(),
            "recent_failure_count": len(self.recent_failures),
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time,
        }


class CacheCircuitBreaker:
    """Circuit breaker for cache operations with automatic recovery.

    Implements the circuit breaker pattern to protect cache operations
    from cascading failures and provide automatic recovery mechanisms.
    """

    def __init__(
        self,
        cache_backend: CacheProtocol[Any],
        config: CircuitBreakerConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize cache circuit breaker.

        Args:
            cache_backend: Underlying cache implementation
            config: Circuit breaker configuration
            logger: Logger for circuit breaker events

        """
        self.cache_backend = cache_backend
        self.config = config or CircuitBreakerConfig()
        self.logger = logger or logging.getLogger(__name__)

        self.state = CircuitBreakerState.CLOSED
        self.metrics = CircuitBreakerMetrics()
        self.last_state_change = time.time()
        self.half_open_requests = 0

        # Lock for thread-safe state changes
        self._state_lock = asyncio.Lock()

    async def get(
        self, key: str, default: str | dict[str, Any] | list[Any] | float | bool | None = None
    ) -> str | dict[str, Any] | list[Any] | int | float | bool | None:
        """Get cached value with circuit breaker protection.

        Args:
            key: Cache key to retrieve
            default: Default value if key not found

        Returns:
            Cached value or default if not found/circuit open

        """
        async with self._state_lock:
            if not self._should_allow_request():
                self.metrics.record_blocked_request()
                self.logger.warning("Circuit breaker OPEN: blocking cache get for key %s", key)
                return default

        try:
            result = await self.cache_backend.get(key, default)
        except (ConnectionError, TimeoutError) as error:
            # Network/connection related errors
            self._record_failure(error)
            self.logger.exception("Cache get failed for key %s", key)
            return default
        except (KeyError, ValueError) as error:
            # Data/key related errors
            self._record_failure(error)
            self.logger.warning("Cache get error for key %s: %s", key, error)
            return default
        except (OSError, RuntimeError, TypeError, AttributeError) as error:
            # Truly unexpected errors
            self._record_failure(error)
            self.logger.critical("Unexpected cache get error for key %s: %s", key, error)
            return default

        self._record_success()
        return result

    async def set(self, key: str, value: str | dict[str, Any] | list[Any] | float | bool, ttl: int | None = None) -> None:
        """Set cached value with circuit breaker protection.

        Args:
            key: Cache key to store
            value: Value to cache
            ttl: Time-to-live in seconds

        """
        async with self._state_lock:
            if not self._should_allow_request():
                self.metrics.record_blocked_request()
                self.logger.warning("Circuit breaker OPEN: blocking cache set for key %s", key)
                return

        try:
            await self.cache_backend.set(key, value, ttl)
        except (ConnectionError, TimeoutError) as error:
            # Network/connection related errors
            self._record_failure(error)
            self.logger.exception("Cache set failed for key %s", key)
            raise
        except (ValueError, OSError) as error:
            # Data/system related errors
            self._record_failure(error)
            self.logger.exception("Cache set error for key %s", key)
            raise
        except Exception as error:
            # Truly unexpected errors
            self._record_failure(error)
            self.logger.critical("Unexpected cache set error for key %s: %s", key, error)
            raise

        self._record_success()

    async def invalidate(self, key: str | list[str]) -> int:
        """Invalidate cache keys with circuit breaker protection.

        Args:
            key: Single key or list of keys to invalidate

        Returns:
            Count of keys successfully invalidated

        """
        async with self._state_lock:
            if not self._should_allow_request():
                self.metrics.record_blocked_request()
                self.logger.warning("Circuit breaker OPEN: blocking cache invalidation")
                return 0

        try:
            result = await self.cache_backend.invalidate(key)
        except (ConnectionError, TimeoutError) as error:
            # Network/connection related errors
            self._record_failure(error)
            self.logger.exception("Cache invalidation failed")
            return 0
        except (KeyError, ValueError) as error:
            # Data/key related errors
            self._record_failure(error)
            self.logger.warning("Cache invalidation error: %s", error)
            return 0
        except (OSError, RuntimeError, TypeError, AttributeError) as error:
            # Truly unexpected errors
            self._record_failure(error)
            self.logger.critical("Unexpected cache invalidation error: %s", error)
            return 0

        self._record_success()
        return result

    async def cleanup(self) -> int:
        """Clean cache with circuit breaker protection.

        Returns:
            Count of entries cleaned

        """
        async with self._state_lock:
            if not self._should_allow_request():
                self.metrics.record_blocked_request()
                self.logger.warning("Circuit breaker OPEN: blocking cache cleanup")
                return 0

        try:
            result = await self.cache_backend.cleanup()
        except (ConnectionError, TimeoutError) as error:
            # Network/connection related errors
            self._record_failure(error)
            self.logger.exception("Cache cleanup failed")
            return 0
        except (OSError, RuntimeError) as error:
            # System/runtime related errors
            self._record_failure(error)
            self.logger.exception("Cache cleanup error")
            return 0
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            # Truly unexpected errors
            self._record_failure(error)
            self.logger.critical("Unexpected cache cleanup error: %s", error)
            return 0

        self._record_success()
        return result

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker and cache statistics.

        Returns:
            Combined statistics from circuit breaker and cache

        """
        cache_stats = {}
        try:
            cache_stats = self.cache_backend.get_stats()
        except (AttributeError, ConnectionError, RuntimeError) as error:
            self.logger.warning("Failed to get cache backend stats: %s", error)
        except (OSError, ValueError, KeyError, TypeError):
            self.logger.exception("Unexpected error getting cache stats")

        circuit_stats: dict[str, Any] = {
            "circuit_breaker": {
                "state": self.state.value,
                "state_duration_seconds": time.time() - self.last_state_change,
                "config": {
                    "failure_threshold": self.config.failure_threshold,
                    "recovery_timeout": self.config.recovery_timeout_seconds,
                    "success_threshold": self.config.success_threshold,
                },
                "metrics": self.metrics.to_dict(),
            }
        }

        return {**cache_stats, **circuit_stats}

    def _should_allow_request(self) -> bool:
        """Determine if request should be allowed based on circuit state.

        Returns:
            True if request should proceed, False if blocked

        """
        current_time = time.time()

        match self.state:
            case CircuitBreakerState.CLOSED:
                # Normal operation - check if we should open circuit
                failure_count = self.metrics.get_recent_failure_count(self.config.monitoring_window_seconds)

                if failure_count >= self.config.failure_threshold:
                    self._transition_to_open()
                    return False

                return True

            case CircuitBreakerState.OPEN:
                # Check if recovery timeout has passed
                time_since_open = current_time - self.last_state_change

                if time_since_open >= self.config.recovery_timeout_seconds:
                    self._transition_to_half_open()
                    return True

                return False

            case CircuitBreakerState.HALF_OPEN:
                # Allow limited requests for testing
                if self.half_open_requests < self.config.max_half_open_requests:
                    self.half_open_requests += 1
                    return True

                return False

    def _record_success(self) -> None:
        """Record successful operation and handle state transitions."""
        self.metrics.record_success()

        if self.state == CircuitBreakerState.HALF_OPEN and self.metrics.recent_successes >= self.config.success_threshold:
            self._transition_to_closed()

    def _record_failure(self, _error: Exception) -> None:
        """Record failed operation and handle state transitions."""
        self.metrics.record_failure()

        if self.state == CircuitBreakerState.HALF_OPEN:
            # Return to open state on any failure during recovery
            self._transition_to_open()

    def _transition_to_open(self) -> None:
        """Transition circuit breaker to OPEN state."""
        self.state = CircuitBreakerState.OPEN
        self.last_state_change = time.time()
        self.half_open_requests = 0
        self.metrics.record_circuit_open()

        self.logger.error("Circuit breaker OPENED due to failure threshold (%d failures)", self.config.failure_threshold)

    def _transition_to_half_open(self) -> None:
        """Transition circuit breaker to HALF_OPEN state."""
        self.state = CircuitBreakerState.HALF_OPEN
        self.last_state_change = time.time()
        self.half_open_requests = 0
        self.metrics.recent_successes = 0
        self.metrics.record_recovery_attempt()

        self.logger.info("Circuit breaker transitioning to HALF_OPEN for recovery testing")

    def _transition_to_closed(self) -> None:
        """Transition circuit breaker to CLOSED state."""
        self.state = CircuitBreakerState.CLOSED
        self.last_state_change = time.time()
        self.half_open_requests = 0
        self.metrics.record_circuit_close()

        self.logger.info("Circuit breaker CLOSED - recovery successful after %d successes", self.config.success_threshold)

    async def force_open(self) -> None:
        """Manually force circuit breaker to OPEN state."""
        async with self._state_lock:
            self._transition_to_open()
            self.logger.warning("Circuit breaker manually forced OPEN")

    async def force_close(self) -> None:
        """Manually force circuit breaker to CLOSED state."""
        async with self._state_lock:
            self._transition_to_closed()
            self.logger.info("Circuit breaker manually forced CLOSED")

    async def reset_metrics(self) -> None:
        """Reset circuit breaker metrics."""
        async with self._state_lock:
            self.metrics = CircuitBreakerMetrics()
            self.logger.info("Circuit breaker metrics reset")


class CacheCircuitBreakerFactory:
    """Factory for creating cache circuit breakers with different configurations."""

    @classmethod
    def create_standard(
        cls: type[CacheCircuitBreakerFactory],
        cache_backend: CacheProtocol[Any],
        logger: logging.Logger | None = None,
    ) -> CacheCircuitBreaker:
        """Create a circuit breaker with standard configuration.

        Args:
            cache_backend: Cache implementation to protect
            logger: Logger for circuit breaker events

        Returns:
            CacheCircuitBreaker with standard settings

        """
        return CacheCircuitBreaker(
            cache_backend=cache_backend,
            config=CircuitBreakerConfig(),
            logger=logger,
        )

    @classmethod
    def create_sensitive(
        cls: type[CacheCircuitBreakerFactory],
        cache_backend: CacheProtocol[Any],
        logger: logging.Logger | None = None,
    ) -> CacheCircuitBreaker:
        """Create a circuit breaker with sensitive configuration.

        More responsive to failures, faster recovery attempts.

        Args:
            cache_backend: Cache implementation to protect
            logger: Logger for circuit breaker events

        Returns:
            CacheCircuitBreaker with sensitive settings

        """
        config = CircuitBreakerConfig(
            failure_threshold=3,  # Open faster
            recovery_timeout_seconds=30.0,  # Recover faster
            success_threshold=2,  # Close faster
            monitoring_window_seconds=180.0,  # Shorter window
            max_half_open_requests=5,  # Fewer test requests
        )

        return CacheCircuitBreaker(
            cache_backend=cache_backend,
            config=config,
            logger=logger,
        )

    @classmethod
    def create_resilient(
        cls: type[CacheCircuitBreakerFactory],
        cache_backend: CacheProtocol[Any],
        logger: logging.Logger | None = None,
    ) -> CacheCircuitBreaker:
        """Create a circuit breaker with resilient configuration.

        More tolerant of failures, longer recovery periods.

        Args:
            cache_backend: Cache implementation to protect
            logger: Logger for circuit breaker events

        Returns:
            CacheCircuitBreaker with resilient settings

        """
        config = CircuitBreakerConfig(
            failure_threshold=10,  # More tolerant
            recovery_timeout_seconds=120.0,  # Longer recovery
            success_threshold=5,  # More successes needed
            monitoring_window_seconds=600.0,  # Longer window
            max_half_open_requests=20,  # More test requests
        )

        return CacheCircuitBreaker(
            cache_backend=cache_backend,
            config=config,
            logger=logger,
        )


# Export public interfaces
__all__ = [
    "CacheCircuitBreaker",
    "CacheCircuitBreakerFactory",
    "CircuitBreakerConfig",
    "CircuitBreakerMetrics",
    "CircuitBreakerState",
]
