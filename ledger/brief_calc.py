"""Deterministic product-brief calculator: 14-day activation (including unanswered invites),
available-market coverage, gross-profit bridge at 500 RUB per extra paid number-month,
calendar launch-month caps, pilot pacing, and 5-row Base/Plan scorecard.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .stats import wilson_interval


def _non_neg_float(val, name: str) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ValueError(f"{name} must be a number")
    f = float(val)
    if not math.isfinite(f) or f < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return f


def _non_neg_int(val, name: str) -> int:
    if isinstance(val, bool) or not isinstance(val, int) or val < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return val


def evaluate_activation_14d(spec: dict) -> dict:
    """Compute 14-day group activation strictly with unanswered/declined invites in denominator."""
    created = _non_neg_int(spec.get("created_groups", 0), "created_groups")
    accepted = _non_neg_int(spec.get("accepted_invite_groups", 0), "accepted_invite_groups")
    activated = _non_neg_int(spec.get("activated_within_14d_groups", 0), "activated_within_14d_groups")
    if created == 0:
        raise ValueError("created_groups must be positive")
    if accepted > created:
        raise ValueError("accepted_invite_groups cannot exceed created_groups")
    if activated > accepted:
        raise ValueError("activated_within_14d_groups cannot exceed accepted_invite_groups")

    unanswered_or_declined = created - accepted
    rate_brief = activated / created
    invite_accept_rate = accepted / created
    action_given_accept = (activated / accepted) if accepted > 0 else 0.0
    ci_low, ci_high = wilson_interval(activated, created)
    target_rate = _non_neg_float(spec.get("target_rate", 0.60), "target_rate")
    min_activated_for_target = math.ceil(created * target_rate - 1e-12)

    return {
        "created_groups_denominator": created,
        "accepted_invite_groups": accepted,
        "unanswered_or_declined_groups_in_denominator": unanswered_or_declined,
        "activated_within_14d_numerator": activated,
        "activation_14d_rate": round(rate_brief, 6),
        "activation_14d_pct": round(rate_brief * 100.0, 2),
        "invite_acceptance_rate": round(invite_accept_rate, 6),
        "conditional_action_rate_given_accepted": round(action_given_accept, 6),
        "naive_rate_excluding_unanswered_warning": round(action_given_accept, 6),
        "ci95_low": round(ci_low, 6),
        "ci95_high": round(ci_high, 6),
        "target_rate": round(target_rate, 6),
        "meets_target": rate_brief + 1e-12 >= target_rate,
        "additional_activated_needed_for_target": max(0, min_activated_for_target - activated),
    }


def evaluate_segment_balance(spec: dict, label: str) -> dict:
    """Reconcile start + organic + incremental additions/churn savings without double-counting."""
    market_total = _non_neg_int(spec["market_total"], f"{label}.market_total")
    available = _non_neg_int(spec["available_base"], f"{label}.available_base")
    if available == 0 or available > market_total:
        raise ValueError(f"{label}.available_base must be in 1..market_total")

    start_conn = _non_neg_int(spec["start_connected"], f"{label}.start_connected")
    no_change_end = _non_neg_int(spec["no_change_end_connected"], f"{label}.no_change_end_connected")
    target_end = _non_neg_int(spec["target_end_connected"], f"{label}.target_end_connected")

    inc_new = _non_neg_int(spec.get("incremental_new_connected", 0), f"{label}.incremental_new_connected")
    inc_saved = _non_neg_int(spec.get("incremental_churn_saved", 0), f"{label}.incremental_churn_saved")
    spend_rub = _non_neg_float(spec.get("acquisition_spend_rub", 0.0), f"{label}.acquisition_spend_rub")

    organic_net = no_change_end - start_conn
    incremental_total = inc_new + inc_saved
    projected_end = no_change_end + incremental_total
    if projected_end > available:
        raise ValueError(f"{label}: projected_end ({projected_end}) exceeds available_base ({available})")

    inc_cac = round(spend_rub / incremental_total, 2) if (spend_rub > 0 and incremental_total > 0) else (0.0 if spend_rub == 0 else None)

    out = {
        "market_total": market_total,
        "available_base": available,
        "start_connected": start_conn,
        "no_change_end_connected": no_change_end,
        "organic_net_additions": organic_net,
        "incremental_new_connected": inc_new,
        "incremental_churn_saved": inc_saved,
        "incremental_vs_no_change": incremental_total,
        "projected_end_connected": projected_end,
        "target_end_connected": target_end,
        "meets_target": projected_end >= target_end,
        "gap_vs_target": projected_end - target_end,
        "share_of_available_base_pct": round((projected_end / available) * 100.0, 2),
        "acquisition_spend_rub": round(spend_rub, 2),
        "incremental_cost_per_added_unit_rub": inc_cac,
    }

    if "no_change_active_last_month" in spec:
        nc_active = _non_neg_int(spec["no_change_active_last_month"], f"{label}.no_change_active_last_month")
        target_active = _non_neg_int(spec["target_active_last_month"], f"{label}.target_active_last_month")
        base_share = nc_active / no_change_end if no_change_end > 0 else 0.0
        proj_share = _non_neg_float(spec.get("projected_active_share", base_share), f"{label}.projected_active_share")
        if proj_share > 1.0:
            raise ValueError(f"{label}.projected_active_share cannot exceed 1.0")
        proj_active = int(round(projected_end * proj_share))
        from_base_growth = round((projected_end - no_change_end) * base_share, 1)
        from_rate_lift = round(projected_end * (proj_share - base_share), 1)
        out["active_last_month"] = {
            "no_change_active": nc_active,
            "no_change_active_share_pct": round(base_share * 100.0, 2),
            "projected_active_share_pct": round(proj_share * 100.0, 2),
            "projected_active": proj_active,
            "target_active": target_active,
            "meets_target": proj_active >= target_active,
            "gap_vs_target": proj_active - target_active,
            "lift_from_connected_growth": from_base_growth,
            "lift_from_engagement_share": from_rate_lift,
        }
    return out


def evaluate_gross_profit_bridge(spec: dict) -> dict:
    """Compute annual gross-profit bridge from 279M RUB baseline, enforcing calendar month caps."""
    no_change_gp = _non_neg_float(spec.get("no_change_gross_profit_rub", 279_000_000), "no_change_gross_profit_rub")
    target_gp = _non_neg_float(spec.get("target_gross_profit_rub", 285_000_000), "target_gross_profit_rub")
    rate_per_month = _non_neg_float(spec.get("margin_per_extra_paid_month_rub", 500.0), "margin_per_extra_paid_month_rub")
    if rate_per_month <= 0:
        raise ValueError("margin_per_extra_paid_month_rub must be positive")

    cohorts = spec.get("retention_cohorts") or []
    total_in_year_months = 0.0
    total_carryover_months = 0.0
    cohort_rows = []
    for idx, c in enumerate(cohorts, 1):
        name = str(c.get("name") or f"cohort_{idx}")
        launch_month = int(c.get("launch_month", 1))
        if not 1 <= launch_month <= 12:
            raise ValueError(f"Cohort {name!r}: launch_month must be in 1..12")
        max_months_in_year_per_number = 13 - launch_month
        retained_numbers = _non_neg_float(c.get("retained_numbers", 0), f"{name}.retained_numbers")
        avg_extra_months_in_year = _non_neg_float(
            c.get("avg_extra_months_in_plan_year", 0), f"{name}.avg_extra_months_in_plan_year"
        )
        if avg_extra_months_in_year > max_months_in_year_per_number + 1e-9:
            raise ValueError(
                f"Cohort {name!r} launches in month {launch_month} and can yield at most "
                f"{max_months_in_year_per_number} extra paid months per number in the plan year "
                f"(got {avg_extra_months_in_year})"
            )
        avg_carryover = _non_neg_float(c.get("avg_extra_months_next_years", 0.0), f"{name}.avg_extra_months_next_years")
        in_year_m = retained_numbers * avg_extra_months_in_year
        carry_m = retained_numbers * avg_carryover
        total_in_year_months += in_year_m
        total_carryover_months += carry_m
        cohort_rows.append({
            "name": name,
            "launch_month": launch_month,
            "max_possible_months_in_year": max_months_in_year_per_number,
            "retained_numbers": round(retained_numbers, 1),
            "avg_extra_months_in_plan_year": round(avg_extra_months_in_year, 2),
            "extra_paid_months_in_plan_year": round(in_year_m, 1),
            "gross_profit_in_plan_year_rub": round(in_year_m * rate_per_month, 2),
            "carryover_months_next_years": round(carry_m, 1),
            "carryover_gross_profit_next_years_rub": round(carry_m * rate_per_month, 2),
        })

    other_gp = _non_neg_float(spec.get("other_gross_profit_rub", 0.0), "other_gross_profit_rub")
    direct_opex = _non_neg_float(spec.get("new_direct_opex_rub", 0.0), "new_direct_opex_rub")

    gp_from_retention = total_in_year_months * rate_per_month
    net_delta_gp = gp_from_retention + other_gp - direct_opex
    projected_gp = no_change_gp + net_delta_gp
    required_gap = max(0.0, target_gp - no_change_gp)

    min_months_zero_opex = math.ceil(required_gap / rate_per_month)
    min_months_with_opex = math.ceil(max(0.0, required_gap + direct_opex - other_gp) / rate_per_month)

    capex_pm = spec.get("capex_person_months") or {}
    total_pm = sum(_non_neg_float(v, f"capex_person_months.{k}") for k, v in capex_pm.items())
    pm_rate = spec.get("person_month_cost_rub")
    capex_rub = round(total_pm * _non_neg_float(pm_rate, "person_month_cost_rub"), 2) if pm_rate is not None else None

    return {
        "no_change_gross_profit_rub": round(no_change_gp, 2),
        "target_gross_profit_rub": round(target_gp, 2),
        "required_gap_vs_no_change_rub": round(required_gap, 2),
        "margin_per_extra_paid_month_rub": round(rate_per_month, 2),
        "cohorts": cohort_rows,
        "extra_paid_number_months_in_plan_year": round(total_in_year_months, 1),
        "gross_profit_from_retention_rub": round(gp_from_retention, 2),
        "other_gross_profit_rub": round(other_gp, 2),
        "new_direct_opex_rub": round(direct_opex, 2),
        "net_incremental_gross_profit_rub": round(net_delta_gp, 2),
        "projected_gross_profit_rub": round(projected_gp, 2),
        "projected_gross_profit_mln_rub": round(projected_gp / 1_000_000.0, 2),
        "meets_target": projected_gp + 1e-6 >= target_gp,
        "gap_vs_target_rub": round(projected_gp - target_gp, 2),
        "breakeven_extra_paid_months_zero_opex": min_months_zero_opex,
        "breakeven_extra_paid_months_with_opex": min_months_with_opex,
        "carryover_next_years_gross_profit_rub": round(total_carryover_months * rate_per_month, 2),
        "capex_separate": {
            "total_person_months": round(total_pm, 2),
            "person_month_cost_rub": pm_rate,
            "capex_total_rub": capex_rub,
            "max_capex_for_plan_year_payback_rub": round(max(0.0, net_delta_gp), 2),
            "max_person_month_rate_for_plan_year_payback_rub": (
                round(max(0.0, net_delta_gp) / total_pm, 2) if total_pm > 0 else None
            ),
        },
    }


def evaluate_pilot_pacing(spec: dict) -> dict:
    """Check Q2 pilot sample recruitment speed, total duration, and expected Wilson precision."""
    weekly_flow = _non_neg_float(spec["weekly_eligible_units"], "weekly_eligible_units")
    weeks = _non_neg_float(spec["recruitment_weeks"], "recruitment_weeks")
    obs_days = _non_neg_int(spec["observation_days"], "observation_days")
    ctrl_rate = _non_neg_float(spec["control_event_rate"], "control_event_rate")
    test_rate = _non_neg_float(spec["target_test_event_rate"], "target_test_event_rate")
    if ctrl_rate > 1.0 or test_rate > 1.0 or weekly_flow <= 0 or weeks <= 0:
        raise ValueError("Invalid pilot pacing parameters")

    total_units = int(math.floor(weekly_flow * weeks))
    per_arm = total_units // 2
    if per_arm < 1:
        raise ValueError("Pilot recruitment yields fewer than 1 unit per arm")

    exp_ctrl_events = int(round(per_arm * ctrl_rate))
    exp_test_events = int(round(per_arm * test_rate))
    c_lo, c_hi = wilson_interval(exp_ctrl_events, per_arm)
    t_lo, t_hi = wilson_interval(exp_test_events, per_arm)

    abs_diff_pp = round((test_rate - ctrl_rate) * 100.0, 2)
    rel_change_pct = round(((test_rate - ctrl_rate) / ctrl_rate) * 100.0, 2) if ctrl_rate > 0 else None
    calendar_days = round(weeks * 7.0 + obs_days, 1)

    return {
        "weekly_eligible_units": weekly_flow,
        "recruitment_weeks": weeks,
        "observation_days": obs_days,
        "total_calendar_days_until_readout": calendar_days,
        "fits_within_single_quarter_90d": calendar_days <= 90.0,
        "total_recruited_units": total_units,
        "units_per_arm": per_arm,
        "control_event_rate_pct": round(ctrl_rate * 100.0, 2),
        "target_test_event_rate_pct": round(test_rate * 100.0, 2),
        "absolute_effect_pp": abs_diff_pp,
        "relative_effect_pct": rel_change_pct,
        "expected_control_wilson_95_pct": [round(c_lo * 100.0, 2), round(c_hi * 100.0, 2)],
        "expected_test_wilson_95_pct": [round(t_lo * 100.0, 2), round(t_hi * 100.0, 2)],
        "intervals_separate_at_expected_rates": (t_hi < c_lo) if test_rate < ctrl_rate else (t_lo > c_hi),
    }


def evaluate_brief_model(spec: dict) -> dict:
    """Evaluate main and adverse scenarios of a 5-part telecom product brief."""
    if not isinstance(spec, dict) or "scenarios" not in spec:
        raise ValueError("Brief calc spec requires a 'scenarios' object (e.g. 'main' and 'adverse')")

    pilot = evaluate_pilot_pacing(spec["pilot"]) if "pilot" in spec else None
    scenarios_out = {}
    for sc_name, sc_data in spec["scenarios"].items():
        act = evaluate_activation_14d(sc_data["activation_q4"])
        fam = evaluate_segment_balance(sc_data["families"], f"{sc_name}.families")
        own = evaluate_segment_balance(sc_data["multi_sim_owners"], f"{sc_name}.multi_sim_owners")
        gp = evaluate_gross_profit_bridge(sc_data["gross_profit"])
        scenarios_out[sc_name] = {
            "title": str(sc_data.get("title") or sc_name),
            "activation_q4": act,
            "families": fam,
            "multi_sim_owners": own,
            "gross_profit": gp,
        }

    return {
        "baseline_reference": spec.get("baseline_reference") or {},
        "pilot_design": pilot,
        "scenarios": scenarios_out,
    }


def evaluate_brief_file(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return evaluate_brief_model(data)


def _fmt_int(n: float | int) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


def render_brief_calc_markdown(report: dict) -> str:
    """Render ready-to-embed 5-row Base/Plan scorecard and profit bridge as Markdown."""
    sc = report["scenarios"]
    main = sc.get("main") or next(iter(sc.values()))
    adv = sc.get("adverse")
    base_ref = report.get("baseline_reference") or {
        "gp_base_plan": "282", "gp_base_est": "275",
        "act_base_plan": "5 000", "act_base_est": "4 000",
        "fam_base_plan": "7 500", "fam_base_est": "6 500",
        "own_base_plan": "1 000", "own_base_est": "800",
        "act14_base_plan": "50%", "act14_base_est": "42%",
    }

    def _cell_pair(m_val: str, a_val: str | None) -> str:
        return f"**{m_val}** / {a_val}" if a_val is not None else f"**{m_val}**"

    gp_m = f"{main['gross_profit']['projected_gross_profit_mln_rub']:.2f}".replace(".", ",")
    gp_a = f"{adv['gross_profit']['projected_gross_profit_mln_rub']:.2f}".replace(".", ",") if adv else None

    act_m = _fmt_int(main["families"]["active_last_month"]["projected_active"])
    act_a = _fmt_int(adv["families"]["active_last_month"]["projected_active"]) if adv else None

    fam_m = _fmt_int(main["families"]["projected_end_connected"])
    fam_a = _fmt_int(adv["families"]["projected_end_connected"]) if adv else None

    own_m = _fmt_int(main["multi_sim_owners"]["projected_end_connected"])
    own_a = _fmt_int(adv["multi_sim_owners"]["projected_end_connected"]) if adv else None

    a14_m = f"{main['activation_q4']['activation_14d_pct']:.1f}%".replace(".0%", "%").replace(".", ",")
    a14_a = f"{adv['activation_q4']['activation_14d_pct']:.1f}%".replace(".0%", "%").replace(".", ",") if adv else None

    hdr_forecast = "Прогноз: Основной / Неблагоприятный (допущения)" if adv else "Прогноз команды (допущения)"
    lines = [
        "| Показатель | Базовый: План | Базовый: Оценка | Плановый: Без изменений | Плановый: Цель, не ниже | " + hdr_forecast + " |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| Валовая прибыль за год, млн руб. | {base_ref['gp_base_plan']} | {base_ref['gp_base_est']} | 279 | 285 | {_cell_pair(gp_m, gp_a)} |",
        f"| Активные семейные группы за последний месяц | {base_ref['act_base_plan']} | {base_ref['act_base_est']} | 5 000 | 8 000 | {_cell_pair(act_m, act_a)} |",
        f"| Подключённые семейные группы на конец года | {base_ref['fam_base_plan']} | {base_ref['fam_base_est']} | 8 000 | 12 000 | {_cell_pair(fam_m, fam_a)} |",
        f"| Подключённые владельцы нескольких номеров | {base_ref['own_base_plan']} | {base_ref['own_base_est']} | 1 000 | 2 000 | {_cell_pair(own_m, own_a)} |",
        f"| Группы, активированные за 14 дн. (Q4, со всеми созданными в знаменателе) | {base_ref['act14_base_plan']} | {base_ref['act14_base_est']} | 45% | 60% | {_cell_pair(a14_m, a14_a)} |",
    ]
    return "\n".join(lines) + "\n"
