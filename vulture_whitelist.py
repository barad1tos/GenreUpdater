# Vulture whitelist - false positives
# These are required by Python protocols/interfaces

# Protocol method parameters (required by interface contract)
event_name  # AnalyticsServiceProtocol.track_event
properties  # AnalyticsServiceProtocol.track_event

# Context manager __exit__ parameters (required by protocol)
exc_val  # __exit__(self, exc_type, exc_val, exc_tb)
exc_tb   # __exit__(self, exc_type, exc_val, exc_tb)

# Exception handler traceback
tb  # except ... as e: tb = e.__traceback__
