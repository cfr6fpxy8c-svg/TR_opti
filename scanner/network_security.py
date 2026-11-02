"""Outbound market requests are limited to known public HTTPS API routes."""
from __future__ import annotations

import urllib.parse
import urllib.request

BASE_URLS = {
    "https://fapi.binance.com/fapi/v1",
    "https://www.binance.com/fapi/v1",
    "https://data-api.binance.vision/api/v3",
    "https://api.bybit.com",
}
_BINANCE_ROUTES = {"exchangeInfo", "ticker/24hr", "klines", "premiumIndex", "openInterest"}
_BYBIT_ROUTES = {"instruments-info", "tickers", "kline", "open-interest"}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def validate_market_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.username is not None
            or parsed.password is not None or parsed.fragment
            or parsed.port not in (None, 443)):
        raise ValueError("market API requires HTTPS without credentials or fragments")
    routes = set()
    for base in BASE_URLS:
        parts = urllib.parse.urlsplit(base)
        if parsed.hostname == parts.hostname:
            names = _BYBIT_ROUTES if parts.hostname == "api.bybit.com" else _BINANCE_ROUTES
            prefix = "/v5/market/" if parts.hostname == "api.bybit.com" else parts.path + "/"
            routes.update(prefix + name for name in names)
    if parsed.path not in routes:
        raise ValueError("market API host or route is not allowed")


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # No request may escape the allowlist through an upstream redirect.
        return None
