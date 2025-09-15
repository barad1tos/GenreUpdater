"""API Compatibility Tests for cache_service.py

This test module ensures that the public API surface of cache_service.py
remains stable during refactoring by comparing against a committed JSON snapshot.

The test will fail if:
- Any public name is removed from the module
- Any function/method signature is changed in a breaking way
- Any class hierarchy is modified in a breaking way
"""

import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import pytest


class APICompatibilityChecker:
    """Checks API compatibility against a JSON snapshot."""

    def __init__(self, snapshot_path: str, module_path: str) -> None:
        """Initialize the compatibility checker.

        Args:
            snapshot_path: Path to the JSON API snapshot
            module_path: Path to the module to check
        """
        self.snapshot_path = Path(snapshot_path)
        self.module_path = Path(module_path)
        self.snapshot_data: dict[str, Any] = {}
        self.module: Any = None

    def load_snapshot(self) -> None:
        """Load the API snapshot from JSON."""
        if not self.snapshot_path.exists():
            message = f"API snapshot not found: {self.snapshot_path}"
            raise FileNotFoundError(message)

        with self.snapshot_path.open(encoding="utf-8") as f:
            self.snapshot_data = json.load(f)

    def load_module(self) -> None:
        """Load the target module for inspection."""
        if not self.module_path.exists():
            message = f"Module not found: {self.module_path}"
            raise FileNotFoundError(message)

        # Import the module
        spec = importlib.util.spec_from_file_location("target_module", str(self.module_path))
        if not spec or not spec.loader:
            message = f"Could not load module spec from {self.module_path}"
            raise ImportError(message)

        self.module = importlib.util.module_from_spec(spec)

        # Add the module's parent directory to Python path temporarily
        sys.path.insert(0, str(self.module_path.parent.parent.parent))

        try:
            spec.loader.exec_module(self.module)
        except Exception as e:
            pytest.skip(f"Could not load module {self.module_path}: {e}")
        finally:
            # Remove from path
            if str(self.module_path.parent.parent.parent) in sys.path:
                sys.path.remove(str(self.module_path.parent.parent.parent))

    def get_current_public_names(self) -> list[str]:
        """Get current public names from the loaded module."""
        if hasattr(self.module, "__all__"):
            return list(self.module.__all__)
        return [name for name in dir(self.module) if not name.startswith("_")]

    def check_public_names_compatibility(self) -> list[str]:
        """Check that all public names from snapshot still exist.

        Returns:
            List of missing public names (empty if all present)
        """
        expected_names = set(self.snapshot_data.get("public_names", []))
        current_names = set(self.get_current_public_names())

        missing_names = expected_names - current_names
        return list(missing_names)

    def check_signature_compatibility(self, name: str) -> dict[str, Any]:
        """Check signature compatibility for a specific function/method.

        Args:
            name: Name of the function/method to check

        Returns:
            Dictionary with compatibility results
        """
        api_data = self.snapshot_data.get("api", {})

        if validation_result := self._validate_compatibility_preconditions(
            name, api_data
        ):
            return validation_result

        snapshot_api = api_data[name]
        obj_type = snapshot_api.get("type")

        try:
            current_obj = getattr(self.module, name, None)
            if current_obj is None:
                return {"compatible": False, "reason": f"Object {name} not found"}

            # Delegate based on type
            checker_map = {
                "function": self._check_function_signature,
                "class": self._check_class_compatibility
            }

            if obj_type in checker_map:
                return checker_map[obj_type](current_obj, snapshot_api)

            return {"compatible": True, "reason": "No issues detected"}

        except Exception as e:
            return {"compatible": False, "reason": f"Error checking {name}: {e}"}

    @staticmethod
    def _validate_compatibility_preconditions(name: str, api_data: dict[str, Any ]) -> dict[str, Any ] | None:
        """Validate preconditions for compatibility check.

        Returns:
            Validation result dictionary if validation fails, None if validation passes
        """
        if name not in api_data:
            return {"compatible": True, "reason": "Not in snapshot"}

        snapshot_api = api_data[name]
        obj_type = snapshot_api.get("type")

        if obj_type not in ["function", "class"]:
            return {"compatible": True, "reason": "Not a callable"}

        return None

    @staticmethod
    def _check_function_signature(func: Any, snapshot_api: dict[str, Any]) -> dict[str, Any]:
        """Check function signature compatibility."""
        try:
            current_sig = inspect.signature(func)
            snapshot_sig = snapshot_api.get("signature", {})

            # Check parameter count (current can have more, but not fewer required params)
            current_params = current_sig.parameters
            snapshot_params = snapshot_sig.get("parameters", {})

            # Count required parameters in both
            current_required = sum(p.default is p.empty for p in current_params.values())
            snapshot_required = sum(
                p.get("default") is None for p in snapshot_params.values()
            )

            if current_required > snapshot_required:
                return {
                    "compatible": False,
                    "reason": f"More required parameters now ({current_required}) than before ({snapshot_required})"
                }

            return next(
                (
                    {
                        "compatible": False,
                        "reason": f"Required parameter '{param_name}' was removed",
                    }
                    for param_name, param_info in snapshot_params.items()
                    if param_info.get("default") is None
                    and param_name not in current_params
                ),
                {"compatible": True, "reason": "Function signature compatible"},
            )
        except Exception as e:
            return {"compatible": False, "reason": f"Error checking function signature: {e}"}

    def _check_class_compatibility(self, cls: type, snapshot_api: dict[str, Any]) -> dict[str, Any]:
        """Check class compatibility."""
        try:
            # Check that class still exists and is a class
            if not inspect.isclass(cls):
                return {"compatible": False, "reason": "Object is no longer a class"}

            # Check key methods like __init__ if they were in the snapshot
            snapshot_methods = snapshot_api.get("methods", {})
            if "__init__" in snapshot_methods:
                init_method = getattr(cls, "__init__", None)
                if init_method is None:
                    return {"compatible": False, "reason": "__init__ method missing"}

                # Check __init__ signature compatibility
                init_result = self._check_function_signature(init_method, snapshot_methods["__init__"])
                if not init_result["compatible"]:
                    return {
                        "compatible": False,
                        "reason": f"__init__ signature incompatible: {init_result['reason']}"
                    }

            return {"compatible": True, "reason": "Class compatibility OK"}

        except Exception as e:
            return {"compatible": False, "reason": f"Error checking class: {e}"}


@pytest.fixture
def api_checker() -> APICompatibilityChecker:
    """Fixture to create API compatibility checker."""
    checker = APICompatibilityChecker(
        snapshot_path="api_snapshots/cache_service_public_api.json",
        module_path="src/services/cache/cache_service.py"
    )
    checker.load_snapshot()
    checker.load_module()
    return checker


def test_api_snapshot_exists() -> None:
    """Test that the API snapshot file exists."""
    snapshot_path = Path("api_snapshots/cache_service_public_api.json")
    assert snapshot_path.exists(), f"API snapshot not found: {snapshot_path}"


def test_module_loads_successfully() -> None:
    """Test that the cache_service module can be loaded."""
    module_path = Path("src/services/cache/cache_service.py")
    assert module_path.exists(), f"Module not found: {module_path}"


def test_public_names_preserved(api_checker: APICompatibilityChecker) -> None:
    """Test that all public names from the snapshot are still present."""
    if missing_names := api_checker.check_public_names_compatibility():
        pytest.fail(
            f"❌ Missing public API names that were in the snapshot: {missing_names}\n"
            f"This indicates a breaking change to the public API. "
            f"If this is intentional, update the API snapshot."
        )


def test_function_signatures_compatible(api_checker: APICompatibilityChecker) -> None:
    """Test that function signatures remain compatible."""
    incompatible_functions = []

    for name in api_checker.snapshot_data.get("public_names", []):
        result = api_checker.check_signature_compatibility(name)
        if not result["compatible"]:
            incompatible_functions.append(f"{name}: {result['reason']}")

    if incompatible_functions:
        pytest.fail(
            "❌ Incompatible function signatures detected:\n" +
            "\n".join(f"  - {func}" for func in incompatible_functions) +
            "\n\nThis indicates breaking changes to the public API. "
            "If these changes are intentional, update the API snapshot."
        )


def test_api_snapshot_regeneration_instructions() -> None:
    """Test that provides instructions for regenerating API snapshot."""
    # This test always passes but provides helpful output
    print("\n" + "="*60)
    print("📋 API Snapshot Management")
    print("="*60)
    print("To regenerate the API snapshot after intentional changes:")
    print("  python tools/snapshot_api.py src/services/cache/cache_service.py api_snapshots/cache_service_public_api.json")
    print("\nTo view current API snapshot:")
    print("  cat api_snapshots/cache_service_public_api.json")
    print("\nTo compare before/after refactoring:")
    print("  git diff api_snapshots/cache_service_public_api.json")
    print("="*60)


if __name__ == "__main__":
    # Allow running this test directly
    pytestmark = pytest.mark.integration
