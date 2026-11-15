"""Deterministic unit economics, break-even cost bounds, and sensitivity checks."""

from __future__ import annotations

import json
from pathlib import Path


def _positive_number(value, label: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{label} must be finite")
    if allow_zero and result < 0:
        raise ValueError(f"{label} must be non-negative")
    if not allow_zero and result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def evaluate_plan(plan: dict, *, min_margin_rate: float) -> dict:
    """Compute monthly contribution margin, cost headroom, payback, and stress scenarios."""
    if not isinstance(plan, dict):
        raise ValueError("Each plan must be an object")
    title = plan.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Plan requires a non-empty title")

    revenue = _positive_number(plan.get("monthly_revenue"), "monthly_revenue")
    recurring = plan.get("recurring_costs", {})
    if not isinstance(recurring, dict):
        raise ValueError("recurring_costs must be an object mapping cost items to amounts")
    recurring_total = sum(
        _positive_number(amount, f"recurring_costs.{key}", allow_zero=True)
        for key, amount in recurring.items()
    )

    capex_items = plan.get("upfront_costs", {})
    if not isinstance(capex_items, dict):
        raise ValueError("upfront_costs must be an object mapping upfront items to amounts")
    upfront_total = sum(
        _positive_number(amount, f"upfront_costs.{key}", allow_zero=True)
        for key, amount in capex_items.items()
    )

    amortize_months = plan.get("amortize_months", 12)
    if isinstance(amortize_months, bool) or not isinstance(amortize_months, int) or amortize_months < 1:
        raise ValueError("amortize_months must be a positive integer")

    amortized_upfront = upfront_total / amortize_months if upfront_total else 0.0
    direct_cost = recurring_total + amortized_upfront
    contribution = revenue - direct_cost
    contribution_rate = contribution / revenue
    max_allowable_cost = revenue * (1.0 - min_margin_rate)
    cost_headroom = max_allowable_cost - direct_cost
    cash_margin_before_capex = revenue - recurring_total
    payback_months = (
        round(upfront_total / cash_margin_before_capex, 2)
        if upfront_total > 0 and cash_margin_before_capex > 0
        else (0.0 if upfront_total == 0 else None)
    )

    def scenario(price_factor: float, cost_factor: float) -> dict:
        adj_rev = revenue * price_factor
        adj_cost = direct_cost * cost_factor
        adj_contrib = adj_rev - adj_cost
        adj_rate = adj_contrib / adj_rev
        return {
            "monthly_revenue": round(adj_rev, 2),
            "direct_cost": round(adj_cost, 2),
            "contribution": round(adj_contrib, 2),
            "contribution_rate": round(adj_rate, 6),
            "meets_target": adj_rate >= min_margin_rate - 1e-12,
        }

    max_cost_increase_pct = (
        round((cost_headroom / direct_cost) * 100.0, 2) if direct_cost > 0 else None
    )

    return {
        "title": title,
        "monthly_revenue": round(revenue, 2),
        "recurring_cost": round(recurring_total, 2),
        "upfront_total": round(upfront_total, 2),
        "amortize_months": amortize_months,
        "amortized_upfront": round(amortized_upfront, 2),
        "direct_cost": round(direct_cost, 2),
        "contribution": round(contribution, 2),
        "contribution_rate": round(contribution_rate, 6),
        "min_margin_rate": round(min_margin_rate, 6),
        "meets_target": contribution_rate >= min_margin_rate - 1e-12,
        "max_allowable_cost": round(max_allowable_cost, 2),
        "cost_headroom": round(cost_headroom, 2),
        "max_cost_increase_pct": max_cost_increase_pct,
        "operational_payback_months": payback_months,
        "sensitivity": {
            "cost_plus_15pct": scenario(1.0, 1.15),
            "cost_plus_25pct": scenario(1.0, 1.25),
            "price_minus_10pct": scenario(0.90, 1.0),
        },
        "unknowns": list(plan.get("unknowns", [])),
    }


def evaluate_economics(spec: dict) -> dict:
    """Evaluate unit economics across one or more candidate product plans."""
    if not isinstance(spec, dict):
        raise ValueError("Economics specification must be a JSON object")
    min_margin = _positive_number(spec.get("min_margin_rate", 0.0), "min_margin_rate", allow_zero=True)
    if min_margin >= 1.0:
        raise ValueError("min_margin_rate must be less than 1.0")

    plans = spec.get("plans")
    if not isinstance(plans, dict) or not plans:
        raise ValueError("Economics specification requires a non-empty 'plans' object")

    results = {
        plan_id: evaluate_plan(plan_data, min_margin_rate=min_margin)
        for plan_id, plan_data in plans.items()
    }
    viable = sorted(plan_id for plan_id, item in results.items() if item["meets_target"])
    return {
        "currency": str(spec.get("currency", "RUB")),
        "unit": str(spec.get("unit", "paying_unit_month")),
        "min_margin_rate": round(min_margin, 6),
        "viable_plans": viable,
        "plans": results,
    }


def evaluate_economics_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return evaluate_economics(data)
