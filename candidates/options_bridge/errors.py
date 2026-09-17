"""Structured errors for the options bridge HTTP envelope."""
from __future__ import annotations


class BridgeError(Exception):
    """
    Application error that becomes {"ok":false,"error":{code,message}}.

    status is the HTTP status code. Data endpoints use 503 when TWS is down.
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = int(status)

    def as_error(self) -> dict:
        """Phase 9A error object (no stack traces, no secrets)."""
        return {"code": self.code, "message": self.message}


class TwsDisconnected(BridgeError):
    """TWS paper API is not reachable — caller should retry after TWS is up."""

    def __init__(self, message: str = "TWS paper API is not connected (127.0.0.1:7497).") -> None:
        super().__init__("TWS_DISCONNECTED", message, status=503)


class TwsTimeout(BridgeError):
    """An IB call did not finish within REQUEST_TIMEOUT_SEC — never hang the HTTP client."""

    def __init__(self, message: str = "TWS request timed out.") -> None:
        super().__init__("TWS_TIMEOUT", message, status=504)


class OrderRouteForbidden(BridgeError):
    """Any order-like path is refused. This process never calls placeOrder."""

    def __init__(self, path: str) -> None:
        super().__init__(
            "ORDER_ROUTE_FORBIDDEN",
            f"Read-only options bridge refuses order route {path!r}. "
            "No place / modify / cancel is implemented.",
            status=403,
        )
