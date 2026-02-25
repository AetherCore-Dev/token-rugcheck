"""Risk scoring engine — pure deterministic rules, zero LLM.

Rule design principles (validated against real Solana ecosystem 2026-02):
  - LP "locked" (RugCheck) and LP "burned" (GoPlus) are tracked separately.
    Locked LP may unlock later; burned LP is permanent.
  - Solana's "closable" refers to token account rent reclamation, NOT token
    destruction. It is demoted from HIGH to LOW.
  - "Metadata mutable" is common for legitimate Solana tokens and demoted to LOW.
  - A combined "LP unprotected" rule checks if LP is neither burned NOR locked.
  - Buy/sell ratio from DexScreener is used to detect active dumps.
  - **Liquidity exemption**: tokens with >= $1M liquidity are exempt from
    LP-protection and holder-concentration rules. High liquidity is itself
    a strong anti-rug signal for established tokens (BONK, WIF, JUP, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from rugcheck.models import (
    ActionLayer,
    AggregatedData,
    AnalysisLayer,
    AuditReport,
    AuditMetadata,
    EvidenceLayer,
    RiskFlag,
    RiskLevel,
)


@dataclass
class Rule:
    """A single risk assessment rule."""

    name: str
    level: str  # CRITICAL, HIGH, MEDIUM, LOW
    score: int  # points added to risk_score (0-100)
    flag_message: str
    evaluate: Callable[[AggregatedData], bool | None]  # True = risk triggered, None = data unavailable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Tokens with liquidity >= this threshold are exempt from LP-protection
# and holder-concentration rules. High liquidity is itself anti-rug.
LIQUIDITY_EXEMPTION_USD = 1_000_000


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _pair_age_hours(data: AggregatedData) -> float | None:
    if data.pair_created_at is None:
        return None
    now = datetime.now(timezone.utc)
    ts = data.pair_created_at.replace(tzinfo=timezone.utc) if data.pair_created_at.tzinfo is None else data.pair_created_at
    return (now - ts).total_seconds() / 3600


def _is_high_liquidity(data: AggregatedData) -> bool:
    """Check if the token has enough liquidity to qualify for exemptions."""
    return data.liquidity_usd is not None and data.liquidity_usd >= LIQUIDITY_EXEMPTION_USD


def _lp_unprotected(data: AggregatedData) -> bool | None:
    """Returns True if LP is neither sufficiently burned NOR sufficiently locked.

    Exempt if liquidity >= $1M — established high-liquidity pools are
    inherently resistant to rug pulls even without LP burn/lock.
    """
    if _is_high_liquidity(data):
        return False
    burned = data.lp_burned_pct
    locked = data.lp_locked_pct
    # If both are unknown, skip the rule
    if burned is None and locked is None:
        return None
    # Safe if EITHER burned >= 50% OR locked >= 50%
    if burned is not None and burned >= 50:
        return False
    if locked is not None and locked >= 50:
        return False
    # If we have data and neither is sufficient, flag it
    return True


def _top10_concentrated(data: AggregatedData) -> bool | None:
    """Top 10 holders > 80% is risky, but exempt for high-liquidity tokens."""
    if data.top10_holder_pct is None:
        return None
    if _is_high_liquidity(data):
        return False
    return data.top10_holder_pct > 80


def _sell_pressure(data: AggregatedData) -> bool | None:
    """Detects heavy sell-side pressure: sells > 3x buys in 24h."""
    if data.buy_count_24h is None or data.sell_count_24h is None:
        return None
    if data.buy_count_24h == 0:
        return data.sell_count_24h > 0
    return data.sell_count_24h > data.buy_count_24h * 3


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

RULES: list[Rule] = [
    # === Critical (any single one = extreme danger) ===
    Rule(
        name="mintable",
        level="CRITICAL",
        score=40,
        flag_message="Contract has active mint authority (Mintable) — owner can inflate supply at will.",
        evaluate=lambda d: d.is_mintable,
    ),
    Rule(
        name="lp_unprotected",
        level="CRITICAL",
        score=35,
        flag_message="Liquidity pool is neither burned nor sufficiently locked (LP Unprotected) — owner can rug pull.",
        evaluate=_lp_unprotected,
    ),
    Rule(
        name="freezable",
        level="CRITICAL",
        score=30,
        flag_message="Contract has freeze authority (Freezable) — owner can freeze any holder's tokens.",
        evaluate=lambda d: d.is_freezable,
    ),
    # === High ===
    Rule(
        name="top10_concentrated",
        level="HIGH",
        score=25,
        flag_message="Top 10 holders control over 80% of supply — highly concentrated ownership.",
        evaluate=_top10_concentrated,
    ),
    Rule(
        name="low_liquidity",
        level="HIGH",
        score=20,
        flag_message="Extremely low liquidity (< $10,000) — easily manipulated or rug-pulled.",
        evaluate=lambda d: d.liquidity_usd < 10_000 if d.liquidity_usd is not None else None,
    ),
    Rule(
        name="sell_pressure",
        level="HIGH",
        score=15,
        flag_message="24h sell count far exceeds buys (>3x) — possible active dump.",
        evaluate=_sell_pressure,
    ),
    # === Medium ===
    Rule(
        name="very_new_pair",
        level="MEDIUM",
        score=10,
        flag_message="Trading pair created less than 24 hours ago — very early-stage, high risk.",
        evaluate=lambda d: _pair_age_hours(d) < 24 if _pair_age_hours(d) is not None else None,
    ),
    Rule(
        name="low_volume",
        level="MEDIUM",
        score=5,
        flag_message="Extremely low 24h volume (< $1,000) — insufficient liquidity.",
        evaluate=lambda d: d.volume_24h_usd < 1_000 if d.volume_24h_usd is not None else None,
    ),
    # === Low (informational, not alarming) ===
    Rule(
        name="metadata_mutable",
        level="LOW",
        score=3,
        flag_message="Token metadata is mutable — common for Solana tokens.",
        evaluate=lambda d: d.is_metadata_mutable,
    ),
    Rule(
        name="closable",
        level="LOW",
        score=3,
        flag_message="Contract has close authority (Closable) — typically used for Solana rent reclamation.",
        evaluate=lambda d: d.is_closable,
    ),
]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def evaluate(data: AggregatedData) -> tuple[int, list[RiskFlag], list[RiskFlag]]:
    """Run all rules and return (risk_score, red_flags, green_flags)."""
    risk_score = 0
    red_flags: list[RiskFlag] = []
    green_flags: list[RiskFlag] = []

    for rule in RULES:
        try:
            triggered = rule.evaluate(data)
        except Exception:  # noqa: BLE001
            continue  # data unavailable → skip rule

        if triggered is True:
            risk_score += rule.score
            red_flags.append(RiskFlag(level=rule.level, message=rule.flag_message))
        elif triggered is False:
            # Explicitly safe on this dimension — only show green for critical rules
            if rule.level == "CRITICAL":
                green_flags.append(RiskFlag(level="SAFE", message=_invert_message(rule.name)))

    risk_score = min(risk_score, 100)
    return risk_score, red_flags, green_flags


def build_report(
    mint_address: str,
    data: AggregatedData,
    response_time_ms: int = 0,
    cache_hit: bool = False,
) -> AuditReport:
    """Build a complete three-layer audit report."""
    risk_score, red_flags, green_flags = evaluate(data)

    if risk_score >= 70:
        risk_level = RiskLevel.CRITICAL
    elif risk_score >= 40:
        risk_level = RiskLevel.HIGH
    elif risk_score >= 20:
        risk_level = RiskLevel.MEDIUM
    elif risk_score >= 5:
        risk_level = RiskLevel.LOW
    else:
        risk_level = RiskLevel.SAFE

    is_safe = risk_score < 40

    if risk_level == RiskLevel.CRITICAL:
        summary = "This token has multiple critical risk factors and is very likely a rug pull scam. Strongly avoid."
    elif risk_level == RiskLevel.HIGH:
        summary = "This token has significant risk factors. Evaluate carefully before trading."
    elif risk_level == RiskLevel.MEDIUM:
        summary = "This token has moderate risk. Review the details before making a decision."
    elif risk_level == RiskLevel.LOW:
        summary = "This token has low risk, but always be aware of market volatility."
    else:
        summary = "No significant risk signals detected. Always do your own research (DYOR)."

    if len(data.sources_failed) == 0:
        completeness = "full"
    elif len(data.sources_succeeded) >= 2:
        completeness = "partial"
    else:
        completeness = "minimal"

    return AuditReport(
        contract_address=mint_address,
        chain="solana",
        action=ActionLayer(is_safe=is_safe, risk_level=risk_level, risk_score=risk_score),
        analysis=AnalysisLayer(summary=summary, red_flags=red_flags, green_flags=green_flags),
        evidence=EvidenceLayer(
            token_name=data.token_name,
            token_symbol=data.token_symbol,
            price_usd=data.price_usd,
            liquidity_usd=data.liquidity_usd,
            volume_24h_usd=data.volume_24h_usd,
            top_10_holders_pct=data.top10_holder_pct,
            is_mintable=data.is_mintable,
            is_freezable=data.is_freezable,
            is_closable=data.is_closable,
            lp_burned_pct=data.lp_burned_pct,
            lp_locked_pct=data.lp_locked_pct,
            pair_created_at=data.pair_created_at.isoformat() if data.pair_created_at else None,
            holder_count=data.holder_count,
            rugcheck_score=data.rugcheck_score,
        ),
        metadata=AuditMetadata(
            data_sources=data.sources_succeeded,
            data_completeness=completeness,
            cache_hit=cache_hit,
            response_time_ms=response_time_ms,
        ),
    )


def _invert_message(rule_name: str) -> str:
    """Generate a positive message for rules that passed."""
    messages = {
        "mintable": "Mint authority renounced (Mint Renounced).",
        "freezable": "No freeze authority (Not Freezable).",
        "lp_unprotected": "Liquidity pool is sufficiently protected (LP Burned or Locked).",
    }
    return messages.get(rule_name, f"{rule_name}: OK")
