"""Self-contained interactive HTML decision dashboard for product leaders (with what-if & accept gate)."""

from __future__ import annotations

import hashlib
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets

from .claims import audit_claims
from .cost import summarize_case_cost
from .economics import PRICE_BASIS_LABELS, evaluate_economics
from .review import impact
from .router import summarize_telemetry
from .store import dumps
from .strategies import _extract_economics_blocks

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


def _draft_fingerprint(proposal: dict, sequence: int) -> str:
    return hashlib.sha256(dumps([sequence, proposal]).encode("utf-8")).hexdigest()


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


def _clean_basis_label(code: str) -> str:
    raw = PRICE_BASIS_LABELS.get(code, "допущение")
    return raw.replace("**", "")


def _extract_whatif_model(case: Path, state: dict, db=None) -> dict | None:
    blocks = _extract_economics_blocks(case, state, db)
    if not blocks and case.is_dir():
        for cand in sorted(case.glob("*economics*.json")):
            if not cand.is_file():
                continue
            try:
                doc = json.loads(cand.read_text(encoding="utf-8-sig"))
                if isinstance(doc, dict) and isinstance(doc.get("plans"), dict) and doc["plans"]:
                    ev = evaluate_economics(doc)
                    blocks.append((cand.stem, doc, ev))
                    break
            except (OSError, ValueError, KeyError, TypeError):
                continue
    if not blocks:
        return None

    # Prefer the primary multi-plan economics block (largest number of plans)
    blocks_sorted = sorted(blocks, key=lambda item: len(item[2].get("plans") or {}), reverse=True)
    source_id, spec_doc, ev_doc = blocks_sorted[0]
    plans_out = []
    for pid, p_eval in ev_doc["plans"].items():
        plans_out.append({
            "id": pid,
            "title": p_eval["title"],
            "basis_code": p_eval.get("epistemic_basis", "scenario_assumptions"),
            "basis_label": _clean_basis_label(p_eval.get("epistemic_basis", "")),
            "price": float(p_eval["monthly_revenue"]),
            "recurring": float(p_eval["recurring_cost"]),
            "cac": float(p_eval["upfront_total"]),
            "retention": int(p_eval["amortize_months"]),
            "assumptions": p_eval.get("assumptions") or {},
        })

    if not plans_out:
        return None

    return {
        "source_id": source_id,
        "currency": ev_doc.get("currency", "RUB"),
        "min_margin_rate": float(ev_doc["min_margin_rate"]),
        "note": str(spec_doc.get("note") or ""),
        "plans": plans_out,
    }


def render_dashboard_html(
    case: Path,
    state: dict,
    sources_health: dict,
    *,
    db=None,
    live_api_token: str | None = None,
) -> str:
    decisions = state.get("decisions", [])
    latest_decision = decisions[-1] if decisions else None
    draft = state.get("draft")
    active_proposal = draft or (latest_decision["proposal"] if latest_decision else None)

    active_impact = impact(active_proposal, sources_health) if active_proposal else None
    approved_impact = impact(latest_decision["proposal"], sources_health) if latest_decision else None
    claims_report = audit_claims(active_proposal, sources_health) if active_proposal else None
    telemetry = summarize_telemetry(state.get("telemetry", []))
    cost_summary = summarize_case_cost(case, state.get("telemetry", []))
    whatif_model = _extract_whatif_model(case, state, db)

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

    # Build committee accept gate panel (Item 4)
    case_cli_hint = f".cases/{case.name}" if case.parent.name == ".cases" else case.name
    accept_gate_meta = {
        "has_draft": bool(draft),
        "case_cli": case_cli_hint,
        "recommended": draft["recommended"] if draft else (latest_decision["option"] if latest_decision else ""),
        "expect_sha": (
            _draft_fingerprint(draft, state.get("draft_sequence", 0))
            if draft
            else (latest_decision["sha"] if latest_decision else "")
        ),
        "live_enabled": bool(live_api_token and draft),
        "live_token": live_api_token or "",
    }
    accept_html = ""
    if active_proposal:
        opt_options_html = "".join(
            f'<option value="{escape(oid)}"{" selected" if oid == active_proposal["recommended"] else ""}>'
            f'{escape(oid)} — {escape(odata["title"])}</option>'
            for oid, odata in active_proposal["options"].items()
        )
        if draft:
            gate_badge = '<span class="tag tag-warn">Ожидает решения комитета (draft открыт)</span>'
            default_reason = f"Согласовано по итогам проверки оснований и стоп-правил ({draft['recommended']})"
            live_btn_html = (
                '<button type="button" class="action-btn action-btn-primary" id="accept-live-btn" onclick="triggerLiveAccept()">'
                'Утвердить в 1 клик (записать в SQLite)</button>'
                if live_api_token
                else '<span class="node-meta" style="align-self:center;">Для записи в 1 клик из браузера: '
                     f'<span class="mono">python3 -m ledger dashboard {escape(case_cli_hint)} --serve</span></span>'
            )
        else:
            gate_badge = (
                f'<span class="tag tag-ok">Решение №{latest_decision["revision"]} зафиксировано · '
                f'{escape(latest_decision["by"])}</span>'
            )
            default_reason = str(latest_decision["reason"]) if latest_decision else "Согласовано комитетом"
            live_btn_html = (
                f'<span class="node-meta" style="align-self:center;">Зафиксировано {escape(latest_decision["at"])} · '
                f'SHA <span class="mono">{escape(latest_decision["sha"][:16])}…</span></span>'
                if latest_decision
                else ""
            )
        accept_html = f"""
  <section class="accept-panel" id="accept-gate-panel">
    <div class="node-head" style="margin-bottom:10px;">
      <div>
        <span class="eyebrow" style="margin-bottom:2px;display:block;">Гейт человека (Human-in-the-loop)</span>
        <h3 style="font-size:15px;">Утверждение решения комитетом (<span class="mono">ledger accept</span>)</h3>
      </div>
      {gate_badge}
    </div>
    <div class="accept-grid">
      <label class="field-label"><span>Вариант решения</span>
        <select id="accept-option" class="field-input" onchange="updateAcceptCommand()">{opt_options_html}</select>
      </label>
      <label class="field-label"><span>Кто утверждает (<span class="mono">--by</span>)</span>
        <input id="accept-by" type="text" class="field-input" value="Продуктовый комитет" oninput="updateAcceptCommand()"/>
      </label>
      <label class="field-label"><span>Основание (<span class="mono">--reason</span>)</span>
        <input id="accept-reason" type="text" class="field-input" value="{escape(default_reason, quote=True)}" oninput="updateAcceptCommand()"/>
      </label>
    </div>
    <div id="accept-mismatch-note" class="claim-warn" style="display:none;margin-top:8px;border-top:none;padding-top:0;">
      Выбранный вариант отличается от <span class="mono">{escape(active_proposal["recommended"])}</span> в черновике: <span class="mono">ledger accept</span> заблокирует подмену без обновления черновика через <span class="mono">propose</span>.
    </div>
    <div class="cmd-box">
      <code id="accept-cmd-preview" class="mono"></code>
    </div>
    <div class="accept-actions">
      <button type="button" class="action-btn" id="accept-copy-btn" onclick="copyAcceptCommand()">Скопировать команду accept</button>
      {live_btn_html}
      <span id="accept-feedback" class="accept-feedback" aria-live="polite"></span>
    </div>
  </section>"""

    # Build interactive what-if unit-economics calculator (Item 1)
    whatif_html = ""
    if whatif_model:
        margin_pct_str = f"{whatif_model['min_margin_rate'] * 100:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        plan_opts_html = "".join(
            f'<option value="{escape(p["id"])}">{escape(p["id"])} — {escape(p["title"])}</option>'
            for p in whatif_model["plans"]
        )
        whatif_html = f"""
  <h2>Интерактивный калькулятор чувствительности юнит-экономики (what-if)</h2>
  <section class="whatif-panel" id="whatif-panel">
    <div class="whatif-header">
      <div>
        <div class="node-meta" style="font-size:12px;color:var(--muted);">
          Порог комитета: <strong>маржинальный вклад &gt;= {margin_pct_str}%</strong> (источник модели: <span class="mono">{escape(whatif_model["source_id"])}</span>).
          Цены с витрин — публичные факты или рыночные ориентиры; <strong>CAC, срок удержания и рекуррентные затраты — сценарные допущения (what-if)</strong>,
          пересчитываемые в браузере строго по формулам <span class="mono">ledger/economics.py</span> без внешних зависимостей.
        </div>
      </div>
      <div class="preset-bar">
        <button type="button" class="preset-btn active-preset" data-preset="base" onclick="applyEconPreset('base', this)">База спецификации</button>
        <button type="button" class="preset-btn" data-preset="cvm" onclick="applyEconPreset('cvm', this)">Собственные CVM-каналы (CAC 25 ₽)</button>
        <button type="button" class="preset-btn" data-preset="paid" onclick="applyEconPreset('paid', this)">Платный внешний трафик (CAC +25%)</button>
        <button type="button" class="preset-btn" data-preset="stress" onclick="applyEconPreset('stress', this)">Стресс затрат (+15%) и оттока (-2 мес.)</button>
      </div>
    </div>

    <div class="whatif-controls-top">
      <label class="field-label" style="flex:1;min-width:260px;">Вариант для настройки ползунками
        <select id="whatif-plan-select" class="field-input" onchange="selectWhatIfPlan(this.value)">{plan_opts_html}</select>
      </label>
      <label class="checkbox-inline">
        <input type="checkbox" id="whatif-apply-all" checked onchange="recomputeAllWhatIf()"/>
        <span>Применять пресет канала привлечения (CVM / платный / стресс) сразу ко всем вариантам сравнения</span>
      </label>
    </div>

    <div class="sliders-grid">
      <div class="slider-card">
        <div class="slider-head">
          <span>Цена / 30 дн. (₽)</span>
          <span id="badge-price-basis" class="tag">факт витрины</span>
        </div>
        <div class="slider-row">
          <input type="range" id="sl-price" min="29" max="600" step="1" oninput="onSliderInput('price', this.value)"/>
          <input type="number" id="num-price" min="1" max="50000" step="1" class="num-input mono" oninput="onSliderInput('price', this.value)"/>
        </div>
        <div class="node-meta" id="delta-price"></div>
      </div>

      <div class="slider-card">
        <div class="slider-head">
          <span>CAC привлечения (₽)</span>
          <span class="tag tag-warn">допущение (what-if)</span>
        </div>
        <div class="slider-row">
          <input type="range" id="sl-cac" min="0" max="800" step="5" oninput="onSliderInput('cac', this.value)"/>
          <input type="number" id="num-cac" min="0" max="100000" step="5" class="num-input mono" oninput="onSliderInput('cac', this.value)"/>
        </div>
        <div class="node-meta" id="delta-cac"></div>
      </div>

      <div class="slider-card">
        <div class="slider-head">
          <span>Срок удержания (мес.)</span>
          <span class="tag tag-warn">допущение (what-if)</span>
        </div>
        <div class="slider-row">
          <input type="range" id="sl-retention" min="1" max="36" step="1" oninput="onSliderInput('retention', this.value)"/>
          <input type="number" id="num-retention" min="1" max="120" step="1" class="num-input mono" oninput="onSliderInput('retention', this.value)"/>
        </div>
        <div class="node-meta" id="delta-retention"></div>
      </div>

      <div class="slider-card">
        <div class="slider-head">
          <span>Рекуррентные затраты / мес. (₽)</span>
          <span class="tag tag-warn">допущение (what-if)</span>
        </div>
        <div class="slider-row">
          <input type="range" id="sl-recurring" min="0" max="300" step="1" oninput="onSliderInput('recurring', this.value)"/>
          <input type="number" id="num-recurring" min="0" max="50000" step="1" class="num-input mono" oninput="onSliderInput('recurring', this.value)"/>
        </div>
        <div class="node-meta" id="delta-recurring"></div>
      </div>
    </div>

    <div class="whatif-kpi-strip" id="whatif-kpi-strip"></div>

    <table style="margin-top:12px;">
      <thead>
        <tr>
          <th>Вариант</th>
          <th>Цена / 30 дн.</th>
          <th>Рекуррентные (допущ.)</th>
          <th>CAC (допущ.)</th>
          <th>Удержание (допущ.)</th>
          <th>Прямые затраты / мес.</th>
          <th>Маржинальный вклад</th>
          <th>Пороги безубыточности к цели {margin_pct_str}%</th>
        </tr>
      </thead>
      <tbody id="whatif-table-body"></tbody>
    </table>
  </section>"""

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
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px;
  }}
  .metric-card {{
    background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px;
  }}
  .metric-label {{ font-size: 11px; color: var(--subtle); text-transform: uppercase; letter-spacing: 0.05em; }}
  .metric-value {{ font-size: 14px; font-weight: 600; margin-top: 4px; }}
  .impact-banner {{
    background: var(--alert-soft); border: 1px solid #FECACA; border-radius: 8px;
    padding: 14px 16px; margin-bottom: 18px; color: #7F1D1D;
  }}
  .impact-banner p {{ margin: 4px 0 0; font-size: 13px; }}
  .accept-panel, .whatif-panel {{
    background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
    padding: 16px 18px; margin-bottom: 20px;
  }}
  .accept-grid {{
    display: grid; grid-template-columns: 1.2fr 0.8fr 1.4fr; gap: 12px; align-items: end;
  }}
  .field-label {{
    display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--subtle);
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  }}
  .field-input {{
    width: 100%; padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px;
    font-size: 13px; font-family: inherit; color: var(--ink); background: #F8FAFC;
  }}
  .cmd-box {{
    margin-top: 10px; padding: 9px 12px; background: #0F172A; color: #E2E8F0;
    border-radius: 6px; overflow-x: auto; font-size: 12px;
  }}
  .accept-actions {{
    margin-top: 10px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  }}
  .action-btn {{
    padding: 7px 13px; border-radius: 6px; border: 1px solid var(--line);
    background: var(--surface); color: var(--ink); font-size: 12px; font-weight: 600; cursor: pointer;
  }}
  .action-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
  .action-btn-primary {{
    background: var(--accent); border-color: var(--accent); color: #FFFFFF;
  }}
  .action-btn-primary:hover {{ background: #115E59; color: #FFFFFF; }}
  .accept-feedback {{ font-size: 12px; font-weight: 600; color: var(--accent); }}
  .whatif-header {{
    display: flex; flex-direction: column; gap: 10px; padding-bottom: 12px; border-bottom: 1px solid var(--line);
  }}
  .preset-bar {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .preset-btn {{
    padding: 5px 11px; border-radius: 6px; border: 1px solid var(--line); background: #F8FAFC;
    color: var(--muted); font-size: 12px; font-weight: 500; cursor: pointer;
  }}
  .preset-btn.active-preset {{
    border-color: var(--accent); background: var(--accent-soft); color: var(--accent); font-weight: 600;
  }}
  .whatif-controls-top {{
    display: flex; flex-wrap: wrap; gap: 16px; align-items: end; margin: 12px 0;
  }}
  .checkbox-inline {{
    display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--muted); padding-bottom: 6px; cursor: pointer;
  }}
  .sliders-grid {{
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 12px;
  }}
  .slider-card {{
    border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; background: #F8FAFC;
  }}
  .slider-head {{
    display: flex; justify-content: space-between; align-items: center; gap: 6px;
    font-size: 12px; font-weight: 600; margin-bottom: 8px;
  }}
  .slider-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
  .slider-row input[type="range"] {{ flex: 1; accent-color: var(--accent); cursor: pointer; }}
  .num-input {{
    width: 72px; padding: 4px 6px; border: 1px solid var(--line); border-radius: 5px;
    background: var(--surface); font-size: 12px; text-align: right;
  }}
  .whatif-kpi-strip {{
    display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px;
  }}
  .kpi-mini {{
    border: 1px solid var(--line); border-radius: 7px; padding: 9px 11px; background: var(--surface);
  }}
  .kpi-mini .k-lbl {{ font-size: 11px; color: var(--subtle); }}
  .kpi-mini .k-val {{ font-size: 14px; font-weight: 600; margin-top: 2px; }}
  tr.whatif-selected-row td {{ background: var(--accent-soft); }}
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
  {accept_html}

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

  {whatif_html}
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
const ACCEPT_META = {json.dumps(accept_gate_meta, ensure_ascii=False)};
const ECON_MODEL = {json.dumps(whatif_model, ensure_ascii=False)};

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
    const up = [start];
    while (up.length) {{
      const cur = up.pop();
      const f = GRAPH.findings[cur];
      if (!f) continue;
      f.sources.forEach(s => rel.add("source:" + s));
      f.depends_on.forEach(d => {{ if (!rel.has("finding:" + d)) {{ rel.add("finding:" + d); up.push(d); }} }});
    }}
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

/* --- Committee Accept Gate (Item 4) --- */
function shQuote(val) {{
  const s = String(val || "").trim();
  return '"' + s.replace(/\\\\/g, "\\\\\\\\").replace(/"/g, '\\\\"') + '"';
}}
function buildAcceptCommand() {{
  const optEl = document.getElementById("accept-option");
  if (!optEl) return "";
  const opt = optEl.value;
  const by = (document.getElementById("accept-by")?.value || "Продуктовый комитет").trim();
  const reason = (document.getElementById("accept-reason")?.value || "Согласовано").trim();
  const sha = ACCEPT_META.expect_sha || "<draft_sha256>";
  return `python3 -m ledger accept ${{ACCEPT_META.case_cli}} ${{opt}} --expect ${{sha}} --by ${{shQuote(by)}} --reason ${{shQuote(reason)}}`;
}}
function updateAcceptCommand() {{
  const preview = document.getElementById("accept-cmd-preview");
  if (!preview) return;
  preview.textContent = buildAcceptCommand();
  const opt = document.getElementById("accept-option")?.value || "";
  const warn = document.getElementById("accept-mismatch-note");
  if (warn) {{
    warn.style.display = (ACCEPT_META.recommended && opt !== ACCEPT_META.recommended) ? "block" : "none";
  }}
}}
function copyAcceptCommand() {{
  const cmd = buildAcceptCommand();
  const fb = document.getElementById("accept-feedback");
  const showOk = (msg) => {{
    if (!fb) return;
    fb.textContent = msg;
    setTimeout(() => {{ if (fb.textContent === msg) fb.textContent = ""; }}, 3200);
  }};
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(cmd).then(() => showOk("Команда скопирована в буфер обмена")).catch(() => fallbackCopy(cmd, showOk));
  }} else {{
    fallbackCopy(cmd, showOk);
  }}
}}
function fallbackCopy(text, cb) {{
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {{ document.execCommand("copy"); cb("Команда скопирована в буфер обмена"); }}
  catch (e) {{ cb("Выделите строку выше и скопируйте (Ctrl+C)"); }}
  document.body.removeChild(ta);
}}
async function triggerLiveAccept() {{
  if (!ACCEPT_META.live_enabled) return;
  const fb = document.getElementById("accept-feedback");
  const btn = document.getElementById("accept-live-btn");
  const payload = {{
    option: document.getElementById("accept-option").value,
    expect: ACCEPT_META.expect_sha,
    by: document.getElementById("accept-by").value,
    reason: document.getElementById("accept-reason").value
  }};
  if (btn) btn.disabled = true;
  if (fb) fb.textContent = "Запись решения в case.sqlite3…";
  try {{
    const res = await fetch("/api/accept", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json", "X-Ledger-Token": ACCEPT_META.live_token }},
      body: JSON.stringify(payload)
    }});
    const data = await res.json();
    if (!res.ok || !data.ok) {{
      if (fb) fb.textContent = "Ошибка гейта: " + (data.error || res.statusText);
      if (btn) btn.disabled = false;
      return;
    }}
    if (fb) fb.textContent = `Утверждено: Решение №${{data.decision.revision}} (${{data.decision.option}})`;
    setTimeout(() => window.location.reload(), 700);
  }} catch (err) {{
    if (fb) fb.textContent = "Сбой соединения с локальным сервером: " + err;
    if (btn) btn.disabled = false;
  }}
}}

/* --- Interactive What-If Unit Economics (Item 1 · exact ledger/economics.py formulas) --- */
const WHATIF_STATE = {{ selectedId: null, preset: "base", overrides: {{}} }};

function fmtRub(val, digits = 2) {{
  if (val === null || val === undefined || Number.isNaN(val)) return "недостижимо";
  const rounded = Number(val.toFixed(digits));
  const parts = rounded.toFixed(digits).split(".");
  parts[0] = parts[0].replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, " ");
  const dec = (digits > 0 && parts[1] !== "00" && parts[1] !== "0") ? ("," + parts[1]) : "";
  return parts[0] + dec + " ₽";
}}
function fmtNum(val, suffix = "", digits = 2) {{
  if (val === null || val === undefined || Number.isNaN(val)) return "недостижимо";
  const rounded = Number(val.toFixed(digits));
  let s = rounded.toFixed(digits).replace(".", ",");
  if (s.endsWith(",00")) s = s.slice(0, -3);
  return s + suffix;
}}
function evaluatePlanExact(price, recurring, cac, retention, minMargin) {{
  const rev = Math.max(0.01, Number(price));
  const rec = Math.max(0, Number(recurring));
  const upf = Math.max(0, Number(cac));
  const ret = Math.max(1, Math.round(Number(retention)));
  const amortUpfront = upf > 0 ? (upf / ret) : 0.0;
  const directCost = rec + amortUpfront;
  const contribution = rev - directCost;
  const contributionRate = contribution / rev;
  const maxAllowableCost = rev * (1.0 - minMargin);
  const costHeadroom = maxAllowableCost - directCost;
  const cashMarginBeforeCapex = rev - rec;
  const paybackMonths = (upf > 0 && cashMarginBeforeCapex > 0)
    ? Number((upf / cashMarginBeforeCapex).toFixed(2))
    : (upf === 0 ? 0.0 : null);
  let maxUpfrontCac = Number(((maxAllowableCost - rec) * ret).toFixed(2));
  if (maxUpfrontCac < 0) maxUpfrontCac = null;
  const monthlyRoom = maxAllowableCost - rec;
  let minRetention = null;
  if (upf === 0) {{
    minRetention = monthlyRoom >= 0 ? 1 : null;
  }} else if (monthlyRoom > 0) {{
    minRetention = Number((upf / monthlyRoom).toFixed(2));
  }}
  const minMonthlyPrice = minMargin < 1.0 ? Number((directCost / (1.0 - minMargin)).toFixed(2)) : null;
  const meetsTarget = (contributionRate + 1e-12) >= minMargin;
  return {{
    price: rev, recurring: rec, cac: upf, retention: ret,
    amortUpfront, directCost, contribution, contributionRate,
    maxAllowableCost, costHeadroom, paybackMonths,
    maxUpfrontCac, minRetention, minMonthlyPrice, meetsTarget
  }};
}}
function presetParamsForPlan(plan, preset) {{
  if (preset === "cvm") {{
    return {{ price: plan.price, recurring: plan.recurring, cac: plan.cac > 0 ? 25 : 0, retention: plan.retention }};
  }}
  if (preset === "paid") {{
    const paidCac = plan.cac > 0 ? Math.round(plan.cac * 1.25) : 260;
    return {{ price: plan.price, recurring: plan.recurring, cac: paidCac, retention: plan.retention }};
  }}
  if (preset === "stress") {{
    return {{
      price: plan.price,
      recurring: Number((plan.recurring * 1.15).toFixed(2)),
      cac: plan.cac,
      retention: Math.max(1, plan.retention - 2)
    }};
  }}
  return {{ price: plan.price, recurring: plan.recurring, cac: plan.cac, retention: plan.retention }};
}}
function initWhatIf() {{
  if (!ECON_MODEL || !ECON_MODEL.plans || !ECON_MODEL.plans.length) return;
  ECON_MODEL.plans.forEach(p => {{
    WHATIF_STATE.overrides[p.id] = {{ price: p.price, recurring: p.recurring, cac: p.cac, retention: p.retention }};
  }});
  WHATIF_STATE.selectedId = ECON_MODEL.plans[0].id;
  syncSlidersToSelected();
  renderWhatIf();
}}
function syncSlidersToSelected() {{
  const plan = ECON_MODEL.plans.find(p => p.id === WHATIF_STATE.selectedId);
  if (!plan) return;
  const cur = WHATIF_STATE.overrides[plan.id];
  const setPair = (key, val, maxHint) => {{
    const sl = document.getElementById("sl-" + key);
    const nm = document.getElementById("num-" + key);
    if (sl) {{
      if (val > Number(sl.max)) sl.max = String(Math.ceil(val * 1.5));
      sl.value = String(val);
    }}
    if (nm) nm.value = String(val);
  }};
  setPair("price", cur.price);
  setPair("cac", cur.cac);
  setPair("retention", cur.retention);
  setPair("recurring", cur.recurring);
}}
function selectWhatIfPlan(planId) {{
  WHATIF_STATE.selectedId = planId;
  const sel = document.getElementById("whatif-plan-select");
  if (sel && sel.value !== planId) sel.value = planId;
  syncSlidersToSelected();
  renderWhatIf();
}}
function applyEconPreset(preset, btnEl) {{
  WHATIF_STATE.preset = preset;
  document.querySelectorAll(".preset-btn").forEach(b => b.classList.toggle("active-preset", b === btnEl));
  const applyAll = document.getElementById("whatif-apply-all")?.checked;
  ECON_MODEL.plans.forEach(p => {{
    if (applyAll || p.id === WHATIF_STATE.selectedId || preset === "base") {{
      WHATIF_STATE.overrides[p.id] = presetParamsForPlan(p, preset);
    }}
  }});
  syncSlidersToSelected();
  renderWhatIf();
}}
function recomputeAllWhatIf() {{
  if (WHATIF_STATE.preset !== "base") {{
    const activeBtn = document.querySelector(`.preset-btn[data-preset="${{WHATIF_STATE.preset}}"]`);
    applyEconPreset(WHATIF_STATE.preset, activeBtn);
  }} else {{
    renderWhatIf();
  }}
}}
function onSliderInput(field, rawVal) {{
  const num = Number(rawVal);
  if (Number.isNaN(num)) return;
  const plan = ECON_MODEL.plans.find(p => p.id === WHATIF_STATE.selectedId);
  if (!plan) return;
  const cur = WHATIF_STATE.overrides[plan.id];
  cur[field] = field === "retention" ? Math.max(1, Math.round(num)) : Math.max(0, num);
  const sl = document.getElementById("sl-" + field);
  const nm = document.getElementById("num-" + field);
  if (sl && document.activeElement !== sl) {{
    if (cur[field] > Number(sl.max)) sl.max = String(Math.ceil(cur[field] * 1.5));
    sl.value = String(cur[field]);
  }}
  if (nm && document.activeElement !== nm) nm.value = String(cur[field]);
  renderWhatIf();
}}
function renderWhatIf() {{
  if (!ECON_MODEL) return;
  const minM = ECON_MODEL.min_margin_rate;
  const selPlan = ECON_MODEL.plans.find(p => p.id === WHATIF_STATE.selectedId) || ECON_MODEL.plans[0];
  const selCur = WHATIF_STATE.overrides[selPlan.id];
  const selEval = evaluatePlanExact(selCur.price, selCur.recurring, selCur.cac, selCur.retention, minM);

  const basisBadge = document.getElementById("badge-price-basis");
  if (basisBadge) {{
    const priceChanged = Math.abs(selCur.price - selPlan.price) > 1e-6;
    basisBadge.textContent = priceChanged ? "допущение (what-if)" : selPlan.basis_label;
    basisBadge.className = priceChanged ? "tag tag-warn" : "tag tag-ok";
  }}
  const showDelta = (id, baseV, curV, unit) => {{
    const el = document.getElementById(id);
    if (!el) return;
    if (Math.abs(curV - baseV) < 1e-6) {{
      el.textContent = `База спецификации: ${{fmtNum(baseV, unit)}}`;
    }} else {{
      const sign = curV > baseV ? "+" : "";
      el.textContent = `База: ${{fmtNum(baseV, unit)}} → изменено (${{sign}}${{fmtNum(curV - baseV, unit)}})`;
    }}
  }};
  showDelta("delta-price", selPlan.price, selCur.price, " ₽");
  showDelta("delta-cac", selPlan.cac, selCur.cac, " ₽");
  showDelta("delta-retention", selPlan.retention, selCur.retention, " мес.");
  showDelta("delta-recurring", selPlan.recurring, selCur.recurring, " ₽");

  const kpiStrip = document.getElementById("whatif-kpi-strip");
  if (kpiStrip) {{
    const statusCls = selEval.meetsTarget ? "tag tag-ok" : "tag tag-alert";
    const statusTxt = selEval.meetsTarget ? "Проходит порог комитета" : "Ниже порога комитета";
    const paybackTxt = selEval.paybackMonths === null ? "не окупается" : fmtNum(selEval.paybackMonths, " мес.");
    kpiStrip.innerHTML = `
      <div class="kpi-mini">
        <div class="k-lbl">Маржинальный вклад (расчёт на допущениях)</div>
        <div class="k-val">${{fmtRub(selEval.contribution)}} (${{fmtNum(selEval.contributionRate * 100, "%", 1)}})</div>
        <div style="margin-top:4px;"><span class="${{statusCls}}">${{statusTxt}}</span></div>
      </div>
      <div class="kpi-mini">
        <div class="k-lbl">Полные прямые затраты / мес.</div>
        <div class="k-val">${{fmtRub(selEval.directCost)}}</div>
        <div class="node-meta">OPEX ${{fmtRub(selEval.recurring)}} + CAC/мес. ${{fmtRub(selEval.amortUpfront)}}</div>
      </div>
      <div class="kpi-mini">
        <div class="k-lbl">Запас до потолка затрат (headroom)</div>
        <div class="k-val" style="color:${{selEval.costHeadroom >= 0 ? 'var(--accent)' : 'var(--alert)'}};">${{selEval.costHeadroom >= 0 ? '+' : ''}}${{fmtRub(selEval.costHeadroom)}}</div>
        <div class="node-meta">Потолок прямых затрат: ${{fmtRub(selEval.maxAllowableCost)}}</div>
      </div>
      <div class="kpi-mini">
        <div class="k-lbl">Окупаемость CAC (операционная)</div>
        <div class="k-val">${{paybackTxt}}</div>
        <div class="node-meta">При удержании ${{selEval.retention}} мес.</div>
      </div>
      <div class="kpi-mini">
        <div class="k-lbl">Границы безубыточности к цели</div>
        <div class="k-val" style="font-size:12px;">CAC &lt;= ${{fmtRub(selEval.maxUpfrontCac)}}</div>
        <div class="node-meta">Удерж. &gt;= ${{fmtNum(selEval.minRetention, " мес.")}} · Цена &gt;= ${{fmtRub(selEval.minMonthlyPrice)}}</div>
      </div>`;
  }}

  const tbody = document.getElementById("whatif-table-body");
  if (tbody) {{
    tbody.innerHTML = ECON_MODEL.plans.map(p => {{
      const cur = WHATIF_STATE.overrides[p.id];
      const ev = evaluatePlanExact(cur.price, cur.recurring, cur.cac, cur.retention, minM);
      const isSel = p.id === WHATIF_STATE.selectedId;
      const badge = ev.meetsTarget
        ? '<span class="tag tag-ok">проходит</span>'
        : '<span class="tag tag-alert">ниже порога</span>';
      const priceBasis = Math.abs(cur.price - p.price) > 1e-6 ? "what-if" : p.basis_label;
      const beLine = ev.maxUpfrontCac !== null
        ? `CAC &lt;= ${{fmtRub(ev.maxUpfrontCac)}} · удерж. &gt;= ${{fmtNum(ev.minRetention, " мес.")}}`
        : `рекуррентные затраты выше потолка ${{fmtRub(ev.maxAllowableCost)}} (CAC не спасает)`;
      return `<tr class="${{isSel ? 'whatif-selected-row' : ''}}" style="cursor:pointer;" onclick="selectWhatIfPlan('${{p.id}}')">
        <td><span class="mono">${{p.id}}</span><div class="node-meta">${{p.title}}</div></td>
        <td>${{fmtRub(ev.price)}} <span class="node-meta">(${{priceBasis}})</span></td>
        <td>${{fmtRub(ev.recurring)}}</td>
        <td>${{fmtRub(ev.cac)}}</td>
        <td>${{ev.retention}} мес.</td>
        <td>${{fmtRub(ev.directCost)}}</td>
        <td><strong>${{fmtNum(ev.contributionRate * 100, "%", 1)}}</strong> (${{fmtRub(ev.contribution)}}) ${{badge}}</td>
        <td style="font-size:12px;">${{beLine}}</td>
      </tr>`;
    }}).join("");
  }}
}}

updateAcceptCommand();
initWhatIf();
</script>
</body>
</html>
"""


def write_dashboard(
    case: Path,
    state: dict,
    sources_health: dict,
    output: Path | None = None,
    *,
    db=None,
) -> Path:
    html = render_dashboard_html(case, state, sources_health, db=db)
    target = output.resolve() if output else (case / "dashboard.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


def serve_dashboard(
    case: Path,
    *,
    load_fn,
    source_health_fn,
    accept_fn,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Serve interactive passport over 127.0.0.1 with 1-click committee accept writeback to case.sqlite3."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Dashboard server may only bind to loopback (127.0.0.1)")
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            if self.path not in {"/", "/index.html", "/decision-passport.html"}:
                self.send_error(404)
                return
            with load_fn(case, write=False) as (st, db_conn):
                srcs = source_health_fn(case, st["sources"])
                body = render_dashboard_html(case, st, srcs, db=db_conn, live_api_token=token).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/api/accept":
                self.send_error(404)
                return
            if self.headers.get("X-Ledger-Token") != token:
                self._json(403, {"ok": False, "error": "Invalid session token"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 65536:
                self._json(400, {"ok": False, "error": "Invalid payload size"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                decision = accept_fn(
                    case,
                    option=str(payload.get("option") or ""),
                    expect=str(payload.get("expect") or ""),
                    by=str(payload.get("by") or ""),
                    reason=str(payload.get("reason") or ""),
                )
                self._json(200, {"ok": True, "decision": decision})
            except Exception as err:  # noqa: BLE001
                self._json(400, {"ok": False, "error": str(err)})

        def _json(self, code: int, obj: dict):
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer((host, port), Handler)
    actual_port = server.server_address[1]
    print(json.dumps({"serving": f"http://{host}:{actual_port}/", "case": str(case)}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
