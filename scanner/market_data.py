"""Binance public market-data client (stdlib only -- zero dependencies).

* Public market data ONLY. No API key, no account endpoints, no trading.
* Automatic endpoint failover: the canonical ``fapi.binance.com`` host is
  geo-blocked (HTTP 451) from some clouds (e.g. certain GitHub Actions / US
  IPs). The configured failover chain first retries the canonical futures
  host, then a futures API mirror, then -- as a clearly flagged degraded
  fallback -- the official Binance spot market-data mirror.
* Retries with exponential backoff on timeouts / 5xx / rate limits (429).
* Deterministic client errors (invalid symbol / delisted) raise immediately
  without retry or failover.
* Never fabricates data: on unrecoverable failure the caller receives an
  exception and must mark the scan degraded/failed.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1m": 60_000}


class MarketDataError(Exception):
    """Unrecoverable market-data failure after retries/failover."""

    def __init__(self, message: str, rate_limited: bool = False, client_error: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited
        self.client_error = client_error


class SymbolUnavailableError(MarketDataError):
    """Symbol delisted / temporarily unavailable (deterministic 4xx)."""

    def __init__(self, message: str):
        super().__init__(message, client_error=True)


@dataclass
class Candle:
    openTime: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    closeTime: int
    quoteVolume: float
    trades: int

    @property
    def closed(self) -> bool:
        return self.closeTime < int(time.time() * 1000)


def _to_f(value) -> float:
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        raise ValueError("non-finite price")
    return out


def parse_klines(rows: list) -> list[Candle]:
    """Parse Binance kline rows into Candle objects; drop malformed rows."""
    out: list[Candle] = []
    for row in rows:
        try:
            if not isinstance(row, (list, tuple)) or len(row) < 8:
                continue
            candle = Candle(
                openTime=int(row[0]),
                open=_to_f(row[1]), high=_to_f(row[2]),
                low=_to_f(row[3]), close=_to_f(row[4]),
                volume=_to_f(row[5]),
                closeTime=int(row[6]),
                quoteVolume=_to_f(row[7]),
                trades=int(row[8]) if len(row) > 8 and str(row[8]) != "" else 0,
            )
            if not (candle.low <= candle.high and candle.low > 0):
                continue
            if candle.openTime <= 0 or candle.closeTime <= candle.openTime:
                continue
            out.append(candle)
        except (ValueError, TypeError, IndexError):
            continue
    out.sort(key=lambda c: c.openTime)
    return out


def drop_incomplete(candles: list[Candle], now_ms: int | None = None) -> list[Candle]:
    """Return only fully-closed candles (closed when closeTime has passed).

    This is the first line of defence against lookahead: signal logic never
    sees an in-progress candle.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return [c for c in candles if c.closeTime < now]


@dataclass
class ClientStats:
    requests: int = 0
    errors: int = 0
    retries: int = 0
    rateLimitHits: int = 0
    lastError: str | None = None
    endpointUsed: str | None = None
    marketUsed: str | None = None

    def as_dict(self) -> dict:
        return {
            "requests": self.requests, "errors": self.errors, "retries": self.retries,
            "rateLimitHits": self.rateLimitHits, "lastError": self.lastError,
            "endpointUsed": self.endpointUsed, "marketUsed": self.marketUsed,
        }


class MarketDataClient:
    """Small resilient GET client for Binance public market data."""

    def __init__(self, endpoints: list[dict], timeout: float = 12.0,
                 max_retries: int = 3, backoff: float = 1.5, max_requests: int = 700):
        if not endpoints:
            raise ValueError("at least one endpoint required")
        # Fail loudly here rather than 100 lines later inside the retry loop:
        # passing a Config by mistake used to surface as "object of type
        # 'Config' has no len()" from _by_dialect.
        if not isinstance(endpoints, (list, tuple)):
            raise TypeError(
                "endpoints must be a list of endpoint dicts, got "
                f"{type(endpoints).__name__}; use make_client(cfg) to build "
                "a client from a Config")
        self.endpoints = list(endpoints)
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.max_requests = max_requests
        self.stats = ClientStats()
        self._endpoint_idx = 0

    # ------------------------------------------------------------------ core
    def _get(self, path: str, params: dict | None = None) -> dict | list:
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        last_exc: Exception | None = None
        # Only fail over between hosts that speak the SAME dialect. The request
        # path and params were built for one API shape, so sending them to a
        # host of another dialect can only 404 (a Binance "/exchangeInfo" is
        # meaningless to api.bybit.com). Crossing dialects is _by_dialect's job:
        # it re-builds the request in the right shape first.
        dialect = self._dialect()
        candidates = [i for i in range(len(self.endpoints))
                      if self.endpoints[i].get("dialect", "binance") == dialect]
        # Start at the currently selected endpoint, then wrap.
        if self._endpoint_idx in candidates:
            pivot = candidates.index(self._endpoint_idx)
            candidates = candidates[pivot:] + candidates[:pivot]
        for idx in candidates:
            ep = self.endpoints[idx]
            # Translated dialects (e.g. Bybit) pass their own absolute API path,
            # so the endpoint's ``base`` is the host root, not a path prefix.
            if path.startswith("/v5/"):
                root = ep.get("root") or ep["base"]
                url = root.rstrip("/") + path + query
            else:
                url = ep["base"].rstrip("/") + path + query
            for attempt in range(self.max_retries + 1):
                if self.stats.requests >= self.max_requests:
                    raise MarketDataError("request budget exhausted for this scan")
                self.stats.requests += 1
                try:
                    result = self._fetch(url)
                    self._endpoint_idx = idx
                    return result
                except SymbolUnavailableError:
                    self.stats.errors += 1
                    raise  # deterministic failure: retrying/failover cannot help
                except MarketDataError as exc:
                    last_exc = exc
                    self.stats.errors += 1
                    self.stats.lastError = str(exc)
                    if exc.rate_limited:
                        self.stats.rateLimitHits += 1
                    if attempt < self.max_retries:
                        self.stats.retries += 1
                        time.sleep(self.backoff * (attempt + 1) * (2.0 if exc.rate_limited else 1.0))
        raise MarketDataError(f"all {dialect} endpoints failed: {last_exc}")

    def _fetch(self, url: str) -> dict | list:
        request = urllib.request.Request(url, headers={
            "User-Agent": "crypto-signal-scanner/1.0 (public market data only)",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            if exc.code in (429, 418):
                raise MarketDataError(f"HTTP {exc.code} rate limited: {body}", rate_limited=True) from exc
            if exc.code in (400, 404, 422):
                raise SymbolUnavailableError(f"HTTP {exc.code}: {body}") from exc
            if exc.code == 451:
                raise MarketDataError(f"HTTP 451 geo-restricted host={url.split('//')[1].split('/')[0]}") from exc
            raise MarketDataError(f"HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MarketDataError(f"network error: {exc}") from exc
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MarketDataError(f"malformed JSON: {exc}") from exc
        if isinstance(data, dict) and data.get("code") not in (None, 0, 200):
            if data.get("code") == -1121:
                raise SymbolUnavailableError(str(data.get("msg", "invalid symbol")))
            raise MarketDataError(f"api error {data.get('code')}: {data.get('msg')}")
        return data

    # --------------------------------------------------------------- api surface
    #
    # Every method below returns the BINANCE wire shape, because that is what
    # the rest of the engine parses. Endpoints whose ``dialect`` is not
    # "binance" are translated by the adapter layer at the bottom of this
    # module, so a non-Binance venue is a drop-in failover rather than a
    # rewrite of the pipeline.
    def _dialect(self) -> str:
        return self.endpoints[self._endpoint_idx].get("dialect", "binance")

    def exchange_info(self) -> dict:
        def binance():
            data = self._get("/exchangeInfo")
            # A response without a symbol list is unusable; treat it as an
            # endpoint failure so the chain fails over instead of returning a
            # shape the universe builder would silently read as "no symbols".
            if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
                raise MarketDataError("exchangeInfo returned unexpected shape")
            return data
        return self._by_dialect({"binance": binance,
                                 "bybit": lambda: bybit_exchange_info(self)})

    def _probe_dialect(self) -> str:
        """Dialect of the endpoint currently selected."""
        return self._dialect()

    def _by_dialect(self, handlers: dict):
        """Run the request builder that matches the serving endpoint's dialect.

        Request *shape* must be chosen before the request is sent, but failover
        happens inside ``_get`` — so a Binance-shaped call can never fail over
        to a Bybit host on its own. This walks the endpoint chain at the
        dialect level: it tries the current endpoint's dialect, and if every
        host of that dialect fails, it pins the next dialect in the chain and
        retries with the correct shape.
        """
        errors: list[str] = []
        tried: set[str] = set()
        start = self._endpoint_idx
        for hop in range(len(self.endpoints)):
            idx = (start + hop) % len(self.endpoints)
            dialect = self.endpoints[idx].get("dialect", "binance")
            if dialect in tried:
                continue
            tried.add(dialect)
            handler = handlers.get(dialect)
            if handler is None:
                errors.append(f"{dialect}: unsupported dialect")
                continue
            self._endpoint_idx = idx  # pin so _get starts on this dialect
            try:
                return handler()
            except SymbolUnavailableError:
                raise  # deterministic: another venue would not help
            except MarketDataError as exc:
                errors.append(f"{dialect}: {exc}")
        raise MarketDataError("all endpoints failed: " + " | ".join(errors))

    def ticker_24h(self) -> list:
        def binance():
            data = self._get("/ticker/24hr")
            if not isinstance(data, list):
                raise MarketDataError("ticker/24hr returned unexpected shape")
            return data
        return self._by_dialect({"binance": binance,
                                 "bybit": lambda: bybit_ticker_24h(self)})

    def klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        if interval not in INTERVAL_MS:
            raise ValueError(f"unsupported interval {interval}")
        def binance():
            rows = self._get("/klines", {"symbol": symbol, "interval": interval,
                                         "limit": limit})
            if not isinstance(rows, list):
                raise MarketDataError("klines returned unexpected shape")
            return parse_klines(rows)
        return self._by_dialect({
            "binance": binance,
            "bybit": lambda: bybit_klines(self, symbol, interval, limit=limit)})

    def klines_since(self, symbol: str, interval: str, start_ms: int) -> list[Candle]:
        """Fetch candles from start_ms onward (used by the outcome engine)."""
        def binance():
            rows = self._get("/klines", {"symbol": symbol, "interval": interval,
                                         "startTime": start_ms, "limit": 1000})
            if not isinstance(rows, list):
                raise MarketDataError("klines returned unexpected shape")
            return parse_klines(rows)
        return self._by_dialect({
            "binance": binance,
            "bybit": lambda: bybit_klines(self, symbol, interval, start_ms=start_ms)})

    def premium_index(self) -> list:
        """Funding/mark info for ALL symbols in one call (futures only)."""
        def binance():
            data = self._get("/premiumIndex")
            if not isinstance(data, list):
                raise MarketDataError("premiumIndex returned unexpected shape")
            return data
        return self._by_dialect({"binance": binance,
                                 "bybit": lambda: bybit_premium_index(self)})

    def open_interest(self, symbol: str) -> dict:
        """Current open interest for one symbol (futures only)."""
        def binance():
            data = self._get("/openInterest", {"symbol": symbol})
            if not isinstance(data, dict):
                raise MarketDataError("openInterest returned unexpected shape")
            return data
        return self._by_dialect({"binance": binance,
                                 "bybit": lambda: bybit_open_interest(self, symbol)})

    def endpoint_info(self) -> dict:
        ep = self.endpoints[self._endpoint_idx]
        return {"name": ep.get("name"), "market": ep.get("market"), "base": ep.get("base")}


# --------------------------------------------------------------------------
# Bybit v5 adapter — genuine cross-exchange redundancy.
#
# Why this exists: the configured "failover chain" was three Binance-owned
# hostnames. Binance answers HTTP 451 to whole cloud ranges (the README admits
# this), and when it does, all three hops fail for the same reason at the same
# moment — that is one source with three spellings, not redundancy.
#
# Bybit is a separate company, separate infrastructure, separate geo policy,
# and its USDT perpetuals cover essentially the same liquid universe. These
# functions translate Bybit's v5 responses into the Binance shapes the engine
# already parses, so nothing downstream needs to know which venue answered.
#
# Bybit v5 reference: GET /v5/market/{instruments-info,tickers,kline,open-interest}
# with category=linear.

# Binance interval -> Bybit kline interval code
_BYBIT_INTERVAL = {"1m": "1", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}


def _bybit_result(payload, what: str) -> dict:
    """Unwrap Bybit's {retCode, retMsg, result} envelope."""
    if not isinstance(payload, dict):
        raise MarketDataError(f"bybit {what}: unexpected shape")
    if payload.get("retCode") not in (0, None):
        raise MarketDataError(f"bybit {what}: {payload.get('retCode')} {payload.get('retMsg')}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise MarketDataError(f"bybit {what}: missing result")
    return result


def bybit_exchange_info(client: "MarketDataClient") -> dict:
    """Bybit instruments-info -> Binance exchangeInfo shape."""
    result = _bybit_result(
        client._get("/v5/market/instruments-info", {"category": "linear", "limit": 1000}),
        "instruments-info")
    symbols = []
    for row in result.get("list", []) or []:
        # Bybit: status "Trading"; contractType "LinearPerpetual"
        if row.get("status") != "Trading":
            continue
        if row.get("contractType") != "LinearPerpetual":
            continue
        launch = row.get("launchTime")
        try:
            onboard = int(launch) if launch not in (None, "", "0") else None
        except (TypeError, ValueError):
            onboard = None
        symbols.append({
            "symbol": row.get("symbol"),
            "status": "TRADING",
            "baseAsset": row.get("baseCoin"),
            "quoteAsset": row.get("quoteCoin"),
            "contractType": "PERPETUAL",
            "onboardDate": onboard,
        })
    return {"symbols": symbols}


def bybit_ticker_24h(client: "MarketDataClient") -> list:
    """Bybit tickers -> Binance /ticker/24hr shape (quoteVolume is what we rank on)."""
    result = _bybit_result(
        client._get("/v5/market/tickers", {"category": "linear"}), "tickers")
    out = []
    for row in result.get("list", []) or []:
        # Bybit reports 24h change as a ratio ("0.0123"); Binance uses percent.
        try:
            pct = float(row.get("price24hPcnt") or 0.0) * 100.0
        except (TypeError, ValueError):
            pct = 0.0
        out.append({
            "symbol": row.get("symbol"),
            # Bybit turnover24h is quote-denominated volume == Binance quoteVolume
            "quoteVolume": row.get("turnover24h", "0"),
            "volume": row.get("volume24h", "0"),
            "lastPrice": row.get("lastPrice", "0"),
            "priceChangePercent": f"{pct:.4f}",
            # Bybit folds funding and OI into the ticker; Binance serves them
            # from /premiumIndex and /openInterest. Carrying them here lets the
            # premium_index/open_interest adapters reuse a single request.
            "lastFundingRate": row.get("fundingRate", "0") or "0",
            "openInterest": row.get("openInterest", "0") or "0",
            "markPrice": row.get("markPrice", row.get("lastPrice", "0")),
        })
    return out


def bybit_klines(client: "MarketDataClient", symbol: str, interval: str,
                 limit: int = 1000, start_ms: int | None = None) -> list[Candle]:
    """Bybit kline -> list[Candle].

    Bybit returns newest-first arrays of
    [startTime, open, high, low, close, volume, turnover] and, unlike Binance,
    gives no closeTime — it is derived from the interval, which is exact for
    every fixed-width interval the engine uses.
    """
    code = _BYBIT_INTERVAL.get(interval)
    if code is None:
        raise ValueError(f"unsupported interval {interval}")
    params = {"category": "linear", "symbol": symbol, "interval": code,
              "limit": min(int(limit) or 200, 1000)}
    if start_ms is not None:
        params["start"] = int(start_ms)
    result = _bybit_result(client._get("/v5/market/kline", params), "kline")
    span = INTERVAL_MS[interval]
    rows = []
    for row in result.get("list", []) or []:
        try:
            open_time = int(row[0])
        except (TypeError, ValueError, IndexError):
            continue
        # Rebuild the Binance row layout parse_klines() expects.
        rows.append([open_time, row[1], row[2], row[3], row[4], row[5],
                     open_time + span - 1, row[6] if len(row) > 6 else "0", 0])
    return parse_klines(rows)  # parse_klines sorts oldest-first


def bybit_premium_index(client: "MarketDataClient") -> list:
    """Bybit tickers -> Binance /premiumIndex shape (funding rate per symbol)."""
    result = _bybit_result(
        client._get("/v5/market/tickers", {"category": "linear"}), "tickers")
    out = []
    for row in result.get("list", []) or []:
        out.append({"symbol": row.get("symbol"),
                    "lastFundingRate": row.get("fundingRate", "0")})
    return out


def bybit_open_interest(client: "MarketDataClient", symbol: str) -> dict:
    """Bybit tickers carry openInterest directly; one call, one symbol."""
    result = _bybit_result(
        client._get("/v5/market/tickers", {"category": "linear", "symbol": symbol}),
        "tickers")
    rows = result.get("list", []) or []
    if not rows:
        raise SymbolUnavailableError(f"bybit: no ticker for {symbol}")
    return {"symbol": symbol, "openInterest": rows[0].get("openInterest", "0")}
