"""Exceptions raised by the futures client."""


class BinanceAPIError(Exception):
    """Binance returned an error payload (negative `code` or non-2xx HTTP)."""

    def __init__(self, code, message, status_code=None):
        self.code = code
        self.message = message
        self.status_code = status_code
        suffix = f" (HTTP {status_code})" if status_code else ""
        super().__init__(f"Binance API error {code}: {message}{suffix}")


class BinanceRequestError(Exception):
    """The HTTP request itself failed (timeout, connection error, ...)."""

    def __init__(self, message):
        super().__init__(f"Request failed: {message}")
