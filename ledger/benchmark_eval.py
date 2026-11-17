"""Objective rubric evaluator for Decision Ledger vs plain chat comparisons (zero fabricated numbers)."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .claims import audit_claims
from .cost import compute_token_cost, DEFAULT_PM_HOURLY_RUB

LOCATOR_LINK_RE = re.compile(r"\[[^\]]+\]\(https?://[^)]+\)|`[^`]+:(?:[^\n`]+)`|\b(?:cbr\.ru|megafon\.ru|vedomosti\.ru|cnews\.ru)\b", re.I)
EPISTEMIC_TAG_RE = re.compile(r"\b(?:факт|наблюдение|вывод|гипотеза|прогноз|неизвестно|допущени[ея]|fact|observation|inference|hypothesis|forecast|unknown)\b", re.I)


def evaluate_markdown_baseline(
    text: str,
    *,
    model: str = "antigravity-subscription",
    initial_input_tokens: int = 0,
    initial_cached_tokens: int = 0,
    initial_output_tokens: int = 0,
    followup_input_tokens: int = 0,
    followup_cached_tokens: int = 0,
    followup_output_tokens: int = 0,
    wall_seconds: float | None = None,
    human_verification_minutes: float | None = None,
    stale_nodes_identified: int | None = None,
    expected_stale_nodes: int = 1,
    cost_method: str = "",
) -> dict:
    """Score a plain-chat Markdown output using only measured transcript fields and verifiable text properties."""
    paragraphs = [
        ln.strip() for ln in text.splitlines()
        if len(ln.strip()) >= 45 and not ln.strip().startswith("#")
    ]
    total_statements = max(len(paragraphs), 1)
    cited_statements = sum(1 for p in paragraphs if LOCATOR_LINK_RE.search(p))
    epistemic_tagged = sum(1 for p in paragraphs if EPISTEMIC_TAG_RE.search(p))

    usd_1, rub_1, _, est_1 = compute_token_cost(model, initial_input_tokens, initial_cached_tokens, initial_output_tokens)
    usd_2, rub_2, _, est_2 = compute_token_cost(model, followup_input_tokens, followup_cached_tokens, followup_output_tokens)
    api_computed = rub_1 is not None and rub_2 is not None
    api_rub = round(rub_1 + rub_2, 2) if api_computed else None
    human_rub = (
        round((human_verification_minutes / 60.0) * DEFAULT_PM_HOURLY_RUB, 2)
        if human_verification_minutes is not None else None
    )

    return {
        "mode": "plain_chat",
        "citation_coverage_ratio": round(cited_statements / total_statements, 4),
        "cited_statements": cited_statements,
        "total_statements": total_statements,
        "sha256_snapshots_count": 0,
        "facts_separated_from_hypotheses": epistemic_tagged >= max(3, total_statements // 4),
        "epistemic_markers_count": epistemic_tagged,
        "automated_hazards_caught": 0,
        "machine_dependency_graph": False,
        "stale_impact_recall": (
            round(stale_nodes_identified / max(expected_stale_nodes, 1), 4)
            if stale_nodes_identified is not None else None
        ),
        "continuation_uncached_tokens": max(followup_input_tokens - followup_cached_tokens, 0),
        "wall_seconds": round(wall_seconds, 2) if wall_seconds is not None else None,
        "api_cost_estimated": bool(est_1 or est_2),
        "cost_method": cost_method or "Токены и время из системного лога; денежный эквивалент оценочный",
        "api_cost_computed": api_computed,
        "api_cost_usd": round(usd_1 + usd_2, 6) if api_computed else None,
        "api_cost_rub": api_rub,
        "human_minutes": round(human_verification_minutes, 2) if human_verification_minutes is not None else None,
        "human_cost_rub": human_rub,
        "total_cost_of_decision_rub": (
            round(api_rub + human_rub, 2) if (human_rub is not None and api_rub is not None) else None
        ),
    }


def evaluate_ledger_case_rubric(
    proposal: dict,
    sources_health: dict,
    *,
    impact_report: dict | None = None,
    expected_stale_findings: int = 0,
    caught_hazards_count: int = 0,
    cost_summary: dict | None = None,
    continuation_uncached_tokens: int = 0,
) -> dict:
    """Score a structured Decision Ledger proposal + follow-up impact + cost summary."""
    findings = proposal.get("findings", {})
    total_findings = max(len(findings), 1)
    grounded = sum(
        1 for f in findings.values()
        if f.get("citations") or f.get("depends_on")
    )
    kinds_present = {f.get("kind") for f in findings.values()}
    claims = audit_claims(proposal, sources_health)
    stale_found = len((impact_report or {}).get("findings", []))
    recall = (
        round(min(stale_found, expected_stale_findings) / expected_stale_findings, 4)
        if expected_stale_findings > 0 else 1.0
    )

    cs = cost_summary or {}
    api_rub = cs.get("api_cost_rub")
    api_rub = float(api_rub) if api_rub is not None else None
    human_min = cs.get("human_minutes")
    human_rub = round((float(human_min) / 60.0) * DEFAULT_PM_HOURLY_RUB, 2) if human_min is not None else None

    return {
        "mode": "decision_ledger_agent",
        "citation_coverage_ratio": round(grounded / total_findings, 4),
        "cited_statements": grounded,
        "total_statements": total_findings,
        "sha256_snapshots_count": len(sources_health),
        "facts_separated_from_hypotheses": bool({"fact", "hypothesis"} <= kinds_present or {"observation", "hypothesis"} <= kinds_present),
        "epistemic_kinds": sorted(k for k in kinds_present if k),
        "claims_audit_passed": claims["passed"],
        "automated_hazards_caught": int(caught_hazards_count),
        "machine_dependency_graph": True,
        "stale_impact_recall": recall,
        "continuation_uncached_tokens": int(continuation_uncached_tokens),
        "wall_seconds": float(cs.get("wall_seconds", 0.0)),
        "api_cost_estimated": bool(cs.get("estimated", False)),
        "cost_methods": cs.get("methods", []),
        "api_cost_computed": api_rub is not None,
        "api_cost_usd": float(cs["api_cost_usd"]) if cs.get("api_cost_usd") is not None else None,
        "api_cost_rub": round(api_rub, 2) if api_rub is not None else None,
        "human_minutes": round(float(human_min), 2) if human_min is not None else None,
        "human_cost_rub": human_rub,
        "total_cost_of_decision_rub": (
            round(api_rub + human_rub, 2) if (human_rub is not None and api_rub is not None) else None
        ),
    }


def evaluate_rubric_spec_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
