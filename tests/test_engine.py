"""Tests for risk scoring engine."""

from datetime import datetime, timezone, timedelta

from rugcheck.engine.risk_engine import evaluate, build_report, RiskLevel
from rugcheck.models import AggregatedData


def _make_data(**kwargs) -> AggregatedData:
    """Helper to create AggregatedData with defaults."""
    defaults = {
        "token_name": "TestToken",
        "token_symbol": "TEST",
        "sources_succeeded": ["RugCheck", "DexScreener", "GoPlus"],
    }
    defaults.update(kwargs)
    return AggregatedData(**defaults)


# --- Individual rule tests ---


def test_mintable_triggers_critical():
    data = _make_data(is_mintable=True)
    score, reds, greens = evaluate(data)
    assert score >= 40
    assert any("mint authority" in f.message.lower() for f in reds)


def test_mintable_false_gives_green_flag():
    data = _make_data(is_mintable=False)
    _score, _reds, greens = evaluate(data)
    assert any("Mint Renounced" in f.message for f in greens)


def test_freezable_triggers_critical():
    data = _make_data(is_freezable=True)
    score, reds, _greens = evaluate(data)
    assert score >= 30
    assert any("freeze authority" in f.message.lower() for f in reds)


def test_lp_unprotected_neither_burned_nor_locked():
    """LP neither burned nor locked → CRITICAL."""
    data = _make_data(lp_burned_pct=10.0, lp_locked_pct=5.0)
    score, reds, _greens = evaluate(data)
    assert score >= 35
    assert any("LP Unprotected" in f.message for f in reds)


def test_lp_burned_sufficient():
    """LP burned >= 50% → safe, even if locked is low."""
    data = _make_data(lp_burned_pct=80.0, lp_locked_pct=0.0)
    _score, _reds, greens = evaluate(data)
    assert any("LP Burned or Locked" in f.message for f in greens)


def test_lp_locked_sufficient():
    """LP locked >= 50% → safe, even if burned is low."""
    data = _make_data(lp_burned_pct=0.0, lp_locked_pct=75.0)
    _score, _reds, greens = evaluate(data)
    assert any("LP Burned or Locked" in f.message for f in greens)


def test_lp_both_unknown_skips_rule():
    """Both None → rule is skipped (no flag)."""
    data = _make_data(lp_burned_pct=None, lp_locked_pct=None)
    score, reds, _greens = evaluate(data)
    assert not any("LP" in f.message for f in reds)


def test_top10_concentrated():
    data = _make_data(top10_holder_pct=85.0)
    score, reds, _greens = evaluate(data)
    assert score >= 25
    assert any("Top 10 holders" in f.message for f in reds)


def test_low_liquidity():
    data = _make_data(liquidity_usd=5000.0)
    score, reds, _greens = evaluate(data)
    assert score >= 20
    assert any("low liquidity" in f.message.lower() for f in reds)


def test_sell_pressure():
    """Sell count > 3x buy count → HIGH risk."""
    data = _make_data(buy_count_24h=100, sell_count_24h=400)
    score, reds, _greens = evaluate(data)
    assert score >= 15
    assert any("sell" in f.message.lower() for f in reds)


def test_no_sell_pressure():
    """Normal buy/sell ratio → no flag."""
    data = _make_data(buy_count_24h=1000, sell_count_24h=800)
    _score, reds, _greens = evaluate(data)
    assert not any("sell" in f.message.lower() for f in reds)


def test_closable_is_low_severity():
    """Closable should be LOW (not HIGH) on Solana."""
    data = _make_data(is_closable=True)
    score, reds, _greens = evaluate(data)
    assert score <= 5  # LOW: only 3 points
    closable_flags = [f for f in reds if "close authority" in f.message.lower()]
    assert len(closable_flags) == 1
    assert closable_flags[0].level == "LOW"


def test_metadata_mutable_is_low_severity():
    """Metadata mutable should be LOW on Solana."""
    data = _make_data(is_metadata_mutable=True)
    score, reds, _greens = evaluate(data)
    assert score <= 5
    meta_flags = [f for f in reds if "metadata" in f.message.lower()]
    assert len(meta_flags) == 1
    assert meta_flags[0].level == "LOW"


def test_very_new_pair():
    recent = datetime.now(timezone.utc) - timedelta(hours=2)
    data = _make_data(pair_created_at=recent)
    score, reds, _greens = evaluate(data)
    assert score >= 10
    assert any("24 hours" in f.message.lower() for f in reds)


def test_low_volume():
    data = _make_data(volume_24h_usd=500.0)
    score, reds, _greens = evaluate(data)
    assert score >= 5


# --- Liquidity exemption tests ---


def test_lp_unprotected_exempt_by_high_liquidity():
    """LP not burned/locked but liquidity >= $1M → exempt (no flag)."""
    data = _make_data(lp_burned_pct=0.0, lp_locked_pct=0.0, liquidity_usd=2_000_000.0)
    score, reds, greens = evaluate(data)
    assert not any("LP Unprotected" in f.message for f in reds)
    assert any("LP Burned or Locked" in f.message for f in greens)


def test_lp_unprotected_not_exempt_by_low_liquidity():
    """LP not burned/locked and liquidity < $1M → still flagged."""
    data = _make_data(lp_burned_pct=0.0, lp_locked_pct=0.0, liquidity_usd=500_000.0)
    score, reds, _greens = evaluate(data)
    assert any("LP Unprotected" in f.message for f in reds)


def test_top10_concentrated_exempt_by_high_liquidity():
    """Top 10 > 80% but liquidity >= $1M → exempt."""
    data = _make_data(top10_holder_pct=85.0, liquidity_usd=5_000_000.0)
    score, reds, _greens = evaluate(data)
    assert not any("Top 10 holders" in f.message for f in reds)


def test_top10_concentrated_not_exempt_by_low_liquidity():
    """Top 10 > 80% and liquidity < $1M → still flagged."""
    data = _make_data(top10_holder_pct=85.0, liquidity_usd=100_000.0)
    score, reds, _greens = evaluate(data)
    assert any("Top 10 holders" in f.message for f in reds)


# --- Composite scenarios ---


def test_completely_safe_token():
    data = _make_data(
        is_mintable=False,
        is_freezable=False,
        is_closable=False,
        is_metadata_mutable=False,
        lp_burned_pct=99.0,
        lp_locked_pct=95.0,
        top10_holder_pct=20.0,
        liquidity_usd=5_000_000.0,
        volume_24h_usd=1_000_000.0,
        buy_count_24h=10000,
        sell_count_24h=8000,
        pair_created_at=datetime.now(timezone.utc) - timedelta(days=365),
    )
    score, reds, greens = evaluate(data)
    assert score == 0
    assert len(reds) == 0
    assert len(greens) >= 3  # mint, freeze, lp green flags


def test_maximum_danger_scam():
    data = _make_data(
        is_mintable=True,
        is_freezable=True,
        is_closable=True,
        is_metadata_mutable=True,
        lp_burned_pct=0.0,
        lp_locked_pct=0.0,
        top10_holder_pct=95.0,
        liquidity_usd=500.0,
        volume_24h_usd=100.0,
        buy_count_24h=10,
        sell_count_24h=50,
        pair_created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    score, reds, greens = evaluate(data)
    assert score == 100  # clamped at 100
    assert len(reds) >= 7
    assert len(greens) == 0


def test_missing_data_skips_rules():
    """All None fields should not trigger any rules."""
    data = _make_data()
    score, reds, greens = evaluate(data)
    assert score == 0
    assert len(reds) == 0


# --- Report building ---


def test_build_report_critical():
    data = _make_data(is_mintable=True, is_freezable=True, lp_burned_pct=0.0, lp_locked_pct=0.0)
    report = build_report("FAKEMINT123", data, response_time_ms=500)

    assert report.contract_address == "FAKEMINT123"
    assert report.chain == "solana"
    assert report.action.is_safe is False
    assert report.action.risk_level == RiskLevel.CRITICAL
    assert report.action.risk_score >= 70
    assert len(report.analysis.red_flags) >= 3
    assert "rug pull" in report.analysis.summary.lower()
    assert report.metadata.response_time_ms == 500
    assert report.metadata.data_completeness == "full"


def test_build_report_safe():
    data = _make_data(
        is_mintable=False,
        is_freezable=False,
        is_closable=False,
        lp_burned_pct=99.0,
        lp_locked_pct=99.0,
        liquidity_usd=1_000_000.0,
        volume_24h_usd=500_000.0,
    )
    report = build_report("SAFEMINT", data)

    assert report.action.is_safe is True
    assert report.action.risk_level in (RiskLevel.SAFE, RiskLevel.LOW)
    assert len(report.analysis.green_flags) >= 2


def test_build_report_partial_data():
    data = _make_data(sources_succeeded=["RugCheck"], sources_failed=["GoPlus", "DexScreener"])
    report = build_report("PARTIALMINT", data)
    assert report.metadata.data_completeness == "minimal"


def test_build_report_partial_two_sources():
    """Two sources succeeded, one failed → partial."""
    data = _make_data(sources_succeeded=["RugCheck", "DexScreener"], sources_failed=["GoPlus"])
    report = build_report("PARTIALMINT2", data)
    assert report.metadata.data_completeness == "partial"


def test_build_report_cache_hit():
    data = _make_data()
    report = build_report("CACHEMINT", data, cache_hit=True)
    assert report.metadata.cache_hit is True


def test_build_report_includes_lp_locked():
    data = _make_data(lp_burned_pct=10.0, lp_locked_pct=75.0)
    report = build_report("LPMINT", data)
    assert report.evidence.lp_burned_pct == 10.0
    assert report.evidence.lp_locked_pct == 75.0
