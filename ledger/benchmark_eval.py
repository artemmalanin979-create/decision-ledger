"""Objective 6-axis Total Cost of Decision rubric evaluator for agent vs plain chat comparisons."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .claims import audit_claims
from .cost import compute_token_cost, DEFAULT_PM_HOURLY_RUB

LOCATOR_LINK_RE = re.compile(r"\[[^\]]+\]\(https?://[^)]+\)|`[^`]+:(?:[^\n`]+)`|\b(?:cbr\.ru|megafon\.ru|vedomosti\.ru|cnews\.ru)\b", re.I)
EPISTEMIC_TAG_RE = re.compile(r"\b(?:факт|наблюдение|вывод|гипотеза|прогноз|неизвестно|fact|observation|inference|hypothesis|forecast|unknown)\b", re.I)


def evaluate_markdown_baseline(
    text: str,
    *,
    model: str = "gpt-5",
    initial_input_tokens: int = 0,
    initial_output_tokens: int = 0,
    followup_refeed_tokens: int = 0,
    followup_output_tokens: int = 0,
    wall_seconds: float = 0.0,
    human_verification_minutes: float = 0.0,
    stale_nodes_identified: int = 0,
    expected_stale_nodes: int = 1,
) -> dict:
    """Score a plain-chat Markdown output along the 6 contest benchmark axes."""
    paragraphs = [
        ln.strip() for ln in text.splitlines()
        if len(ln.strip()) >= 45 and not ln.strip().startswith("#")
    ]
    total_statements = max(len(paragraphs), 1)
    cited_statements = sum(1 for p in paragraphs if LOCATOR_LINK_RE.search(p))
    epistemic_tagged = sum(1 for p in paragraphs if EPISTEMIC_TAG_RE.search(p))

    usd_1, rub_1, _ = compute_token_cost(model, initial_input_tokens, 0, initial_output_tokens)
    usd_2, rub_2, _ = compute_token_cost(model, followup_refeed_tokens, 0, followup_output_tokens)
    human_rub = round((human_verification_minutes / 60.0) * DEFAULT_PM_HOURLY_RUB, 2)
    api_rub = round(rub_1 + rub_2, 2)

    return {
        "mode": "plain_chat",
        "citation_coverage_ratio": round(cited_statements / total_statements, 4),
        "cited_statements": cited_statements,
        "total_statements": total_statements,
        "facts_separated_from_hypotheses": epistemic_tagged >= max(3, total_statements // 4),
        "epistemic_markers_count": epistemic_tagged,
        "automated_hazards_caught": 0,
        "stale_impact_recall": round(stale_nodes_identified / max(expected_stale_nodes, 1), 4),
        "continuation_uncached_tokens": followup_refeed_tokens,
        "wall_seconds": round(wall_seconds, 2),
        "api_cost_usd": round(usd_1 + usd_2, 6),
        "api_cost_rub": api_rub,
        "human_minutes": round(human_verification_minutes, 2),
        "human_cost_rub": human_rub,
        "total_cost_of_decision_rub": round(api_rub + human_rub, 2),
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
    """Score a structured Decision Ledger proposal + follow-up impact + cost summary on the same 6 axes."""
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
    api_rub = float(cs.get("api_cost_rub", 0.0))
    human_min = float(cs.get("human_minutes", 0.0))
    human_rub = round((human_min / 60.0) * DEFAULT_PM_HOURLY_RUB, 2)

    return {
        "mode": "decision_ledger_agent",
        "citation_coverage_ratio": round(grounded / total_findings, 4),
        "cited_statements": grounded,
        "total_statements": total_findings,
        "facts_separated_from_hypotheses": bool({"fact", "hypothesis"} <= kinds_present or {"observation", "hypothesis"} <= kinds_present),
        "epistemic_kinds": sorted(k for k in kinds_present if k),
        "claims_audit_passed": claims["passed"],
        "automated_hazards_caught": int(caught_hazards_count),
        "stale_impact_recall": recall,
        "continuation_uncached_tokens": int(continuation_uncached_tokens),
        "wall_seconds": float(cs.get("wall_seconds", 0.0)),
        "api_cost_usd": float(cs.get("api_cost_usd", 0.0)),
        "api_cost_rub": round(api_rub, 2),
        "human_minutes": round(human_min, 2),
        "human_cost_rub": human_rub,
        "total_cost_of_decision_rub": round(api_rub + human_rub, 2),
    }


def evaluate_rubric_spec_file(path: Path) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    return spec
