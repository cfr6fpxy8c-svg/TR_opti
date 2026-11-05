"""Centralised configuration loader.

Loads /config/strategy.json, deep-merges it over safe built-in defaults so a
partially specified file never crashes the engine, and exposes dotted-path
lookups. All strategy parameters live here -- never hard-coded in the engine.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .network_security import BASE_URLS

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "scan": {"intervalMinutes": 15, "scheduleMinutesUtc": [0, 15, 30, 45],
             "timezone": "UTC"},
    "dataSource": {
        "failoverEndpoints": [
            {"name": "binance-futures", "market": "futures", "base": "https://fapi.binance.com/fapi/v1"},
        ],
        "requestTimeoutSeconds": 12,
        "maxRetries": 3,
        "retryBackoffSeconds": 1.5,
        "maxRequestsPerScan": 700,
        "klineLimits": {"4h": 260, "1h": 300, "15m": 500},
    },
    "universe": {
        "maxSymbols": 100,
        "minQuoteVolume24h": 3_000_000,
        "excludeSymbolPatterns": ["UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"],
        "excludeBaseAssets": ["USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "AEUR", "EUR", "PAXG"],
        "minListingAgeDays": 30,
    },
    "indicators": {
        "emaFast": 20, "emaMid": 50, "emaSlow": 200,
        "rsiPeriod": 14, "adxPeriod": 14, "atrPeriod": 14,
        "relVolumeLookback": 20, "vwapWindow": 48,
    },
    "structure": {
        "swingLookback": 2, "minSwingAgeBars": 3, "sweepMaxAtrMultiple": 1.0,
        "displacementBodyAtrMultiple": 1.5, "equalLevelAtrTolerance": 0.10,
        "maxStructureSwings": 40,
    },
    "smc": {"fvgMinGapAtrMultiple": 0.10, "obLookback": 10, "setupWindowBars": 12},
    "signalModel": {
        "mode": "strict",
        "rsiLongMin": 50, "rsiLongMax": 72, "rsiShortMin": 28, "rsiShortMax": 50,
        "minAdx15m": 18, "minRelVolume": 1.20, "minAtrPercent": 0.10, "maxAtrPercent": 3.00,
        # ADX does not mean the same thing on every timeframe; higher
        # timeframes trend more smoothly and read lower for the same
        # conviction. Falls back to minAdx15m when a key is absent.
        "minAdxByTimeframe": {"15m": None, "1h": 18, "4h": 20, "1d": 20},
    },
    "risk": {
        "minRr": 2.5, "preferredRr": 3.0,
        "stopBufferAtrMultiple": 0.5, "triggerBufferAtrMultiple": 0.10,
        "entryZoneAtrMultiple": 0.5, "minStopAtrMultiple": 0.15, "maxStopAtrMultiple": 3.0,
        "maxRunupAtrMultiple": 3.0, "minStructureRoomAtrMultiple": 1.0,
    },
    "costs": {
        "enabled": True, "entryFeePct": 0.05, "exitFeePct": 0.05,
        "useFunding": True, "fundingIntervalMs": 28_800_000, "maxCostR": 0.25,
    },
    "scoring": {
        "minScore": 80, "aPlusThreshold": 90, "aThreshold": 85, "bPlusThreshold": 80,
        "weights": {
            "htfAlignment": 20, "marketStructure": 20, "liquiditySmc": 20,
            "momentum": 15, "volume": 10, "volatility": 5, "riskReward": 10,
        },
    },
    "lifecycle": {"triggerExpiryCandles": 12, "tradeExpiryCandles": 16, "resolveAmbiguousWith1m": True, "candleMs": 900_000},
    "dedupe": {"symbolCooldownMinutes": 240, "sameDirectionOnly": True, "maxActiveSignalsTotal": 12},
    "weighted": {
        "minScore": 50, "bThreshold": 70, "minRr": 1.5, "fallbackRr": 2.0,
        "cooldownMinutes": 60, "contextWeight": 10, "standby": [],
    },
    "performance": {"monteCarloBlockLength": None},
    "retention": {"marketSnapshots": 288, "logEntries": 200},
    "trend4h": {
        "enabled": False, "symbols": ["BTCUSDT"], "trendSymbol": "BTCUSDT",
        "channelBars": 20, "trailBars": 10, "atrPeriod": 14, "stopAtr": 2.0,
        "trendEma": 200, "volumeLookback": 20, "volumeMultiple": 2.0, "maxFundingRate": 0.0003,
        "riskPct": 1.0, "maxPositions": 5, "maxLeverage": 5.0, "startCapital": 500.0,
        "takerFeePct": 0.05, "slippagePct": 0.025, "klineLimit": 1000,
    },
    "aiAnalyst": {
        "enabled": False, "model": "gemini-3.5-flash", "location": "global", "horizonHours": 24,
        "maxDelayMinutes": 90, "maxAttemptsPerBar": 3, "timeoutSeconds": 150, "maxOutputTokens": 8192,
    },
    "trendCombo": {
        "enabled": False, "aiEnabled": False, "startCapital": 500.0, "risk4hPct": 1.0, "risk1hPct": 0.5,
        "h1ChannelBars": 160, "h1TrailBars": 40, "h1StopAtr": 2.0, "h1KlineLimit": 1000,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    """Read-only accessor over the merged strategy configuration."""

    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self._data = _deep_merge(DEFAULTS, data or {})
        self.path = path

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path) if path else repo_root() / "config" / "strategy.json"
        data: dict[str, Any] = {}
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
        return cls(data, p)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted)
        if value is None:
            raise KeyError(f"missing config key: {dotted}")
        return value

    @property
    def raw(self) -> dict[str, Any]:
        return self._data

    def validate(self) -> list[str]:
        errors: list[str] = []
        s = self.get("scoring", {})
        if not (0 < s.get("minScore", 0) <= 100):
            errors.append("scoring.minScore must be within 1..100")
        if not (s.get("bPlusThreshold", 0) <= s.get("aThreshold", 0) <= s.get("aPlusThreshold", 0)):
            errors.append("scoring thresholds must satisfy bPlus <= A <= A+")
        risk = self.get("risk", {})
        if risk.get("minRr", 0) <= 0 or risk.get("preferredRr", 0) < risk.get("minRr", 0):
            errors.append("risk.minRr/preferredRr invalid")
        if self.get("signalModel.mode", "strict") not in ("strict", "weighted"):
            errors.append("signalModel.mode must be 'strict' or 'weighted'")
        w = self.get("weighted", {})
        if not (0 < w.get("minScore", 0) <= w.get("bThreshold", 0) <= s.get("bPlusThreshold", 0)):
            errors.append("weighted thresholds must satisfy 0 < minScore <= bThreshold <= bPlus")
        if not (0 < w.get("minRr", 0) <= w.get("fallbackRr", 0)):
            errors.append("weighted.minRr/fallbackRr invalid")
        from .scoring import METRIC_MAX
        unknown = [m for m in w.get("standby", []) if m not in METRIC_MAX]
        if unknown:
            errors.append(f"weighted.standby has unknown metrics: {unknown}")
        min_stop = risk.get("minStopAtrMultiple", 0)
        if min_stop <= 0 or min_stop > risk.get("maxStopAtrMultiple", 0):
            errors.append("risk.minStopAtrMultiple must be > 0 and <= maxStopAtrMultiple")
        costs = self.get("costs", {})
        if costs.get("enabled", True):
            if costs.get("entryFeePct", -1) < 0 or costs.get("exitFeePct", -1) < 0:
                errors.append("costs.entryFeePct/exitFeePct must be >= 0")
            if costs.get("maxCostR", 0) <= 0:
                errors.append("costs.maxCostR must be > 0")
            if costs.get("fundingIntervalMs", 0) <= 0:
                errors.append("costs.fundingIntervalMs must be > 0")
        endpoints = self.get("dataSource.failoverEndpoints", [])
        if not endpoints:
            errors.append("dataSource.failoverEndpoints must not be empty")
        if not isinstance(endpoints, list):
            errors.append("dataSource.failoverEndpoints must be a list")
        else:
            for ep in endpoints:
                if (not isinstance(ep, dict) or not isinstance(ep.get("base"), str)
                        or ep["base"].rstrip("/") not in BASE_URLS):
                    errors.append("dataSource endpoint is not an approved public HTTPS API")
                elif ep.get("root") not in (None, "https://api.bybit.com"):
                    errors.append("dataSource endpoint root is not allowed")
        errors.extend(self._validate_trend4h())
        errors.extend(self._validate_ai_analyst())
        errors.extend(self._validate_trend_combo())
        return errors

    def _validate_trend_combo(self) -> list[str]:
        c = self.get("trendCombo", {})
        if not c.get("enabled", False):
            return []
        errors = []
        if not self.get("trend4h.symbols"):
            errors.append("trendCombo reuses trend4h.symbols and filters, which must be configured")
        for key in ("startCapital", "risk4hPct", "risk1hPct", "h1StopAtr"):
            if not isinstance(c.get(key), (int, float)) or c[key] <= 0:
                errors.append(f"trendCombo.{key} must be > 0")
        for key in ("risk4hPct", "risk1hPct"):
            if isinstance(c.get(key), (int, float)) and c[key] > 5:
                errors.append(f"trendCombo.{key} must not exceed 5")
        for key in ("h1ChannelBars", "h1TrailBars"):
            if not isinstance(c.get(key), int) or c[key] < 1:
                errors.append(f"trendCombo.{key} must be a positive integer")
        limit = c.get("h1KlineLimit")
        if not isinstance(limit, int) or not (c.get("h1ChannelBars") or 0) + 50 <= limit <= 1500:
            errors.append("trendCombo.h1KlineLimit must cover h1ChannelBars + 50 candles and be <= 1500")
        return errors

    def _validate_ai_analyst(self) -> list[str]:
        a = self.get("aiAnalyst", {})
        if not a.get("enabled", False):
            return []
        from .ai_analyst import LOCATION_RE, MODEL_RE
        errors = []
        if not isinstance(a.get("model"), str) or not MODEL_RE.match(a["model"]):
            errors.append("aiAnalyst.model must be a model id like gemini-3.5-flash")
        if not isinstance(a.get("location"), str) or not LOCATION_RE.match(a["location"]):
            errors.append("aiAnalyst.location must be 'global' or a region like europe-west1")
        h = a.get("horizonHours")
        if not isinstance(h, int) or h % 4 or not 4 <= h <= 168:
            errors.append("aiAnalyst.horizonHours must be a multiple of 4 within 4..168")
        for key, low, high in (("maxDelayMinutes", 5, 240), ("maxAttemptsPerBar", 1, 10),
                               ("timeoutSeconds", 10, 300), ("maxOutputTokens", 512, 65536)):
            if not isinstance(a.get(key), int) or not low <= a[key] <= high:
                errors.append(f"aiAnalyst.{key} must be an integer within {low}..{high}")
        return errors

    def _validate_trend4h(self) -> list[str]:
        t = self.get("trend4h", {})
        if not t.get("enabled", False):
            return []
        errors = []
        symbols = t.get("symbols")
        if (not isinstance(symbols, list) or not symbols
                or not all(isinstance(x, str) and x.isalnum() and x.isupper() for x in symbols)):
            errors.append("trend4h.symbols must be a non-empty list of symbols like BTCUSDT")
        trend = t.get("trendSymbol")
        if not (isinstance(trend, str) and trend.isalnum() and trend.isupper()):
            errors.append("trend4h.trendSymbol must be a symbol like BTCUSDT")
        positive_ints = ("channelBars", "trailBars", "atrPeriod", "trendEma", "volumeLookback", "maxPositions")
        for key in positive_ints:
            if not isinstance(t.get(key), int) or t[key] < 1:
                errors.append(f"trend4h.{key} must be a positive integer")
        for key in ("stopAtr", "volumeMultiple", "riskPct", "maxLeverage", "startCapital"):
            if not isinstance(t.get(key), (int, float)) or t[key] <= 0:
                errors.append(f"trend4h.{key} must be > 0")
        if isinstance(t.get("riskPct"), (int, float)) and t["riskPct"] > 5:
            errors.append("trend4h.riskPct must not exceed 5")
        for key in ("maxFundingRate", "takerFeePct", "slippagePct"):
            if not isinstance(t.get(key), (int, float)) or t[key] < 0:
                errors.append(f"trend4h.{key} must be >= 0")
        limit = t.get("klineLimit")
        need = max(v for v in (t.get("trendEma"), t.get("channelBars"), 0) if isinstance(v, int)) + 50
        if not isinstance(limit, int) or not need <= limit <= 1500:
            errors.append("trend4h.klineLimit must cover trendEma + 50 candles and be <= 1500")
        return errors


def repo_root() -> Path:
    """Repository root = parent of the ``scanner`` package directory."""
    return Path(__file__).resolve().parents[1]
