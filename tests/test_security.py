"""Regression checks for the trust boundaries hardened during the audit."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
from unittest.mock import patch

import pytest

from scanner.config import Config, repo_root
from scanner.html_security import authorize_inline_scripts, inline_script
from scanner.market_data import MarketDataClient, MarketDataError
from scanner.network_security import NoRedirects, validate_market_url
from scanner import persist
from scanner.validation import validate_signal, validate_signals_payload
from conftest import make_signal


@pytest.mark.parametrize("url", [
    "http://fapi.binance.com/fapi/v1/klines", "file:///etc/passwd",
    "https://127.0.0.1/fapi/v1/klines", "https://169.254.169.254/latest/meta-data/",
    "https://fapi.binance.com.evil.example/fapi/v1/klines",
    "https://user:password@fapi.binance.com/fapi/v1/klines",
    "https://fapi.binance.com:8443/fapi/v1/klines",
    "https://fapi.binance.com/fapi/v1/order", "https://fapi.binance.com/fapi/v1/klines#x",
])
def test_unapproved_requests_never_reach_network(url):
    client = MarketDataClient(Config.load().get("dataSource.failoverEndpoints"))
    with patch("urllib.request.build_opener") as transport:
        with pytest.raises(MarketDataError, match="blocked"):
            client._fetch(url)
    transport.assert_not_called()


@pytest.mark.parametrize("url", [
    "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT",
    "https://www.binance.com/fapi/v1/premiumIndex",
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.bybit.com/v5/market/kline",
])
def test_public_market_routes_allowed(url):
    validate_market_url(url)


def test_redirect_does_not_follow_to_internal_address():
    assert NoRedirects().redirect_request(None, None, 302, "", {}, "http://127.0.0.1/") is None


def test_config_rejects_unapproved_api_host():
    cfg = Config({"dataSource": {"failoverEndpoints": [{"base": "http://localhost"}]}})
    assert any("approved" in msg for msg in cfg.validate())


@pytest.mark.parametrize("name", ["../.git/config", "/tmp/secret", "unexpected.json"])
def test_data_paths_cannot_escape_directory(name):
    with pytest.raises(ValueError):
        persist.write_data_file(name, {})


def test_nonfinite_write_preserves_previous_data(tmp_path):
    dest = tmp_path / "data.json"
    dest.write_text('{"old":true}', encoding="utf-8")
    with pytest.raises(ValueError):
        persist.atomic_write_json(dest, {"bad": float("nan")})
    assert json.loads(dest.read_text()) == {"old": True}


def test_corrupt_history_is_not_reset_to_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(persist, "data_dir", lambda: tmp_path)
    path = tmp_path / "signals.json"
    path.write_text('{"signals": [broken')
    with pytest.raises(ValueError, match="refusing to reset"):
        persist.load_json("signals.json", {"signals": []})
    assert path.read_text() == '{"signals": [broken'


def test_inline_scripts_cannot_close_their_html_element():
    assert "</script" not in inline_script('const x="</ScRiPt><img src=x>";').lower()


def test_script_csp_hash_matches_actual_bytes():
    source = "\nconst slash = /[\\n]/;\n"
    html = '<meta content="script-src \'self\';"><script>' + source + "</script>"
    secured = authorize_inline_scripts(html)
    digest = base64.b64encode(hashlib.sha256(source.encode()).digest()).decode()
    assert "'sha256-" + digest + "'" in secured
    assert "unsafe-inline" not in secured


def test_preview_preserves_javascript_escapes(tmp_path):
    from scanner.build_preview import build
    (tmp_path / "index.html").write_text('<meta content="script-src \'self\';"><script src="app.js"></script>')
    (tmp_path / "styles.css").write_text("")
    js = 'const newline = /[\\n]/; const string = "\\\\";'
    (tmp_path / "app.js").write_text(js)
    build(tmp_path)
    result = (tmp_path / "preview.html").read_text()
    assert js in result
    assert "sha256-" in result


@pytest.mark.parametrize("value", [None, [], "invalid", {"signals": [None]}, {"signals": [{"id": []}]}])
def test_malformed_signal_payload_is_rejected_without_crashing(value):
    assert validate_signals_payload(value, Config.load(), 0)


def test_html_symbol_and_invalid_numeric_values_rejected():
    errors = validate_signal(make_signal(symbol='<img src=x>USDT', riskReward="2.5"), Config.load())
    assert "symbol invalid" in errors
    assert "non-finite prices/score" in errors


def test_publisher_blocks_artifact_code_and_extra_files(tmp_path):
    spec = importlib.util.spec_from_file_location("publisher", repo_root() / ".github/scripts/publish_data.py")
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    (tmp_path / "scanner.py").write_text("raise RuntimeError('not data')")
    with pytest.raises(ValueError, match="unexpected"):
        publisher.validated_files(tmp_path)


def test_schedule_diagnostic_does_not_confuse_push_with_cron():
    from scanner.diagnose_schedule import summarize
    result = summarize({"default_branch": "main"}, {"state": "active"},
                       {"total_count": 0, "workflow_runs": []},
                       {"workflow_runs": [{"event": "push"}]}, ["7,22,37,52 * * * *"])
    assert result["findings"] == ["NO_SCHEDULE_EVENTS"]


def test_schedule_api_uses_exact_repository_url():
    from scanner.diagnose_schedule import github_get
    with patch("scanner.diagnose_schedule.urllib.request.build_opener") as build:
        build.return_value.open.return_value.__enter__.return_value.read.return_value = b'{}'
        assert github_get("owner/repo", "") == {}
        assert build.return_value.open.call_args.args[0].full_url == "https://api.github.com/repos/owner/repo"


def test_oversized_market_payload_rejected():
    client = MarketDataClient(Config.load().get("dataSource.failoverEndpoints"))
    with patch("scanner.market_data.MAX_RESPONSE_BYTES", 16), patch("urllib.request.build_opener") as build:
        response = build.return_value.open.return_value.__enter__.return_value
        response.read.return_value = b"x" * 17
        with pytest.raises(MarketDataError, match="size limit"):
            client._fetch("https://fapi.binance.com/fapi/v1/klines")
        response.read.assert_called_once_with(17)


@pytest.mark.parametrize("mode", ["valid", "missing", "mirror_mismatch"])
def test_publisher_requires_complete_matching_data(tmp_path, mode):
    spec = importlib.util.spec_from_file_location("publisher", repo_root() / ".github/scripts/publish_data.py")
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    for name in publisher.APPROVED:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo_root() / name, dest)
    if mode == "missing":
        (tmp_path / "data/signals.json").unlink()
        with pytest.raises(ValueError, match="missing"):
            publisher.validated_files(tmp_path)
    elif mode == "mirror_mismatch":
        (tmp_path / "frontend/data/signals.json").write_text('{}')
        with pytest.raises(ValueError, match="mirror"):
            publisher.validated_files(tmp_path)
    else:
        assert publisher.validated_files(tmp_path) == sorted(publisher.APPROVED)
