"""Self-contained interactive HTML decision dashboard for product leaders."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
import re

from .claims import audit_claims
from .cost import summarize_case_cost
from .review import impact
from .router import summarize_telemetry

KIND_LABELS = {
    "fact": "Факт",
    "observation": "Наблюдение",
    "inference": "Вывод",
    "hypothesis": "Гипотеза",
    "forecast": "Прогноз",
    "unknown": "Неизвестно",
}

OPTION_TYPE_LABELS = {
    "keep": "Оставить как есть",
    "change": "Изменить продукт",
    "close": "Закрыть инициативу",
}


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n) % 100
    n1 = n_abs % 10
    if 11 <= n_abs <= 19:
        return f"{n} {many}"
    if n1 == 1:
        return f"{n} {one}"
    if 2 <= n1 <= 4:
        return f"{n} {few}"
    return f"{n} {many}"


def _format_prose_html(text: str) -> str:
    escaped = escape(text).replace("\n", "<br/>")
    return re.sub(r"(?<=[.;:])\s+(?=\d+\)\s)", "<br/>", escaped)


def _clip_words(text: str, max_chars: int = 240) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut}…"


def render_dashboard_html(case: Path, state: dict, sources_health: dict) -> str:
    decisions = state.get("decisions", [])
    latest_decision = decisions[-1] if decisions else None
    draft = state.get("draft")
    active_proposal = draft or (latest_decision["proposal"] if latest_decision else None)

    active_impact = impact(active_proposal, sources_health) if active_proposal else None
    approved_impact = impact(latest_decision["proposal"], sources_health) if latest_decision else None
    claims_report = audit_claims(active_proposal, sources_health) if active_proposal else None
    telemetry = summarize_telemetry(state.get("telemetry", []))
    cost_summary = summarize_case_cost(case, state.get("telemetry", []))

    has_stale = bool(approved_impact and (approved_impact["sources"] or approved_impact["unreviewed"]))
    if draft and latest_decision:
        banner_state = f"Черновик пересмотра (после Решения №{latest_decision['revision']})"
        banner_tone = "warn"
    elif draft:
        banner_state = "Рекомендация на согласовании руководителя"
        banner_tone = "neutral"
    elif latest_decision and has_stale:
        banner_state = f"Решение №{latest_decision['revision']} требует пересмотра (обновились данные)"
        banner_tone = "alert"
    elif latest_decision:
        banner_state = f"Утверждено · Решение №{latest_decision['revision']}"
        banner_tone = "ok"
    else:
        banner_state = "Постановка задачи"
        banner_tone = "neutral"

    title = active_proposal["title"] if active_proposal else state["request"]
    summary = active_proposal["summary"] if active_proposal else "Исследование ещё не сформировало черновик стратегии."
    checkpoint = state.get("checkpoint", {})

    # Prepare graph relation map for interactive highlighting in browser JS
    graph_data = {"sources": {}, "findings": {}, "targets": {}}
    if active_proposal:
        findings = active_proposal["findings"]
        for fid, fdata in findings.items():
            graph_data["findings"][fid] = {
                "sources": [c["source"] for c in fdata.get("citations", [])],
                "depends_on": list(fdata.get("depends_on", [])),
            }
        for oid, odata in active_proposal["options"].items():
            graph_data["targets"][f"option:{oid}"] = list(odata.get("depends_on", []))
        for skey, sdata in active_proposal["strategy"].items():
            graph_data["targets"][f"strategy:{skey}"] = list(sdata.get("depends_on", []))

    affected_sources = set((approved_impact or active_impact or {}).get("sources", []))
    affected_findings = set((approved_impact or active_impact or {}).get("findings", []))
    affected_options = set((approved_impact or active_impact or {}).get("options", []))
    affected_strategy = set((approved_impact or active_impact or {}).get("strategy", []))

    # Build sources cards
    source_cards = []
    for sid, srec in sorted(sources_health.items()):
        health = srec["health"]
        is_changed = sid in affected_sources or health != "ok"
        cls = "node-card stale" if is_changed else "node-card"
        badge_cls = "tag tag-alert" if health in {"changed", "unavailable"} else (
            "tag tag-muted" if health == "retired" else (
                "tag tag-warn" if srec["kind"] == "synthetic" else "tag"
            )
        )
        sha_short = srec["sha"][:10]
        source_cards.append(
            f'<div class="{cls}" data-node="source:{escape(sid)}" onclick="selectNode(\'source:{escape(sid)}\')">'
            f'<div class="node-head"><span class="mono">{escape(sid)}</span>'
            f'<span class="{badge_cls}">{escape(srec["kind"])} · {escape(health)}</span></div>'
            f'<div class="node-meta">{escape(srec["path"])} · <span class="mono">{escape(sha_short)}</span></div>'
            f'</div>'
        )

    # Build findings cards
    finding_cards = []
    if active_proposal:
        for fid, fdata in active_proposal["findings"].items():
            stale_cls = " stale" if fid in affected_findings else ""
            kind = fdata["kind"]
            kind_cls = "tag-ok" if kind in {"fact", "observation"} else (
                "tag-warn" if kind in {"hypothesis", "forecast", "unknown"} else ""
            )
            refs = [f'{c["source"]}:{c["locator"]}' for c in fdata.get("citations", [])] + list(fdata.get("depends_on", []))
            ref_line = ", ".join(refs) if refs else "без ссылок"
            stale_tag = '<span class="tag tag-alert">Перепроверить</span>' if fid in affected_findings else ""
            finding_cards.append(
                f'<div class="node-card{stale_cls}" data-node="finding:{escape(fid)}" onclick="selectNode(\'finding:{escape(fid)}\')">'
                f'<div class="node-head"><span class="mono">{escape(fid)}</span>'
                f'<div class="tags"><span class="tag {kind_cls}">{escape(KIND_LABELS.get(kind, kind))}</span>{stale_tag}</div></div>'
                f'<div class="node-body">{escape(fdata["text"])}</div>'
                f'<div class="node-meta">Опора: {escape(ref_line)}</div>'
                f'</div>'
            )

    # Build options cards
    option_cards = []
    if active_proposal:
        rec = active_proposal["recommended"]
        for oid, odata in active_proposal["options"].items():
            is_rec = oid == rec
            stale_cls = " stale" if oid in affected_options else ""
            rec_cls = " selected-option" if is_rec else ""
            rec_badge = '<span class="tag tag-ok">Рекомендован</span>' if is_rec else ""
            stale_badge = '<span class="tag tag-alert">Основание изменилось</span>' if oid in affected_options else ""
            option_cards.append(
                f'<div class="node-card{rec_cls}{stale_cls}" data-node="option:{escape(oid)}" onclick="selectNode(\'option:{escape(oid)}\')">'
                f'<div class="node-head"><strong>{escape(odata["title"])}</strong>'
                f'<div class="tags"><span class="tag">{escape(OPTION_TYPE_LABELS.get(odata["type"], odata["type"]))}</span>{rec_badge}{stale_badge}</div></div>'
                f'<div class="node-body">{escape(odata["case"])}</div>'
                f'<div class="objection-line"><span>Возражение:</span> {escape(odata["objection"])}</div>'
                f'<div class="node-meta">Основания: {escape(", ".join(odata["depends_on"]))}</div>'
                f'</div>'
            )

    # Strategy blocks
    strategy_cards = []
    if active_proposal:
        for skey, slabel in (
            ("product", "Продуктовая стратегия"),
            ("communication", "Коммуникационная стратегия"),
            ("experiment", "План проверки и стоп-правила"),
        ):
            sblock = active_proposal["strategy"][skey]
            stale_cls = " stale" if skey in affected_strategy else ""
            extra = ""
            if skey == "communication" and claims_report:
                if claims_report["passed"]:
                    extra = '<div class="claim-ok">Аудит обещаний: границы применимости согласованы с продуктом</div>'
                else:
                    msgs = "; ".join(w["message"] for w in claims_report["warnings"])
                    extra = f'<div class="claim-warn">Аудит обещаний: {escape(msgs)}</div>'
            deps_list = ", ".join(sblock["depends_on"])
            deps_count = _plural_ru(len(sblock["depends_on"]), "основание", "основания", "оснований")
            strategy_cards.append(
                f'<div class="strategy-card{stale_cls}" data-node="strategy:{escape(skey)}" onclick="selectNode(\'strategy:{escape(skey)}\')">'
                f'<div class="node-head"><h3>{escape(slabel)}</h3>'
                f'<span class="tag" title="{escape(deps_list)}">{deps_count}</span></div>'
                f'<p>{_format_prose_html(sblock["text"])}</p>'
                f'<div class="node-meta" style="margin-top:8px;">Основания: {escape(deps_list)}</div>'
                f'{extra}'
                f'</div>'
            )

    # Actions table
    action_rows = []
    if active_proposal:
        completions = latest_decision.get("completions", {}) if (latest_decision and not draft) else {}
        for act in active_proposal["actions"]:
            comp = completions.get(act["id"])
            status_cell = f'Выполнено ({escape(comp["note"])})' if comp else "Открыто"
            action_rows.append(
                f'<tr><td class="mono">{escape(act["id"])}</td>'
                f'<td>{escape(act["owner"])}</td>'
                f'<td class="mono">{escape(act["due"])}</td>'
                f'<td>{escape(act["task"])}</td>'
                f'<td>{escape(act["trigger"])}</td>'
                f'<td>{status_cell}</td></tr>'
            )

    # History timeline
    history_rows = []
    for entry in decisions:
        history_rows.append(
            f'<div class="history-item">'
            f'<span class="mono">Решение №{entry["revision"]}</span>'
            f'<strong>{escape(entry["option"])}</strong>'
            f'<span>Утвердил: {escape(entry["by"])} · {escape(entry["at"])}</span>'
            f'<div class="node-meta">{escape(entry["reason"])} · снимок <span class="mono">{escape(entry["sha"][:12])}</span></div>'
            f'</div>'
        )

    # Optional methodology sections (JTBD, OST, Pre-mortem, Promise Matrix)
    methodology_html = ""
    meth = (active_proposal or {}).get("methodology") or {}
    meth_affected = set((approved_impact or active_impact or {}).get("methodology", []))
    if meth:
        meth_blocks = []
        if "jtbd" in meth:
            jtbd = meth["jtbd"]
            forces = jtbd["forces"]
            stale_cls = " stale" if "jtbd" in meth_affected else ""
            jtbd_deps = ", ".join(jtbd.get("depends_on", []))
            jtbd_count = _plural_ru(len(jtbd.get("depends_on", [])), "основание", "основания", "оснований")
            meth_blocks.append(
                f'<div class="strategy-card{stale_cls}" style="margin-bottom:14px;">'
                f'<div class="node-head"><h3>JTBD: Покупатель vs Пользователь</h3>'
                f'<span class="tag" title="{escape(jtbd_deps)}">{jtbd_count}</span></div>'
                f'<p><strong>Задача плательщика:</strong> {escape(jtbd["payer_job"])}<br/>'
                f'<strong>Задача пользователя:</strong> {escape(jtbd["user_job"])}</p>'
                f'<div class="node-meta" style="margin-top:8px;">'
                f'<strong>Толчок:</strong> {escape(forces["push"])} · '
                f'<strong>Притяжение:</strong> {escape(forces["pull"])} · '
                f'<strong>Тревога:</strong> {escape(forces["anxiety"])} · '
                f'<strong>Инерция:</strong> {escape(forces["habit"])}</div></div>'
            )
        if "ost" in meth:
            ost = meth["ost"]
            opp_cards = []
            for opp in ost["opportunities"]:
                ost_stale = " stale" if f"ost:{opp['id']}" in meth_affected else ""
                sol_items = "".join(
                    f'<li><span class="mono">{escape(s["option_id"])}</span> — тест допущения: {escape(s["assumption_test"])}</li>'
                    for s in opp["solutions"]
                )
                opp_cards.append(
                    f'<div class="node-card{ost_stale}">'
                    f'<div class="node-head"><span class="mono">{escape(opp["id"])}</span>'
                    f'<span class="tag">{_plural_ru(len(opp["depends_on"]), "основание", "основания", "оснований")}</span></div>'
                    f'<div class="node-body"><strong>{escape(opp["statement"])}</strong></div>'
                    f'<ul style="margin:6px 0 8px 18px;padding:0;font-size:12px;color:var(--muted);">{sol_items}</ul>'
                    f'<div class="node-meta">Опора: {escape(", ".join(opp["depends_on"]))}</div></div>'
                )
            meth_blocks.append(
                f'<div style="margin-bottom:14px;">'
                f'<div class="col-title"><span>Opportunity Solution Tree · Исход: {escape(ost["outcome"])}</span></div>'
                f'<div class="strategy-grid">{"".join(opp_cards)}</div></div>'
            )
        if "promise_matrix" in meth:
            pm_rows = []
            for idx, r in enumerate(meth["promise_matrix"], 1):
                row_stale = ' style="background:#FFFDFD;"' if f"promise_matrix:{idx}" in meth_affected else ""
                pm_rows.append(
                    f'<tr{row_stale}><td><strong>{escape(r["promise"])}</strong></td>'
                    f'<td>{escape(r["capability"])}</td>'
                    f'<td class="mono">{escape(", ".join(r["proof_findings"]))}</td>'
                    f'<td>{escape(r["boundary"])}</td>'
                    f'<td>{escape(r["channel"])}</td></tr>'
                )
            meth_blocks.append(
                f'<div style="margin-bottom:14px;">'
                f'<div class="col-title"><span>Матрица «Обещание коммуникации ↔ Возможность продукта»</span></div>'
                f'<table><thead><tr><th>Обещание аудитории</th><th>Механизм продукта</th><th>Основания</th><th>Честная граница</th><th>Канал</th></tr></thead>'
                f'<tbody>{"".join(pm_rows)}</tbody></table></div>'
            )
        if "premortem" in meth:
            pre_rows = []
            for idx, r in enumerate(meth["premortem"], 1):
                row_stale = ' style="background:#FFFDFD;"' if f"premortem:{idx}" in meth_affected else ""
                pre_rows.append(
                    f'<tr{row_stale}><td>{escape(r["failure_mode"])}</td>'
                    f'<td class="mono">{escape(r["probability"])}</td>'
                    f'<td class="mono">{escape(r["impact"])}</td>'
                    f'<td class="mono">{escape(r["preventability"])}</td>'
                    f'<td>{escape(r["mitigation"])}</td></tr>'
                )
            meth_blocks.append(
                f'<div style="margin-bottom:14px;">'
                f'<div class="col-title"><span>Pre-mortem выбранного варианта (через 6 месяцев)</span></div>'
                f'<table><thead><tr><th>Сценарий провала</th><th>Вероятность</th><th>Влияние</th><th>Предотвратимость</th><th>Защитная мера / стоп-правило</th></tr></thead>'
                f'<tbody>{"".join(pre_rows)}</tbody></table></div>'
            )
        methodology_html = f'<h2>Продуктовая методология и контроль рисков</h2>{"".join(meth_blocks)}'

    if cost_summary["steps"] > 0:
        est_prefix = "Оценка API: " if cost_summary.get("estimated") else "API: "
        human_part = (
            f'чел: {cost_summary["human_minutes"]} мин'
            if cost_summary.get("human_minutes") is not None
            else "чел: не измерялось"
        )
        if cost_summary.get("api_cost_rub") is not None:
            money_part = f'{est_prefix}{cost_summary["api_cost_rub"]:.2f} ₽ (${cost_summary["api_cost_usd"]:.3f})'
        elif cost_summary.get("api_cost_usd") is not None:
            money_part = f'Оценка CLI из лога: ${cost_summary["api_cost_usd"]:.4f} (квота подписки, ₽ не списывались)'
        else:
            money_part = "₽ не рассчитываются (квота подписки)"
        tokens_part = (
            f'{cost_summary["input_tokens"]:,} вх. ({cost_summary.get("uncached_input_tokens", 0):,} нов. + {cost_summary["cached_tokens"]:,} кэш) / {cost_summary["output_tokens"]:,} вых. токенов'.replace(",", " ")
            if cost_summary.get("input_tokens") else ""
        )
        prov_badge = (
            '<span class="tag tag-ok" style="margin-right:6px;">Логи CLI (verified)</span>'
            if cost_summary.get("telemetry_verified")
            else '<span class="tag tag-alert" style="margin-right:6px;">Самоотчёт модели (не верифицировано)</span>'
        )
        cost_card_val = " · ".join(
            part for part in (tokens_part, f'{cost_summary["wall_seconds"]} с', money_part, human_part) if part
        )
        method_note = "; ".join(cost_summary.get("methods") or [])
        cost_method_html = (
            f'<div class="node-meta" style="margin-top:5px;">{prov_badge}{escape(_clip_words(method_note))}</div>'
            if method_note
            else f'<div class="node-meta" style="margin-top:5px;">{prov_badge}</div>'
        )
    else:
        cost_card_val = f'{_plural_ru(telemetry["events"], "шаг", "шага", "шагов")} · {telemetry["total_seconds"]} с · {_plural_ru(telemetry["tool_calls"], "вызов", "вызова", "вызовов")} CLI'
        cost_method_html = ""

    impact_banner = ""
    if approved_impact and (approved_impact["sources"] or approved_impact["unreviewed"]):
        impact_banner = (
            f'<section class="impact-banner">'
            f'<div><strong>Обнаружено обновление входных данных после Решения №{latest_decision["revision"] if latest_decision else 1}</strong>'
            f'<p>Изменившиеся источники: <span class="mono">{escape(", ".join(approved_impact["sources"]) or "нет")}</span> · '
            f'Новые источники: <span class="mono">{escape(", ".join(approved_impact["unreviewed"]) or "нет")}</span> · '
            f'Затронуто транзитивно: {_plural_ru(len(approved_impact["findings"]), "основание", "основания", "оснований")}, '
            f'{_plural_ru(len(approved_impact["options"]), "вариант", "варианта", "вариантов")}, '
            f'{_plural_ru(len(approved_impact["strategy"]), "блок", "блока", "блоков")} стратегии.</p></div>'
            f'</section>'
        )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{escape(title)} — Decision Ledger</title>
<style>
  :root {{
    --bg: #F8FAFC;
    --surface: #FFFFFF;
    --ink: #0F172A;
    --muted: #475569;
    --subtle: #64748B;
    --line: #E2E8F0;
    --accent: #0F766E;
    --accent-soft: #F0FDFA;
    --warn: #B45309;
    --warn-soft: #FFFBEB;
    --alert: #B91C1C;
    --alert-soft: #FEF2F2;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 32px 40px 56px;
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--ink);
    line-height: 1.5;
    font-size: 14px;
    font-variant-numeric: tabular-nums;
  }}
  .container {{ max-width: 1360px; margin: 0 auto; }}
  .topbar {{
    display: flex; justify-content: space-between; align-items: flex-start; gap: 24px;
    padding-bottom: 20px; border-bottom: 1px solid var(--line); margin-bottom: 24px;
  }}
  .eyebrow {{
    font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--accent); margin-bottom: 6px;
  }}
  h1 {{
    margin: 0 0 8px;
    font-family: "Inter Display", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 26px; line-height: 1.2; letter-spacing: -0.02em;
  }}
  h2 {{
    margin: 28px 0 12px;
    font-family: "Inter Display", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 16px; letter-spacing: -0.01em;
  }}
  h3 {{ margin: 0; font-size: 14px; }}
  .summary {{ margin: 0; color: var(--muted); max-width: 840px; font-size: 14px; }}
  .status-pill {{
    padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;
    border: 1px solid var(--line); background: var(--surface); white-space: nowrap;
  }}
  .status-ok {{ border-color: #99F6E4; background: var(--accent-soft); color: var(--accent); }}
  .status-warn {{ border-color: #FDE68A; background: var(--warn-soft); color: var(--warn); }}
  .status-alert {{ border-color: #FECACA; background: var(--alert-soft); color: var(--alert); }}
  .meta-strip {{
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 20px;
  }}
  .metric-card {{
    background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px;
  }}
  .metric-label {{ font-size: 11px; color: var(--subtle); text-transform: uppercase; letter-spacing: 0.05em; }}
  .metric-value {{ font-size: 14px; font-weight: 600; margin-top: 4px; }}
  .impact-banner {{
    background: var(--alert-soft); border: 1px solid #FECACA; border-radius: 8px;
    padding: 14px 16px; margin-bottom: 20px; color: #7F1D1D;
  }}
  .impact-banner p {{ margin: 4px 0 0; font-size: 13px; }}
  .lineage-grid {{
    display: grid; grid-template-columns: 0.85fr 1.25fr 1.1fr; gap: 16px; align-items: start;
  }}
  .col-title {{
    font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase;
    letter-spacing: 0.05em; margin-bottom: 10px; display: flex; justify-content: space-between;
  }}
  .node-card {{
    background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
    padding: 11px 13px; margin-bottom: 10px; cursor: pointer; transition: border-color 0.12s ease, opacity 0.12s ease;
  }}
  .node-card:hover {{ border-color: #94A3B8; }}
  .node-card.selected-option {{ border-left: 3px solid var(--accent); }}
  .node-card.stale, .strategy-card.stale {{ border-color: #FCA5A5; background: #FFFDFD; }}
  .node-card.active-link, .strategy-card.active-link {{ border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }}
  .node-card.dimmed, .strategy-card.dimmed {{ opacity: 0.35; }}
  .node-head {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 6px; }}
  .node-body {{ font-size: 13px; color: var(--ink); margin-bottom: 6px; }}
  .objection-line {{ font-size: 12px; color: var(--muted); border-top: 1px solid var(--line); padding-top: 6px; margin-bottom: 6px; }}
  .objection-line span {{ font-weight: 600; color: var(--ink); }}
  .node-meta {{ font-size: 11px; color: var(--subtle); }}
  .tags {{ display: flex; gap: 5px; flex-wrap: wrap; }}
  .tag {{
    font-size: 11px; padding: 1px 7px; border-radius: 4px; border: 1px solid var(--line);
    color: var(--muted); background: #F8FAFC;
  }}
  .tag-ok {{ border-color: #99F6E4; background: var(--accent-soft); color: var(--accent); }}
  .tag-warn {{ border-color: #FDE68A; background: var(--warn-soft); color: var(--warn); }}
  .tag-alert {{ border-color: #FECACA; background: var(--alert-soft); color: var(--alert); }}
  .strategy-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }}
  .strategy-card {{
    background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 16px; cursor: pointer;
  }}
  .strategy-card p {{ margin: 10px 0 0; font-size: 13px; color: var(--muted); }}
  .claim-ok {{ margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--line); font-size: 12px; color: var(--accent); font-weight: 500; }}
  .claim-warn {{ margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--line); font-size: 12px; color: var(--alert); font-weight: 500; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); font-size: 13px; vertical-align: top; }}
  th {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--subtle); background: #F8FAFC; }}
  .history-list {{ display: grid; gap: 8px; }}
  .history-item {{
    background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px;
    display: flex; flex-wrap: wrap; gap: 12px; align-items: baseline; font-size: 13px;
  }}
  .mono {{ font-family: "Source Code Pro", "DejaVu Sans Mono", monospace; font-size: 12px; }}
  .reset-btn {{
    border: 1px solid var(--line); background: var(--surface); color: var(--muted);
    border-radius: 5px; padding: 2px 8px; font-size: 11px; cursor: pointer;
  }}
</style>
</head>
<body>
<div class="container">
  <header class="topbar">
    <div>
      <div class="eyebrow">Decision Ledger · Паспорт продуктового решения · {escape(case.name)}</div>
      <h1>{escape(title)}</h1>
      <p class="summary">{_format_prose_html(summary)}</p>
    </div>
    <div class="status-pill status-{banner_tone}">{escape(banner_state)}</div>
  </header>

  <section class="meta-strip">
    <div class="metric-card">
      <div class="metric-label">Исходный запрос</div>
      <div class="metric-value">{escape(state["request"])}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Контрольная точка ({escape(checkpoint.get("stage", "intake"))})</div>
      <div class="metric-value">{escape(checkpoint.get("next", "—"))}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Источники и ревизии</div>
      <div class="metric-value">{_plural_ru(len(sources_health), "источник", "источника", "источников")} · {_plural_ru(len(decisions), "утверждённое решение", "утверждённых решения", "утверждённых решений")}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Затраты и участие человека{" · ОЦЕНКА (ESTIMATED)" if cost_summary.get("estimated") else ""}</div>
      <div class="metric-value">{escape(cost_card_val)}</div>
      {cost_method_html}
    </div>
  </section>

  {impact_banner}

  <div style="display:flex;justify-content:space-between;align-items:center;">
    <h2>Трассировка оснований (кликните узел для подсветки зависимостей)</h2>
    <button class="reset-btn" onclick="resetSelection()">Сбросить фильтр</button>
  </div>
  <section class="lineage-grid">
    <div>
      <div class="col-title"><span>1. Источники ({len(source_cards)})</span></div>
      {"".join(source_cards) or '<div class="node-meta">Источники ещё не зарегистрированы.</div>'}
    </div>
    <div>
      <div class="col-title"><span>2. Утверждения и гипотезы ({len(finding_cards)})</span></div>
      {"".join(finding_cards) or '<div class="node-meta">Черновик ещё не создан.</div>'}
    </div>
    <div>
      <div class="col-title"><span>3. Альтернативы комитета ({len(option_cards)})</span></div>
      {"".join(option_cards) or '<div class="node-meta">Варианты ещё не сформированы.</div>'}
    </div>
  </section>

  <h2>Согласованная продуктовая и коммуникационная стратегия</h2>
  <section class="strategy-grid">
    {"".join(strategy_cards) or '<div class="node-meta">Стратегия появится после этапа propose.</div>'}
  </section>

  {methodology_html}

  <h2>Обязательства команды и триггеры пересмотра</h2>
  <table>
    <thead>
      <tr><th>ID</th><th>Владелец</th><th>Срок</th><th>Задача</th><th>Условие пересмотра</th><th>Статус</th></tr>
    </thead>
    <tbody>
      {"".join(action_rows) or '<tr><td colspan="6">Обязательства не назначены.</td></tr>'}
    </tbody>
  </table>

  <h2>История принятых решений</h2>
  <section class="history-list">
    {"".join(history_rows) or '<div class="node-meta">Утверждённых решений пока нет.</div>'}
  </section>
</div>
<script>
const GRAPH = {json.dumps(graph_data, ensure_ascii=False)};
function relatedSet(nodeId) {{
  const rel = new Set([nodeId]);
  if (nodeId.startsWith("source:")) {{
    const sid = nodeId.slice(7);
    let changed = true;
    while (changed) {{
      changed = false;
      for (const [fid, f] of Object.entries(GRAPH.findings)) {{
        const key = "finding:" + fid;
        if (rel.has(key)) continue;
        if (f.sources.includes(sid) || f.depends_on.some(d => rel.has("finding:" + d))) {{
          rel.add(key); changed = true;
        }}
      }}
    }}
    for (const [tid, deps] of Object.entries(GRAPH.targets)) {{
      if (deps.some(d => rel.has("finding:" + d))) rel.add(tid);
    }}
  }} else if (nodeId.startsWith("finding:")) {{
    const start = nodeId.slice(8);
    // Upstream
    const up = [start];
    while (up.length) {{
      const cur = up.pop();
      const f = GRAPH.findings[cur];
      if (!f) continue;
      f.sources.forEach(s => rel.add("source:" + s));
      f.depends_on.forEach(d => {{ if (!rel.has("finding:" + d)) {{ rel.add("finding:" + d); up.push(d); }} }});
    }}
    // Downstream
    const down = new Set([start]);
    let changed = true;
    while (changed) {{
      changed = false;
      for (const [fid, f] of Object.entries(GRAPH.findings)) {{
        if (!down.has(fid) && f.depends_on.some(d => down.has(d))) {{
          down.add(fid); rel.add("finding:" + fid); changed = true;
        }}
      }}
    }}
    for (const [tid, deps] of Object.entries(GRAPH.targets)) {{
      if (deps.some(d => down.has(d))) rel.add(tid);
    }}
  }} else {{
    const deps = GRAPH.targets[nodeId] || [];
    const stack = [...deps];
    stack.forEach(d => rel.add("finding:" + d));
    while (stack.length) {{
      const cur = stack.pop();
      const f = GRAPH.findings[cur];
      if (!f) continue;
      f.sources.forEach(s => rel.add("source:" + s));
      f.depends_on.forEach(d => {{ if (!rel.has("finding:" + d)) {{ rel.add("finding:" + d); stack.push(d); }} }});
    }}
  }}
  return rel;
}}
function selectNode(nodeId) {{
  const rel = relatedSet(nodeId);
  document.querySelectorAll("[data-node]").forEach(el => {{
    const id = el.getAttribute("data-node");
    el.classList.toggle("active-link", rel.has(id));
    el.classList.toggle("dimmed", !rel.has(id));
  }});
}}
function resetSelection() {{
  document.querySelectorAll("[data-node]").forEach(el => {{
    el.classList.remove("active-link", "dimmed");
  }});
}}
</script>
</body>
</html>
"""


def write_dashboard(case: Path, state: dict, sources_health: dict, output: Path | None = None) -> Path:
    html = render_dashboard_html(case, state, sources_health)
    target = output.resolve() if output else (case / "dashboard.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target
