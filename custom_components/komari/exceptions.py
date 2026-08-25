"""Exceptions raised by the Komari API client."""


class KomariError(Exception):
    """Base exception for Komari failures."""


class KomariConnectionError(KomariError):
    """Raised when the Komari panel cannot be reached."""


class KomariProtocolError(KomariError):
    """Raised when the panel returns an invalid RPC response."""


class KomariRpcError(KomariError):
    """Raised when an RPC method returns an error."""

    def __init__(self, code: int | None, _message: str) -> None:
        """Initialize a sanitized RPC exception."""
        self.code = code
        detail = f" (code {code})" if code is not None else ""
        super().__init__(f"Komari RPC request failed{detail}")


class KomariMethodNotFoundError(KomariRpcError):
    """Raised when an RPC method is unavailable."""


class KomariPermissionError(KomariRpcError):
    """Raised when credentials do not permit an RPC method."""


class KomariAuthenticationError(KomariError):
    """Raised when Komari rejects the configured credentials."""


class KomariUnsupportedError(KomariError):
    """Raised when the panel does not expose required RPC2 capabilities."""
