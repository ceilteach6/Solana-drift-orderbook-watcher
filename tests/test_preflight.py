"""
tests/test_preflight.py

Tests the go-live preflight checks: each check in isolation (with the network
call mocked) and the aggregate report/exit-code behavior.
"""

import sys
import types

import pytest

from config.settings import Settings
from src import preflight
from src.preflight import (
    CheckResult,
    check_driftpy,
    check_feed_mode,
    check_markets,
    check_python,
    check_rpc,
    check_storage,
    format_report,
    preflight_main,
)


def make_settings(**overrides) -> Settings:
    base = dict(
        rpc_url="https://rpc.example.com/key",
        drift_env="mainnet",
        keypair_path="",
        markets=("SOL-PERP",),
    )
    base.update(overrides)
    return Settings(**base)


def test_check_python_passes_on_this_interpreter():
    result = check_python()
    assert result.passed  # the suite itself needs 3.10+


def test_check_driftpy_reports_missing_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "driftpy", None)  # forces ImportError
    result = check_driftpy()
    assert not result.passed
    assert "virtualenv" in result.hint


def test_check_driftpy_reports_installed_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "driftpy", types.ModuleType("driftpy"))
    result = check_driftpy()
    assert result.passed  # version may be "installed" without dist metadata


def test_check_rpc_success(monkeypatch):
    responses = {"getHealth": {"result": "ok"}, "getSlot": {"result": 123456}}
    monkeypatch.setattr(preflight, "_rpc_call",
                        lambda url, method, timeout: responses[method])
    result = check_rpc(make_settings())
    assert result.passed
    assert "slot 123456" in result.detail
    assert result.hint == ""  # non-public endpoint: no rate-limit warning


def test_check_rpc_public_endpoint_warns_but_passes(monkeypatch):
    monkeypatch.setattr(preflight, "_rpc_call",
                        lambda url, method, timeout: {"result": 1})
    result = check_rpc(make_settings(rpc_url="https://api.mainnet-beta.solana.com"))
    assert result.passed
    assert "helius" in result.hint.lower()


def test_check_rpc_failure_hints_at_network(monkeypatch):
    def boom(url, method, timeout):
        raise OSError("CONNECT tunnel failed, response 403")

    monkeypatch.setattr(preflight, "_rpc_call", boom)
    result = check_rpc(make_settings())
    assert not result.passed
    assert "403" in result.detail
    assert "WSS" in result.hint


def test_check_markets_pass_and_unknown_venue():
    assert check_markets(make_settings()).passed
    result = check_markets(make_settings(markets=("nosuchvenue:X",)))
    assert not result.passed
    assert "nosuchvenue" in result.detail


def test_check_markets_malformed_spec():
    result = check_markets(make_settings(markets=("phoenix:",)))
    assert not result.passed


def test_check_feed_mode_semantics():
    assert check_feed_mode(make_settings(feed_mode="live")).passed
    assert check_feed_mode(make_settings(feed_mode="live")).hint == ""

    auto = check_feed_mode(make_settings(feed_mode="auto"))
    assert auto.passed and "FEED_MODE=live" in auto.hint

    synthetic = check_feed_mode(make_settings(feed_mode="synthetic"))
    assert not synthetic.passed


def test_check_storage_disabled_passes_with_hint():
    result = check_storage(make_settings(storage_enabled=False))
    assert result.passed
    assert "replay" in result.hint


def test_check_storage_enabled_verifies_writability(tmp_path):
    ok = check_storage(make_settings(storage_enabled=True,
                                     db_path=str(tmp_path / "pf.db")))
    assert ok.passed


def test_format_report_and_exit_codes(monkeypatch):
    all_green = [CheckResult("a", True, "fine"), CheckResult("b", True, "fine")]
    monkeypatch.setattr(preflight, "run_preflight", lambda s: all_green)
    assert preflight_main(make_settings()) == 0

    one_red = [CheckResult("a", True, "fine"),
               CheckResult("b", False, "broken", hint="fix it")]
    monkeypatch.setattr(preflight, "run_preflight", lambda s: one_red)
    assert preflight_main(make_settings()) == 1

    text = format_report(one_red)
    assert "✅" in text and "❌" in text
    assert "fix it" in text
    assert "NOT ready" in text
