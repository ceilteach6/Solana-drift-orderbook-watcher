"""
tests/test_reputation.py

Unit tests for WalletReputation and the wallet_reputation SQLite methods.
No RPC, no driftpy required.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.reputation.wallet_reputation import WalletReputation
from src.storage.sqlite_store import SqliteStore


def make_settings(**kw):
    return SimpleNamespace(
        blocklist_wallets=kw.get("blocklist_wallets", []),
        reputation_decay=kw.get("reputation_decay", 0.90),
        reputation_block_threshold=kw.get("reputation_block_threshold", 0.75),
        db_path="",
    )


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(str(tmp_path / "rep.db"))
    s.open()
    yield s
    s.close()


# --------------------------------------------------------------------------- #
# WalletReputation — in-memory behaviour
# --------------------------------------------------------------------------- #

def test_initial_score_is_zero():
    rep = WalletReputation(make_settings())
    assert rep.get_score("wallet_A") == 0.0


def test_record_accumulates_ema():
    rep = WalletReputation(make_settings(reputation_decay=0.9))
    s1 = rep.record("wallet_A", 1.0)
    assert s1 == pytest.approx(0.1)  # 0.9*0 + 0.1*1.0

    s2 = rep.record("wallet_A", 1.0)
    assert s2 == pytest.approx(0.19)  # 0.9*0.1 + 0.1*1.0


def test_not_blocked_below_threshold():
    rep = WalletReputation(make_settings(reputation_block_threshold=0.75))
    rep.record("wallet_A", 0.5)
    assert not rep.is_blocked("wallet_A")


def test_auto_block_above_threshold():
    rep = WalletReputation(make_settings(reputation_decay=0.0, reputation_block_threshold=0.75))
    # decay=0.0 → score = detection_score on each tick
    rep.record("wallet_A", 0.80)
    assert rep.is_blocked("wallet_A")


def test_manual_block_overrides_score():
    rep = WalletReputation(make_settings(reputation_block_threshold=0.99))
    rep.block("wallet_B")
    assert rep.is_blocked("wallet_B")
    assert rep.get_score("wallet_B") == 0.0  # no detections yet


def test_blocklist_wallets_from_settings():
    rep = WalletReputation(make_settings(blocklist_wallets=["addr1", "addr2"]))
    assert rep.is_blocked("addr1")
    assert rep.is_blocked("addr2")
    assert not rep.is_blocked("addr3")


def test_top_suspects_sorted_descending():
    rep = WalletReputation(make_settings(reputation_decay=0.0))
    rep.record("wallet_low", 0.3)
    rep.record("wallet_high", 0.9)
    rep.record("wallet_mid", 0.6)
    suspects = rep.top_suspects(n=3)
    scores = [s["score"] for s in suspects]
    assert scores == sorted(scores, reverse=True)
    assert suspects[0]["wallet"] == "wallet_high"


def test_top_suspects_includes_blocked_flag():
    rep = WalletReputation(make_settings(reputation_decay=0.0, reputation_block_threshold=0.75))
    rep.record("bad_actor", 0.90)
    top = rep.top_suspects(1)
    assert top[0]["blocked"] is True


def test_top_suspects_respects_n_limit():
    rep = WalletReputation(make_settings())
    for i in range(20):
        rep.record(f"wallet_{i}", 0.5)
    assert len(rep.top_suspects(5)) == 5


def test_len_tracks_unique_wallets():
    rep = WalletReputation(make_settings())
    assert len(rep) == 0
    rep.record("w1", 0.5)
    rep.record("w1", 0.5)  # same wallet twice
    rep.record("w2", 0.5)
    assert len(rep) == 2


def test_hit_count_increments():
    rep = WalletReputation(make_settings())
    for _ in range(5):
        rep.record("wallet_A", 0.5)
    top = rep.top_suspects(1)
    assert top[0]["hit_count"] == 5


def test_unknown_wallet_not_blocked():
    rep = WalletReputation(make_settings())
    assert not rep.is_blocked("never_seen_wallet")


# --------------------------------------------------------------------------- #
# WalletReputation — SQLite persistence
# --------------------------------------------------------------------------- #

def test_load_restores_scores(store):
    rep = WalletReputation(make_settings(reputation_decay=0.0), store=store)
    rep.record("wallet_A", 0.6)
    rep.record("wallet_B", 0.8)

    rep2 = WalletReputation(make_settings(reputation_decay=0.0), store=store)
    rep2.load()
    assert rep2.get_score("wallet_A") == pytest.approx(0.6)
    assert rep2.get_score("wallet_B") == pytest.approx(0.8)


def test_load_restores_blocked_flag(store):
    rep = WalletReputation(make_settings(), store=store)
    rep.block("blocked_wallet")

    rep2 = WalletReputation(make_settings(), store=store)
    rep2.load()
    assert rep2.is_blocked("blocked_wallet")


def test_persistence_empty_on_disabled_store():
    rep = WalletReputation(make_settings(), store=None)
    rep.record("wallet_A", 0.9)
    rep.load()  # no-op, must not raise
    assert rep.get_score("wallet_A") == pytest.approx(0.09)


# --------------------------------------------------------------------------- #
# SqliteStore — wallet_reputation table methods
# --------------------------------------------------------------------------- #

def test_upsert_and_load_wallet(store):
    store.upsert_wallet("addr1", 0.5, 3, False)
    rows = store.load_wallets()
    assert len(rows) == 1
    assert rows[0]["wallet"] == "addr1"
    assert rows[0]["score"] == pytest.approx(0.5)
    assert rows[0]["hit_count"] == 3
    assert rows[0]["blocked"] is False


def test_upsert_updates_existing(store):
    store.upsert_wallet("addr1", 0.3, 1, False)
    store.upsert_wallet("addr1", 0.7, 4, True)
    rows = store.load_wallets()
    assert len(rows) == 1
    assert rows[0]["score"] == pytest.approx(0.7)
    assert rows[0]["blocked"] is True


def test_top_suspect_wallets_sorted(store):
    store.upsert_wallet("low", 0.2, 1, False)
    store.upsert_wallet("high", 0.9, 5, False)
    store.upsert_wallet("mid", 0.5, 2, False)
    top = store.top_suspect_wallets(n=2)
    assert top[0]["wallet"] == "high"
    assert top[1]["wallet"] == "mid"


def test_wallet_count(store):
    assert store.wallet_count() == 0
    store.upsert_wallet("addr1", 0.5, 1, False)
    store.upsert_wallet("addr2", 0.6, 2, False)
    assert store.wallet_count() == 2


def test_wallet_methods_noop_on_disabled_store():
    s = SqliteStore("")
    s.open()
    s.upsert_wallet("addr", 0.5, 1, False)  # must not raise
    assert s.load_wallets() == []
    assert s.top_suspect_wallets() == []
    assert s.wallet_count() == 0
    s.close()
