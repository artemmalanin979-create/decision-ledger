"""Deterministic unit economics, explicit assumptions vs facts, break-even bounds, and switching thresholds."""

from __future__ import annotations

import json
import math
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
    """Compute monthly contribution margin, cost headroom, payback, stress scenarios, and switching bounds."""
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

    # Exact analytical switching / break-even thresholds to hit min_margin_rate:
    # 1) Max upfront CAC at fixed price, recurring cost, and retention:
    #    recurring_total + CAC / amortize_months <= max_allowable_cost
    max_upfront_for_target = round((max_allowable_cost - recurring_total) * amortize_months, 2)
    if max_upfront_for_target < 0:
        max_upfront_for_target = None  # Impossible even at 0 CAC because recurring_total > max_allowable_cost

    # 2) Min retention months at fixed price, recurring cost, and upfront CAC:
    #    upfront_total / L <= max_allowable_cost - recurring_total
    monthly_room_for_cac = max_allowable_cost - recurring_total
    if upfront_total == 0:
        min_retention_for_target = 1 if monthly_room_for_cac >= 0 else None
    elif monthly_room_for_cac > 0:
        min_retention_for_target = round(upfront_total / monthly_room_for_cac, 2)
    else:
        min_retention_for_target = None

    # 3) Min monthly price at fixed direct_cost to satisfy (P - direct_cost) / P >= min_margin_rate:
    min_price_for_target = (
        round(direct_cost / (1.0 - min_margin_rate), 2)
        if min_margin_rate < 1.0 else None
    )

    def scenario(price_factor: float, cost_factor: float) -> dict:
        adj_rev = revenue * price_factor
        adj_cost = direct_cost * cost_factor
        adj_contrib = adj_rev - adj_cost
        adj_rate = adj_contrib / adj_rev if adj_rev > 0 else 0.0
        return {
            "monthly_revenue": round(adj_rev, 2),
            "direct_cost": round(adj_cost, 2),
            "contribution": round(adj_contrib, 2),
            "contribution_rate": round(adj_rate, 6),
            "meets_target": adj_rate + 1e-12 >= min_margin_rate,
        }

    max_cost_increase_pct = (
        round(((max_allowable_cost / direct_cost) - 1.0) * 100.0, 2)
        if direct_cost > 0
        else None
    )

    out = {
        "title": title.strip(),
        "epistemic_basis": plan.get("epistemic_basis", "scenario_assumptions"),
        "monthly_revenue": round(revenue, 2),
        "recurring_cost": round(recurring_total, 2),
        "upfront_total": round(upfront_total, 2),
        "amortize_months": amortize_months,
        "amortized_upfront": round(amortized_upfront, 2),
        "direct_cost": round(direct_cost, 2),
        "contribution": round(contribution, 2),
        "contribution_rate": round(contribution_rate, 6),
        "min_margin_rate": round(min_margin_rate, 6),
        "meets_target": contribution_rate + 1e-12 >= min_margin_rate,
        "max_allowable_cost": round(max_allowable_cost, 2),
        "cost_headroom": round(cost_headroom, 2),
        "max_cost_increase_pct": max_cost_increase_pct,
        "operational_payback_months": payback_months,
        "breakeven_for_target_margin": {
            "max_upfront_cac": max_upfront_for_target,
            "min_retention_months": min_retention_for_target,
            "min_monthly_price": min_price_for_target,
        },
        "sensitivity": {
            "cost_plus_15pct": scenario(1.0, 1.15),
            "cost_plus_25pct": scenario(1.0, 1.25),
            "price_minus_10pct": scenario(0.90, 1.0),
        },
        "unknowns": list(plan.get("unknowns", [])),
    }
    if "assumptions" in plan and isinstance(plan["assumptions"], dict):
        out["assumptions"] = plan["assumptions"]
    return out


def evaluate_economics(spec: dict) -> dict:
    """Evaluate one or more candidate product pricing/cost plans from a structured JSON spec."""
    if not isinstance(spec, dict):
        raise ValueError("Economics spec must be a JSON object")
    currency = spec.get("currency", "RUB")
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("currency must be a non-empty string")
    unit = spec.get("unit", "paying_unit_month")
    min_margin_rate = _positive_number(spec.get("min_margin_rate", 0.30), "min_margin_rate", allow_zero=True)
    if min_margin_rate >= 1.0:
        raise ValueError("min_margin_rate must be less than 1.0")

    plans = spec.get("plans")
    if not isinstance(plans, dict) or not plans:
        raise ValueError("Economics spec requires a non-empty 'plans' object")

    results = {}
    viable = []
    switching_notes = []
    for plan_id, plan_data in plans.items():
        evaluated = evaluate_plan(plan_data, min_margin_rate=min_margin_rate)
        results[plan_id] = evaluated
        if evaluated["meets_target"]:
            viable.append(plan_id)

        be = evaluated["breakeven_for_target_margin"]
        if evaluated["meets_target"]:
            switching_notes.append({
                "plan_id": plan_id,
                "current_status": "viable",
                "flips_to_non_viable_if": {
                    "upfront_cac_exceeds": be["max_upfront_cac"],
                    "retention_months_below": be["min_retention_months"],
                    "direct_cost_exceeds": evaluated["max_allowable_cost"],
                    "monthly_price_below": be["min_monthly_price"],
                },
            })
        else:
            switching_notes.append({
                "plan_id": plan_id,
                "current_status": "below_target_margin",
                "flips_to_viable_only_if": {
                    "upfront_cac_at_most": be["max_upfront_cac"],
                    "retention_months_at_least": be["min_retention_months"],
                    "monthly_price_at_least": be["min_monthly_price"],
                },
            })

    out = {
        "currency": currency.strip(),
        "unit": unit,
        "min_margin_rate": round(min_margin_rate, 6),
        "viable_plans": sorted(viable),
        "switching_analysis": switching_notes,
        "plans": results,
    }
    if "note" in spec:
        out["note"] = spec["note"]
    if "facts_vs_assumptions" in spec:
        out["facts_vs_assumptions"] = spec["facts_vs_assumptions"]
    return out


def evaluate_economics_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return evaluate_economics(data)


PRICE_BASIS_LABELS = {
    "public_price_plus_model_assumptions": "факт витрины",
    "benchmark_price_plus_model_assumptions": "ориентир рынка",
    "scenario_hypothesis_assumptions": "**допущение**",
}


def _fmt_num(value, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "недостижимо"
    text = f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")
    if text.endswith(",00"):
        text = text[:-3]
    return f"{text}{suffix}"


def render_assumptions_markdown(report: dict) -> str:
    """Render facts vs assumptions and one-at-a-time switching thresholds as Markdown.

    Every money/retention parameter that is not a public price is labelled «допущение»;
    thresholds are exact analytical bounds of ``min_margin_rate`` with the other inputs fixed.
    """
    margin_pct = _fmt_num(report["min_margin_rate"] * 100, "%")
    lines = [
        f"Порог комитета — маржинальный вклад `>= {margin_pct}`. Цена подписки — публичный факт или рыночный ориентир; "
        "прямые затраты, CAC и срок удержания — **допущения** сценарной модели "
        "(`examples/antifraud-subscription/initial/economics_2027.json`), а не данные управленческой отчётности. "
        "Поэтому маржа каждого варианта — расчёт на допущениях, а не измеренный факт.",
        "",
        "| Вариант | Цена / 30 дн. (основание) | Рекуррентные затраты / мес. (**допущение**) | CAC (**допущение**) | Удержание, мес. (**допущение**) | Маржа (расчёт на допущениях) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for plan_id, plan in report["plans"].items():
        status = "проходит порог" if plan["meets_target"] else "ниже порога"
        basis = PRICE_BASIS_LABELS.get(plan.get("epistemic_basis", ""), "**допущение**")
        lines.append(
            f"| `{plan_id}` | {_fmt_num(plan['monthly_revenue'], ' ₽')} ({basis}) | {_fmt_num(plan['recurring_cost'], ' ₽')} | "
            f"{_fmt_num(plan['upfront_total'], ' ₽')} | {_fmt_num(plan['amortize_months'])} | "
            f"{_fmt_num(plan['contribution_rate'] * 100, '%', 1)} ({status}) |"
        )
    lines += [
        "",
        "**Пороги смены решения** (каждый параметр меняется отдельно, остальные зафиксированы):",
        "",
        "| Вариант | Статус сейчас | CAC | Удержание | Полные прямые затраты / мес. (рекуррентные + CAC в месяц) | Цена / 30 дн. |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for note in report.get("switching_analysis", []):
        plan = report["plans"][note["plan_id"]]
        be = plan["breakeven_for_target_margin"]
        if note["current_status"] == "viable":
            row = (
                "проходит",
                f"выпадает, если `> {_fmt_num(be['max_upfront_cac'], ' ₽')}`",
                f"выпадает, если `< {_fmt_num(be['min_retention_months'], ' мес.')}`",
                f"выпадает, если `> {_fmt_num(plan['max_allowable_cost'], ' ₽')}`",
                f"выпадает, если `< {_fmt_num(be['min_monthly_price'], ' ₽')}`",
            )
        else:
            cac = be["max_upfront_cac"]
            ret = be["min_retention_months"]
            row = (
                "ниже порога",
                f"проходит, если `<= {_fmt_num(cac, ' ₽')}`" if cac is not None else "не спасает (рекуррентные затраты выше допустимых)",
                f"проходит, если `>= {_fmt_num(ret, ' мес.')}`" if ret is not None else "не спасает",
                f"проходит, если `<= {_fmt_num(plan['max_allowable_cost'], ' ₽')}`",
                f"проходит, если `>= {_fmt_num(be['min_monthly_price'], ' ₽')}`",
            )
        lines.append(f"| `{note['plan_id']}` | " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"
