"""Domain-specific exceptions for RideStream v2."""


class DomainError(Exception):
    """Base domain error."""


class InvalidRideStateError(DomainError):
    """Raised when a ride state transition is invalid."""


class RideNotFoundError(DomainError):
    """Raised when a ride cannot be found."""


class InvalidLocationError(DomainError):
    """Raised when location coordinates are out of range."""


class InvalidFareError(DomainError):
    """Raised when fare calculation produces invalid result."""
