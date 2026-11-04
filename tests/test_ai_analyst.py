"""Analyste IA (shadow): request building, strict parsing, scoring and run cadence."""
import json

import pytest

from scanner import ai_analyst, persist
from scanner.config import Config

H4 = ai_analyst.H4_MS
T0 = 1_699_920_000_000
BAR = T0 + 50 * H4  # close of the last candle given to the model


def candles(n=60, start=100.0, step=1.0):
    return [[T0 + i * H4, start + i * step, start + i * step + 1, start + i * step - 1, start + i * step]
            for i in range(n)]


@pytest.fixture()
def trend():
    return {
        "params": {"symbols": ["AAAUSDT"], "trendSymbol": "AAAUSDT", "stopAtr": 2.0},
        "watch": {"AAAUSDT": {"barClose": BAR, "candles": candles(), "channelHigh": 150, "channelLow": 130,
                              "relVolume": 2.5, "fundingRate": 0.0001, "stopDistance": 4.0,
                              "trendClose": 149, "trendEma": 120}},
        "evaluations": [{"symbol": "AAAUSDT", "barClose": BAR, "side": "LONG", "close": 149,
                         "channelLevel": 148, "relVolume": 2.5, "taken": False, "reasons": ["volume"]}],
    }


def answer(view, grounding=None):
    return {"candidates": [{"content": {"parts": [{"text": "Analyse :\n```json\n" + json.dumps(view) + "\n```"}]},
                            "groundingMetadata": grounding or {}}],
            "usageMetadata": {"totalTokenCount": 1234}, "modelVersion": "gemini-test"}


GOOD = {"macro": "Fed calme", "assets": {"AAAUSDT": {
    "direction24h": "UP", "probUp24h": 64, "reason": "ETF en hausse", "risks": "CPI demain",
    "breakout": {"decision": "GO", "probTarget": 41, "reason": "volume et news"},
    "trade": {"action": "LONG", "conviction": 70, "stop": 146, "target": 160, "maxHoldHours": 48, "reason": "flux ETF"}}}}


def test_facts_carry_our_numbers_but_not_the_filter_verdict(trend):
    facts = ai_analyst.build_facts(trend, BAR)
    a = facts["assets"]["AAAUSDT"]
    assert a["close"] == 149 and a["change24hPct"] == pytest.approx(100 * (149 / 143 - 1), abs=0.01)
    assert a["atrPct"] == pytest.approx(100 * 2.0 / 149, abs=0.01)
    assert a["breakout"] == {"side": "LONG", "close": 149, "brokenLevel": 148, "relVolume": 2.5}
    text = json.dumps(facts)
    assert "taken" not in text and "reasons" not in text and "volume\"" not in text


def test_parse_keeps_only_valid_views_and_clamps(trend):
    facts = ai_analyst.build_facts(trend, BAR)
    hostile = {"assets": {"AAAUSDT": {"direction24h": "UP", "probUp24h": 250, "reason": "x" * 999,
                                      "breakout": {"decision": "GO", "probTarget": -5}},
                          "EVILUSDT": {"direction24h": "UP", "probUp24h": 50}}}
    view = ai_analyst.parse_view(json.dumps(hostile), facts)
    v = view["assets"]["AAAUSDT"]
    assert set(view["assets"]) == {"AAAUSDT"} and v["probUp"] == 100 and len(v["reason"]) == 400
    assert v["breakout"]["probTarget"] == 0 and v["breakout"]["side"] == "LONG"
    with pytest.raises(ai_analyst.AnalystError):
        ai_analyst.parse_view('{"assets": {"AAAUSDT": {"direction24h": "MOON"}}}', facts)
    with pytest.raises(ai_analyst.AnalystError):
        ai_analyst.parse_view("pas de JSON", facts)


@pytest.mark.parametrize("project, location, model", [
    ("opti-trade-513710/../x", "global", "gemini-3.5-flash"),
    ("opti-trade-513710", "global", "gemini?key=1"),
    ("opti-trade-513710", "evil.com/", "gemini-3.5-flash"),
])
def test_endpoint_rejects_injected_identifiers(project, location, model):
    with pytest.raises(ai_analyst.AnalystError):
        ai_analyst.endpoint(project, location, model)


def test_endpoint_uses_global_host():
    url = ai_analyst.endpoint("opti-trade-513710", "global", "gemini-3.5-flash")
    assert url.startswith("https://aiplatform.googleapis.com/v1/projects/opti-trade-513710/locations/global/")


def test_scoring_direction_and_breakout_matrix(trend):
    ai = {"analyses": [{"barClose": BAR, "analyzedAt": BAR, "horizonHours": 24, "assets": {
        "AAAUSDT": {"direction": "UP", "probUp": 70, "price0": 149,
                    "breakout": {"decision": "GO", "probTarget": 40}}}}]}
    trend["watch"]["AAAUSDT"]["candles"] = candles(n=57)  # candle closing BAR + 24h has close 155
    trend["evaluations"][0]["shadow"] = {"status": "closed", "rNet": 2.5, "exitReason": "TRAIL"}
    ai_analyst.score(ai, trend, BAR + 25 * 3_600_000)
    v = ai["analyses"][0]["assets"]["AAAUSDT"]
    assert v["outcome"]["hit"] is True and v["outcome"]["retPct"] == pytest.approx(100 * (155 / 149 - 1), abs=1e-3)
    assert v["breakout"]["ours"] == "rejected" and v["breakout"]["result"]["rNet"] == 2.5
    s = ai_analyst.stats(ai)
    assert s["direction"]["hitRate"] == 1.0 and s["direction"]["brier"] == pytest.approx(0.09)
    assert s["breakouts"]["rejected_GO"] == {"count": 1, "closed": 1, "wins": 1, "avgR": 2.5, "totalR": 2.5}
    assert s["breakouts"]["taken_NO_GO"]["count"] == 0


@pytest.fixture()
def stores(tmp_path, monkeypatch, trend):
    monkeypatch.setattr(persist, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(persist, "frontend_data_dir", lambda: tmp_path / "front")
    persist.write_data_file("trend4h-paper.json", trend)
    return tmp_path


ENV = {"GCP_PROJECT": "opti-trade-513710", "GCP_ACCESS_TOKEN": "token"}
CFG = Config({"aiAnalyst": {"enabled": True}})


def test_run_calls_the_model_once_per_close_and_records_sources(stores):
    calls = []

    def post(url, token, body, timeout):
        calls.append(body)
        assert body["tools"] == [{"googleSearch": {}}] and token == "token"
        return answer(GOOD, {"webSearchQueries": ["fed today"], "groundingChunks": [
            {"web": {"uri": "https://example.org/a", "title": "A"}}, {"web": {"uri": "javascript:alert(1)"}}]})

    first = ai_analyst.run(CFG, BAR + 60_000, log=lambda *_: None, post=post, env=ENV)
    again = ai_analyst.run(CFG, BAR + 120_000, log=lambda *_: None, post=post, env=ENV)
    assert len(calls) == 1 and again["lastRun"]["status"] == "up-to-date"
    (a,) = first["analyses"]
    assert a["assets"]["AAAUSDT"]["breakout"]["decision"] == "GO"
    assert a["queries"] == ["fed today"] and a["sources"] == [{"title": "A", "uri": "https://example.org/a"}]
    assert "taken" not in calls[0]["contents"][0]["parts"][0]["text"]
    assert (stores / "front" / ai_analyst.FILE).read_bytes() == (stores / "data" / ai_analyst.FILE).read_bytes()


def test_run_without_credentials_or_too_late_never_calls(stores):
    def post(*_):
        raise AssertionError("must not call the model")

    st = ai_analyst.run(CFG, BAR + 60_000, log=lambda *_: None, post=post, env={})
    assert st["lastRun"]["status"] == "not-configured"
    st = ai_analyst.run(CFG, BAR + 3 * 3_600_000, log=lambda *_: None, post=post, env=ENV)
    assert st["lastRun"]["status"] == "skipped-late" and st["analyses"] == []


def test_run_gives_up_after_repeated_failures(stores):
    def post(*_):
        raise ai_analyst.AnalystError("HTTP 500")

    for i in range(4):
        st = ai_analyst.run(CFG, BAR + (i + 1) * 60_000, log=lambda *_: None, post=post, env=ENV)
    assert st["lastRun"]["status"] == "gave-up" and st["attempts"] == {str(BAR): 3}


@pytest.mark.parametrize("trade, valid, status", [
    ({"action": "LONG", "stop": 146, "target": 160}, True, None),               # 1.5 ATR below the close
    ({"action": "LONG", "stop": 148.5, "target": 160}, False, "invalid-stop"),  # 0.25 ATR: too tight
    ({"action": "LONG", "stop": 152, "target": 160}, False, "invalid-stop"),    # wrong side
    ({"action": "SHORT", "stop": 152, "target": 155}, False, "invalid-target"),
    ({"action": "SHORT", "stop": float("nan"), "target": None}, False, "invalid-stop"),
])
def test_ai_trade_levels_are_validated(trend, trade, valid, status):
    fact = ai_analyst.build_facts(trend, BAR)["assets"]["AAAUSDT"]
    assert fact["atr14"] == pytest.approx(2.0)
    view = ai_analyst._trade_view(dict(trade, conviction=60, maxHoldHours=999), fact)
    assert view["valid"] is valid and view.get("status") == status and view["maxHoldHours"] == 168


def ai_with_trade(trade, bar=BAR):
    return {"analyses": [{"barClose": bar, "analyzedAt": bar, "horizonHours": 24, "assets": {"AAAUSDT": {
        "direction": "UP", "probUp": 60, "price0": 149,
        "trade": dict({"action": "LONG", "conviction": 70, "reason": "x", "valid": True}, **trade)}}}]}


def test_ai_portfolio_opens_at_next_open_and_hits_target(trend):
    trend["params"].update(startCapital=500, riskPct=1.0, takerFeePct=0.05, slippagePct=0.025)
    trend["watch"]["AAAUSDT"]["candles"] = candles(n=56)  # candle 54 reaches 155
    ai = ai_with_trade({"stop": 146, "target": 155, "maxHoldHours": 72})
    now = T0 + 56 * H4
    ai_analyst.simulate(ai, trend, now)
    assert ai["analyses"][0]["assets"]["AAAUSDT"]["trade"]["status"] == "opened"
    ai_analyst.simulate(ai, trend, now)
    pf = ai["portfolio"]
    (trade,) = pf["trades"]
    assert (trade["entryPrice"], trade["exitReason"], trade["exitPrice"]) == (150, "TARGET", 155)
    assert trade["rNet"] == pytest.approx(5 / 4 - 0.0015 * 150 / 4, abs=1e-4)
    assert pf["equity"] == pytest.approx(500 + 5 * trade["rNet"], abs=1e-3) and pf["positions"] == []
    assert ai_analyst._portfolio_stats(pf)["wins"] == 1


def test_ai_portfolio_time_exit_and_one_position_per_asset(trend):
    trend["watch"]["AAAUSDT"]["candles"] = candles(n=53)
    ai = ai_with_trade({"stop": 146, "target": None, "maxHoldHours": 8})
    ai["analyses"].append(ai_with_trade({"stop": 147, "target": None, "maxHoldHours": 8}, BAR + H4)["analyses"][0])
    ai_analyst.simulate(ai, trend, BAR + 60_000)
    assert ai["analyses"][1]["assets"]["AAAUSDT"]["trade"]["status"] == "skipped-open"
    ai_analyst.simulate(ai, trend, T0 + 53 * H4)
    (trade,) = ai["portfolio"]["trades"]
    assert (trade["exitReason"], trade["exitPrice"]) == ("TIME", 151)


def test_ai_config_is_validated():
    cfg = Config({"aiAnalyst": {"enabled": True, "model": "x/../y", "horizonHours": 5}})
    errors = cfg.validate()
    assert any("aiAnalyst.model" in e for e in errors) and any("horizonHours" in e for e in errors)
