"""Universal export of Product and Communication strategies (Markdown + PDF) from case.sqlite3."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .claims import audit_claims
from .cost import summarize_case_cost
from .economics import evaluate_economics, render_assumptions_markdown
from .review import impact, order_findings

KIND_LABELS_RU = {
    "fact": "Факт",
    "observation": "Наблюдение",
    "inference": "Расчёт / вывод",
    "hypothesis": "Гипотеза",
    "forecast": "Прогноз",
    "assumption": "Допущение",
    "unknown": "Пробел данных (unknown)",
}

OPTION_TYPE_LABELS_RU = {
    "keep": "Оставить как есть (keep)",
    "change": "Изменить продукт / модель (change)",
    "close": "Закрыть / свернуть (close)",
}

SOURCE_KIND_LABELS_RU = {
    "web": "Открытая веб-страница",
    "document": "Документ / отчёт",
    "table": "Таблица данных",
    "ux": "Экран / UX-снимок",
    "interview": "Полевое интервью",
    "synthetic": "Модельные респонденты (синтетика)",
}


def _md_cell(text: object) -> str:
    raw = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    return raw.replace("|", "\\|")


def _extract_economics_blocks(case: Path, state: dict, db) -> list[tuple[str, dict, dict]]:
    """Find any registered JSON source in case.sqlite3 (or case dir) that represents a valid economics spec."""
    results: list[tuple[str, dict, dict]] = []
    for sid, rec in (state.get("sources") or {}).items():
        if rec.get("retired") or not rec.get("versions"):
            continue
        ver = rec["versions"][-1]
        rel_path = str(ver.get("path") or "")
        if not rel_path.lower().endswith(".json"):
            continue
        raw_bytes: bytes | None = None
        if db is not None:
            row = db.execute("SELECT content FROM evidence WHERE sha = ?", (ver["sha"],)).fetchone()
            if row and row[0]:
                raw_bytes = bytes(row[0])
        if raw_bytes is None:
            disk_p = case / rel_path
            if disk_p.is_file():
                raw_bytes = disk_p.read_bytes()
        if not raw_bytes:
            continue
        try:
            doc = json.loads(raw_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("plans"), dict) and doc["plans"]:
            try:
                ev = evaluate_economics(doc)
                results.append((sid, doc, ev))
            except (KeyError, TypeError, ValueError):
                continue
    return results


def _format_cost_lines(cost_summary: dict) -> list[str]:
    if cost_summary.get("steps", 0) == 0:
        return ["- Журнал `cost.jsonl` пуст: телеметрия шагов в этом кейсе ещё не записывалась."]

    if cost_summary.get("telemetry_verified"):
        prov = "Верифицировано из сырых логов рантайма CLI (`verified: true`)"
    else:
        unv = cost_summary.get("unverified_self_reported_steps", 0)
        prov = f"ВНИМАНИЕ: содержит неверифицированный самоотчёт модели (`{unv}` шаг., `verified: false`)"

    human_min = cost_summary.get("human_minutes")
    if human_min is None:
        human_str = "`не измерялось` (секундомер участия продакта не вёлся)"
    elif cost_summary.get("total_decision_cost_rub") is not None:
        human_str = (
            f"`{human_min} мин` (`{cost_summary['human_cost_rub']:.2f} ₽`) · "
            f"полная стоимость с участием человека: **`{cost_summary['total_decision_cost_rub']:.2f} ₽`**"
        )
    else:
        human_str = f"`{human_min} мин` (`{cost_summary['human_cost_rub']:.2f} ₽`)"

    if cost_summary.get("api_cost_rub") is not None:
        tag = "оценка по публичному прайсу" if cost_summary.get("estimated") else "прямой счёт API"
        money_str = f"`${cost_summary['api_cost_usd']:.4f}` (`{cost_summary['api_cost_rub']:.2f} ₽`, {tag})"
    elif cost_summary.get("api_cost_usd") is not None:
        money_str = (
            f"оценка CLI из лога `${cost_summary['api_cost_usd']:.4f}` "
            "(работа в квоте подписки, списания в ₽ не было)"
        )
    else:
        money_str = "`не рассчитывается` (работа в квоте подписки IDE, денежная оценка в логе отсутствует)"

    model_calls_part = (
        f" · вызовов модели: `{cost_summary['model_calls']}`"
        if cost_summary.get("model_calls") is not None
        else ""
    )
    reasoning_part = (
        f" (в т.ч. рассуждений `{cost_summary['reasoning_tokens']:,}`)"
        if cost_summary.get("reasoning_tokens")
        else ""
    ).replace(",", " ")

    lines = [
        f"- **Статус телеметрии**: {prov}.",
        (
            f"- **Объём и время**: шагов в `{cost_summary['log_file']}` — `{cost_summary['steps']}` · "
            f"суммарное время — `{cost_summary['wall_seconds']} с`"
            f"{model_calls_part} · вызовов инструментов — `{cost_summary.get('tool_calls', 0)}` · "
            f"повторов — `{cost_summary.get('retries', 0)}`."
        ),
        (
            f"- **Токены**: полный вход `{cost_summary['input_tokens']:,}` "
            f"(новый вход `{cost_summary['uncached_input_tokens']:,}` + чтение кэша `{cost_summary['cached_tokens']:,}`, "
            f"доля кэша `{round(cost_summary['cache_hit_ratio'] * 100, 1)}%`), "
            f"выход `{cost_summary['output_tokens']:,}`{reasoning_part}."
        ).replace(",", " "),
        f"- **Денежная оценка рантайма**: {money_str}.",
        f"- **Время человека (`human_minutes`)**: {human_str}.",
    ]
    methods = cost_summary.get("methods") or []
    if methods:
        lines.append(f"- **Методика учёта**: {'; '.join(methods)}.")
    return lines


def build_product_strategy_markdown(
    *,
    case: Path,
    state: dict,
    sources_health: dict,
    proposal: dict,
    decision: dict | None,
    active_impact: dict,
    cost_summary: dict,
    econ_blocks: list[tuple[str, dict, dict]],
) -> str:
    rec_id = proposal["recommended"]
    rec_opt = proposal["options"][rec_id]
    decisions = state.get("decisions") or []

    if decision:
        status_badge = (
            f"Решение №{decision['revision']} · Утвердил: {decision['by']} · "
            f"{decision['at']} · снимок `{decision['sha'][:16]}…`"
        )
        approval_reason = decision["reason"]
    else:
        status_badge = "Черновик на рассмотрении комитета (гейт `accept` ещё не пройден)"
        approval_reason = "Ожидает подтверждения руководителем продукта"

    stale_warn = ""
    if active_impact["sources"] or active_impact["unreviewed"]:
        stale_warn = (
            f"> **Внимание — обнаружены обновлённые вводные:** изменившиеся источники "
            f"`{', '.join(active_impact['sources']) or 'нет'}`, новые непроверенные источники "
            f"`{', '.join(active_impact['unreviewed']) or 'нет'}` (затронуто оснований: "
            f"`{len(active_impact['findings'])}`, вариантов: `{len(active_impact['options'])}`).\n\n"
        )

    lines = [
        f"# Продуктовая стратегия: {proposal['title']}",
        "",
        f"*{status_badge}*",
        "",
        stale_warn.rstrip() if stale_warn else "",
    ]
    lines = [ln for ln in lines if ln != "" or lines.index(ln) == 1]
    if stale_warn:
        lines.append("")

    lines += [
        "## Резюме для продуктового комитета",
        "",
        f"- **Исходный запрос**: {state['request']}",
        f"- **Выбранное направление (`{rec_id}`)**: **{rec_opt['title']}** ({OPTION_TYPE_LABELS_RU.get(rec_opt['type'], rec_opt['type'])}).",
        f"- **Суть решения**: {proposal['summary']}",
        f"- **Главное возражение скептика к выбранному ходу**: {rec_opt['objection']}",
        f"- **Основание фиксации**: {approval_reason}.",
    ]
    if decision and decision["revision"] > 1 and len(decisions) >= 2:
        prev = decisions[decision["revision"] - 2]
        lines.append(
            f"- **Преемственность версий**: замещает Решение №{prev['revision']} "
            f"(`{prev['option']}`, снимок `{prev['sha'][:12]}…`, статус в БЗ: `superseded`)."
        )
    lines += ["", "---", ""]

    # Section 1: Sources & Findings
    lines += [
        "## 1. Доказательная база и эпистемический статус оснований",
        "",
        "Все первичные материалы зафиксированы побайтным хэшем `SHA-256` в `case.sqlite3`. "
        "Синтетические респонденты и прошлые выводы помечены `can_cite_as_fact: false` и не могут выдаваться за доказанный рыночный спрос.",
        "",
        "| Источник (`ID`) | Тип материала | Путь / первоисточник | Снимок `SHA-256` | Примечание при сверке |",
        "| --- | --- | --- | --- | --- |",
    ]
    for sid, rev_meta in proposal["reviewed"].items():
        srec = sources_health.get(sid) or (state.get("sources") or {}).get(sid) or {}
        skind = srec.get("kind", "document")
        surl = srec.get("url")
        spath = srec.get("path") or ((srec.get("versions") or [{}])[-1].get("path", ""))
        loc_cell = f"[{_md_cell(spath)}]({surl})" if surl else f"`{_md_cell(spath)}`"
        sha_short = str(rev_meta.get("sha") or "")[:12]
        lines.append(
            f"| `{sid}` | {_md_cell(SOURCE_KIND_LABELS_RU.get(skind, skind))} | {loc_cell} "
            f"| `{sha_short}…` | {_md_cell(rev_meta.get('note', ''))} |"
        )
    lines.append("")

    ordered_f = order_findings(proposal["findings"])
    lines += [
        "### Ключевые основания решения (`findings`)",
        "",
        "| Узел | Статус | Формулировка вывода / границы | Опора (источник · локатор / зависимости) |",
        "| --- | --- | --- | --- |",
    ]
    for fid, fdata in ordered_f.items():
        refs = [f"`{c['source']}` ({c['locator']})" for c in fdata.get("citations", [])]
        refs += [f"зависит от `{d}`" for d in fdata.get("depends_on", [])]
        ref_str = "; ".join(refs) if refs else "явно зафиксированный пробел"
        k_label = KIND_LABELS_RU.get(fdata["kind"], fdata["kind"])
        lines.append(f"| `{fid}` | **{k_label}** | {_md_cell(fdata['text'])} | {_md_cell(ref_str)} |")
    lines += ["", "---", ""]

    # Section 2: JTBD & OST (if present)
    meth = proposal.get("methodology") or {}
    sec_num = 2
    if "jtbd" in meth or "ost" in meth:
        lines += [f"## {sec_num}. Работа аудитории (`JTBD`) и дерево возможностей (`OST`)", ""]
        if "jtbd" in meth:
            jtbd = meth["jtbd"]
            forces = jtbd["forces"]
            lines += [
                f"- **Задача плательщика (покупателя)**: {jtbd['payer_job']}",
                f"- **Задача конечного пользователя**: {jtbd['user_job']}",
                "- **Четыре силы переключения**:",
                f"  - *Толчок (Push)*: {forces['push']}",
                f"  - *Притяжение (Pull)*: {forces['pull']}",
                f"  - *Тревога (Anxiety)*: {forces['anxiety']}",
                f"  - *Инерция (Habit)*: {forces['habit']}",
                "",
            ]
        if "ost" in meth:
            ost = meth["ost"]
            lines += [
                f"### Дерево возможностей и решений (Целевой исход: {ost['outcome']})",
                "",
            ]
            for opp in ost["opportunities"]:
                lines.append(
                    f"- **Возможность `{opp['id']}`**: {opp['statement']} "
                    f"*(основания: `{', '.join(opp['depends_on'])}`)*"
                )
                for sol in opp["solutions"]:
                    lines.append(f"  - Вариант `{sol['option_id']}` — проверка допущения: {sol['assumption_test']}")
            lines.append("")
        lines += ["---", ""]
        sec_num += 1

    # Section 3: Options & Economics
    lines += [
        f"## {sec_num}. Сравнение стратегических альтернатив и юнит-экономика",
        "",
        "| Код и вариант | Тип хода | Аргумент в пользу варианта | Сильное возражение скептика | Основания | Статус |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for oid, odata in proposal["options"].items():
        status_txt = "**ВЫБРАН**" if oid == rec_id else "Отклонён / резерв"
        deps = ", ".join(f"`{d}`" for d in odata["depends_on"])
        lines.append(
            f"| `{oid}` · **{_md_cell(odata['title'])}** | {_md_cell(OPTION_TYPE_LABELS_RU.get(odata['type'], odata['type']))} "
            f"| {_md_cell(odata['case'])} | {_md_cell(odata['objection'])} | {deps} | {status_txt} |"
        )
    lines.append("")

    for sid, _spec_doc, ev_doc in econ_blocks:
        lines += [
            f"### Детерминированный расчёт экономики и порогов смены решения (источник `{sid}`)",
            "",
            render_assumptions_markdown(ev_doc).strip(),
            "",
        ]
    lines += ["---", ""]
    sec_num += 1

    # Section 4: Product Strategy, Experiment, Pre-mortem, Actions
    prod_block = proposal["strategy"]["product"]
    exp_block = proposal["strategy"]["experiment"]
    lines += [
        f"## {sec_num}. Целевая продуктовая стратегия, план проверки и поручения",
        "",
        "### Продуктовое решение и границы охвата",
        "",
        prod_block["text"],
        "",
        f"*Опирается на основания: `{', '.join(prod_block['depends_on'])}`.*",
        "",
        "### План проверки гипотез и стоп-правила пилота",
        "",
        exp_block["text"],
        "",
        f"*Опирается на основания: `{', '.join(exp_block['depends_on'])}`.*",
        "",
    ]

    if "premortem" in meth:
        lines += [
            "### Анализ рисков `Pre-mortem` (горизонт 6 месяцев)",
            "",
            "| Сценарий срыва | Вероятность | Влияние | Предотвратимость | Защитная мера / стоп-триггер |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in meth["premortem"]:
            lines.append(
                f"| {_md_cell(row['failure_mode'])} | `{_md_cell(row['probability'])}` "
                f"| `{_md_cell(row['impact'])}` | `{_md_cell(row['preventability'])}` | {_md_cell(row['mitigation'])} |"
            )
        lines.append("")

    completions = (decision or {}).get("completions") or {}
    lines += [
        "### Дорожная карта поручений и триггеры пересмотра (`actions`)",
        "",
        "| ID | Ответственный | Срок | Задача | Триггер возврата к решению | Статус |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for act in proposal["actions"]:
        comp = completions.get(act["id"])
        st_cell = f"Выполнено ({_md_cell(comp['note'])})" if comp else "Открыто"
        lines.append(
            f"| `{act['id']}` | {_md_cell(act['owner'])} | `{act['due']}` "
            f"| {_md_cell(act['task'])} | {_md_cell(act['trigger'])} | {st_cell} |"
        )
    lines += ["", "---", ""]
    sec_num += 1

    # Section 5: Cost provenance
    lines += [
        f"## {sec_num}. Прозрачность затрат и участие человека",
        "",
        *_format_cost_lines(cost_summary),
        "",
    ]
    return "\n".join(lines)


def build_comms_strategy_markdown(
    *,
    state: dict,
    proposal: dict,
    decision: dict | None,
    claims_report: dict,
    cost_summary: dict,
) -> str:
    rec_id = proposal["recommended"]
    rec_opt = proposal["options"][rec_id]
    comm_block = proposal["strategy"]["communication"]
    prod_block = proposal["strategy"]["product"]
    exp_block = proposal["strategy"]["experiment"]
    meth = proposal.get("methodology") or {}

    if decision:
        status_badge = (
            f"Решение №{decision['revision']} · Утвердил: {decision['by']} · "
            f"{decision['at']} · снимок `{decision['sha'][:16]}…`"
        )
    else:
        status_badge = "Черновик на согласовании (до прохождения гейта `accept`)"

    claims_status = (
        "ПРОЙДЕН (`0` предупреждений — обещания соответствуют границам продукта и типам оснований)"
        if claims_report["passed"]
        else f"ЕСТЬ ПРЕДУПРЕЖДЕНИЯ (`{claims_report['total_warnings']}` шт. — требуется корректировка формулировок)"
    )

    lines = [
        f"# Коммуникационная стратегия: {proposal['title']}",
        "",
        f"*{status_badge}*",
        "",
        "## Резюме для маркетинга, CVM и юридического контроля",
        "",
        f"- **Связанный продуктовый выбор (`{rec_id}`)**: **{rec_opt['title']}**.",
        f"- **Статус автоматического аудита обещаний (`ledger audit-claims`)**: **{claims_status}**.",
        f"- **Продуктовая рамка, за которую нельзя выходить в коммуникации**: {prod_block['text']}",
        "",
        "---",
        "",
        "## 1. Аудитория, барьеры восприятия и контекст контакта",
        "",
    ]

    if "jtbd" in meth:
        jtbd = meth["jtbd"]
        forces = jtbd["forces"]
        lines += [
            f"- **К кому обращено ценностное предложение (плательщик)**: {jtbd['payer_job']}",
            f"- **Опыт конечного пользователя (защищаемого / участника)**: {jtbd['user_job']}",
            f"- **Главный барьер тревоги (`Anxiety`), который обязана снять коммуникация**: {forces['anxiety']}",
            f"- **Привычка / альтернатива (`Habit`), с которой конкурирует сообщение**: {forces['habit']}",
            "",
        ]
    else:
        lines += [
            f"- **Исходный запрос и проблема аудитории**: {state['request']}",
            f"- **Ключевое возражение при выборе продукта**: {rec_opt['objection']}",
            "",
        ]

    # List synthetic/hypothesis caveats explicitly
    hyp_findings = [
        (fid, f) for fid, f in order_findings(proposal["findings"]).items()
        if f["kind"] in {"hypothesis", "assumption", "unknown"}
    ]
    if hyp_findings:
        lines += [
            "### Неизмеренные гипотезы и ограничения, которые запрещено подавать в рекламе как факт",
            "",
        ]
        for fid, fdata in hyp_findings:
            lines.append(
                f"- `{fid}` (*{KIND_LABELS_RU.get(fdata['kind'], fdata['kind'])}*): {fdata['text']}"
            )
        lines.append("")

    lines += [
        "---",
        "",
        "## 2. Позиционирование, ключевые сообщения и каналы",
        "",
        comm_block["text"],
        "",
        f"*Подтверждающие основания в реестре: `{', '.join(comm_block['depends_on'])}`.*",
        "",
        "---",
        "",
        "## 3. Матрица «Обещание коммуникации ↔ Возможность продукта»",
        "",
    ]

    if "promise_matrix" in meth and meth["promise_matrix"]:
        lines += [
            "| Обещание аудитории | Реальный механизм продукта | Подтверждающие основания | Честная граница (дисклеймер) | Канал |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in meth["promise_matrix"]:
            proofs = ", ".join(f"`{p}`" for p in row["proof_findings"])
            lines.append(
                f"| {_md_cell(row['promise'])} | {_md_cell(row['capability'])} "
                f"| {proofs} | {_md_cell(row['boundary'])} | {_md_cell(row['channel'])} |"
            )
        lines.append("")
    else:
        lines += [
            "| Элемент контроля | Содержание в утверждённом решении | Основания |",
            "| --- | --- | --- |",
            f"| Ключевое сообщение | {_md_cell(comm_block['text'])} | `{', '.join(comm_block['depends_on'])}` |",
            f"| Возможность продукта | {_md_cell(prod_block['text'])} | `{', '.join(prod_block['depends_on'])}` |",
            f"| Критерий остановки / граница | {_md_cell(exp_block['text'])} | `{', '.join(exp_block['depends_on'])}` |",
            "",
        ]

    lines += [
        "---",
        "",
        "## 4. Автоматический комплаенс-контроль (`audit-claims`) и метрики безопасности пилота",
        "",
    ]
    if claims_report["passed"]:
        lines.append(
            "- **Результат проверки стоп-листа**: безусловных гарантий (`«100%»`, `«абсолютно всегда»`, "
            "`«от любых угроз без исключений»`) и ссылок на модельных респондентов как на факт спроса **не обнаружено**."
        )
    else:
        for warn in claims_report["warnings"]:
            lines.append(f"- **Предупреждение (`{warn['severity']}`, `{warn['code']}`)**: {warn['message']}")
    lines += [
        f"- **Синхронизация с планом проверки и порогами жалоб**: {exp_block['text']}",
        "",
        "---",
        "",
        "## 5. Сводка телеметрии подготовки решения",
        "",
        *_format_cost_lines(cost_summary),
        "",
    ]
    return "\n".join(lines)


def _md_inline_html(text: str) -> str:
    s = escape(text)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def markdown_to_styled_html(md_text: str, doc_title: str) -> str:
    """Convert structured strategy Markdown into clean A4 print HTML."""
    lines = md_text.splitlines()
    out: list[str] = []
    in_ul = False
    in_ol = False
    in_table = False
    in_code = False

    def close_blocks() -> None:
        nonlocal in_ul, in_ol, in_table
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    def split_table_row(row_str: str) -> list[str]:
        inner = row_str.strip()
        if inner.startswith("|"):
            inner = inner[1:]
        if inner.endswith("|"):
            inner = inner[:-1]
        parts = re.split(r"(?<!\\)\|", inner)
        return [p.replace("\\|", "|").strip() for p in parts]

    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if s.startswith("```"):
            close_blocks()
            if not in_code:
                in_code = True
                out.append("<pre><code>")
            else:
                in_code = False
                out.append("</code></pre>")
            i += 1
            continue
        if in_code:
            out.append(escape(raw))
            i += 1
            continue
        if not s:
            close_blocks()
            i += 1
            continue
        if s == "---":
            close_blocks()
            out.append("<hr/>")
            i += 1
            continue
        if s.startswith("|") and s.endswith("|"):
            if not in_table:
                close_blocks()
                headers = [_md_inline_html(c) for c in split_table_row(s)]
                out.append("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead><tbody>")
                in_table = True
                if i + 1 < len(lines) and re.match(r"^\|[\s:\-\|]+\|$", lines[i + 1].strip()):
                    i += 2
                    continue
            else:
                cells = [_md_inline_html(c) for c in split_table_row(s)]
                out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            i += 1
            continue
        close_blocks()
        if s.startswith("# "):
            out.append(f"<h1>{_md_inline_html(s[2:])}</h1>")
        elif s.startswith("## "):
            out.append(f"<h2>{_md_inline_html(s[3:])}</h2>")
        elif s.startswith("### "):
            out.append(f"<h3>{_md_inline_html(s[4:])}</h3>")
        elif s.startswith("#### "):
            out.append(f"<h4>{_md_inline_html(s[5:])}</h4>")
        elif s.startswith("> "):
            out.append(f"<blockquote>{_md_inline_html(s[2:])}</blockquote>")
        elif re.match(r"^[-*]\s+", s):
            out.append("<ul>")
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                item = re.sub(r"^\s*[-*]\s+", "", lines[i])
                out.append(f"<li>{_md_inline_html(item)}</li>")
                i += 1
            out.append("</ul>")
            continue
        elif re.match(r"^\d+\.\s+", s):
            out.append("<ol>")
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                item = re.sub(r"^\s*\d+\.\s+", "", lines[i])
                out.append(f"<li>{_md_inline_html(item)}</li>")
                i += 1
            out.append("</ol>")
            continue
        else:
            out.append(f"<p>{_md_inline_html(s)}</p>")
        i += 1
    close_blocks()
    body = "\n".join(out)
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"/><title>{escape(doc_title)}</title>
<style>
  @page {{ size: A4; margin: 14mm 13mm 15mm 13mm; }}
  body {{
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #0F172A; font-size: 9.6pt; line-height: 1.45; margin: 0;
    font-variant-numeric: tabular-nums;
  }}
  h1 {{
    font-family: "Inter Display", "Inter", -apple-system, sans-serif;
    font-size: 16.5pt; line-height: 1.18; letter-spacing: -0.02em;
    margin: 0 0 8pt; color: #0F172A; border-bottom: 1.5pt solid #0F766E; padding-bottom: 5pt;
  }}
  h2 {{
    font-family: "Inter Display", "Inter", -apple-system, sans-serif;
    font-size: 12.2pt; letter-spacing: -0.01em; margin: 12pt 0 5pt; color: #0F766E; page-break-after: avoid;
  }}
  h3 {{ font-size: 10.3pt; margin: 8pt 0 4pt; color: #1E293B; page-break-after: avoid; }}
  p, li {{ margin: 0 0 4.5pt; }}
  ul, ol {{ margin: 0 0 6pt 15pt; padding: 0; }}
  blockquote {{ margin: 6pt 0; padding: 6pt 9pt; background: #F0FDFA; border-left: 3pt solid #0F766E; color: #1E293B; font-size: 9.1pt; }}
  table {{ width: 100%; border-collapse: collapse; margin: 6pt 0 8pt; font-size: 8.2pt; page-break-inside: auto; }}
  tr {{ page-break-inside: avoid; }}
  th, td {{ border: 0.6pt solid #CBD5E1; padding: 4pt 5.5pt; text-align: left; vertical-align: top; word-break: normal; overflow-wrap: break-word; }}
  th {{ background: #F8FAFC; font-weight: 700; color: #334155; }}
  td code {{ white-space: nowrap; }}
  code {{ font-family: "Source Code Pro", "DejaVu Sans Mono", monospace; font-size: 7.9pt; background: #F1F5F9; padding: 1pt 3pt; border-radius: 3pt; }}
  pre {{ font-family: "Source Code Pro", "DejaVu Sans Mono", monospace; background: #0F172A; color: #F8FAFC; padding: 7pt 9pt; border-radius: 5pt; overflow-x: auto; font-size: 8.0pt; }}
  pre code {{ background: none; padding: 0; color: inherit; }}
  hr {{ border: none; border-top: 0.7pt solid #E2E8F0; margin: 9pt 0; }}
  a {{ color: #0F766E; text-decoration: none; }}
</style></head><body>{body}</body></html>"""


def _find_browser_binary() -> str | None:
    for candidate in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _write_minimal_stdlib_pdf(md_text: str, out_pdf: Path) -> None:
    """Zero-dependency fallback PDF writer when no headless Chromium binary is installed."""
    # Transliterate/ASCII-safe fallback only if headless browser is absent, keeping valid multi-page PDF 1.4 structure.
    cyr_map = {
        "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "Yo", "Ж": "Zh", "З": "Z",
        "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R",
        "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Shch",
        "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "Yu", "Я": "Ya",
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z",
        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "№": "No.", "₽": "RUB",
        "—": "-", "–": "-", "«": '"', "»": '"', "…": "...", "·": "*", "↔": "<->",
    }

    def to_ascii(s: str) -> str:
        buf = []
        for ch in s:
            if ch in cyr_map:
                buf.append(cyr_map[ch])
            elif 32 <= ord(ch) <= 126:
                buf.append(ch)
            else:
                buf.append(" ")
        return "".join(buf)

    wrapped: list[str] = []
    for raw in md_text.splitlines():
        asc = to_ascii(raw)
        if not asc.strip():
            wrapped.append("")
            continue
        while len(asc) > 92:
            wrapped.append(asc[:92])
            asc = "  " + asc[92:]
        wrapped.append(asc)

    lines_per_page = 56
    pages = [wrapped[i : i + lines_per_page] for i in range(0, max(len(wrapped), 1), lines_per_page)]

    objects: list[bytes] = []
    # 1: Catalog, 2: Pages, 3: Font
    page_obj_ids = []
    next_id = 4
    content_streams: list[tuple[int, int, bytes]] = []
    for pg in pages:
        p_id = next_id
        c_id = next_id + 1
        next_id += 2
        page_obj_ids.append(p_id)
        cmds = [b"BT /F1 9 Tf 42 790 Td 13 TL"]
        for ln in pg:
            esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            cmds.append(f"({esc}) Tj T*".encode("ascii", errors="replace"))
        cmds.append(b"ET")
        stream_bytes = b"\n".join(cmds)
        content_streams.append((p_id, c_id, stream_bytes))

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_obj_ids)} >>".encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for p_id, c_id, s_bytes in content_streams:
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {c_id} 0 R >>".encode("ascii")
        )
        objects.append(f"<< /Length {len(s_bytes)} >>\nstream\n".encode("ascii") + s_bytes + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    out_pdf.write_bytes(bytes(out))


def render_markdown_to_pdf(md_text: str, out_pdf: Path, *, title: str) -> str:
    """Render Markdown to A4 PDF via headless Chromium/Chrome, or stdlib PDF fallback if absent."""
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    browser = _find_browser_binary()
    if browser:
        html_doc = markdown_to_styled_html(md_text, title)
        with tempfile.TemporaryDirectory() as td:
            html_path = Path(td) / "strategy.html"
            html_path.write_text(html_doc, encoding="utf-8")
            res = subprocess.run(
                [
                    browser,
                    "--headless",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--hide-scrollbars",
                    f"--print-to-pdf={out_pdf}",
                    "--no-pdf-header-footer",
                    html_path.resolve().as_uri(),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and out_pdf.is_file() and out_pdf.stat().st_size > 500:
                return "headless_chromium"
    _write_minimal_stdlib_pdf(md_text, out_pdf)
    return "stdlib_pdf_fallback"


def export_strategies(
    case: Path,
    state: dict,
    sources_health: dict,
    db=None,
    *,
    out_dir: Path | None = None,
    decision_rev: int | None = None,
    use_draft: bool = False,
    prefix: str = "strategy",
    include_pdf: bool = True,
) -> dict:
    """Export product & communication strategies (MD + PDF) from case.sqlite3 for any case."""
    decisions = state.get("decisions") or []
    draft = state.get("draft")

    selected_decision: dict | None = None
    if use_draft or (decision_rev is None and not decisions):
        if not draft:
            raise ValueError("Case has no staged draft to export")
        proposal = draft
    else:
        rev = decision_rev if decision_rev is not None else len(decisions)
        if not 1 <= rev <= len(decisions):
            raise ValueError(f"Decision revision {rev} does not exist")
        selected_decision = decisions[rev - 1]
        proposal = selected_decision["proposal"]

    dest_dir = (out_dir if out_dir is not None else (case / "strategies")).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    active_impact = impact(proposal, sources_health)
    claims_report = audit_claims(proposal, sources_health)
    cost_summary = summarize_case_cost(case, state.get("telemetry", []))
    econ_blocks = _extract_economics_blocks(case, state, db)

    prod_md = build_product_strategy_markdown(
        case=case,
        state=state,
        sources_health=sources_health,
        proposal=proposal,
        decision=selected_decision,
        active_impact=active_impact,
        cost_summary=cost_summary,
        econ_blocks=econ_blocks,
    )
    comms_md = build_comms_strategy_markdown(
        state=state,
        proposal=proposal,
        decision=selected_decision,
        claims_report=claims_report,
        cost_summary=cost_summary,
    )

    clean_prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", prefix.strip()).strip("-") or "strategy"
    prod_md_path = dest_dir / f"{clean_prefix}-product.md"
    comms_md_path = dest_dir / f"{clean_prefix}-comms.md"
    prod_md_path.write_text(prod_md, encoding="utf-8")
    comms_md_path.write_text(comms_md, encoding="utf-8")

    result = {
        "case": str(case),
        "status": f"decision_r{selected_decision['revision']}" if selected_decision else "draft",
        "recommended": proposal["recommended"],
        "product_md": str(prod_md_path),
        "comms_md": str(comms_md_path),
        "product_pdf": None,
        "comms_pdf": None,
        "pdf_renderer": None,
        "claims_passed": claims_report["passed"],
    }

    if include_pdf:
        prod_pdf_path = dest_dir / f"{clean_prefix}-product.pdf"
        comms_pdf_path = dest_dir / f"{clean_prefix}-comms.pdf"
        renderer = render_markdown_to_pdf(
            prod_md, prod_pdf_path, title=f"Продуктовая стратегия — {proposal['title']}"
        )
        render_markdown_to_pdf(
            comms_md, comms_pdf_path, title=f"Коммуникационная стратегия — {proposal['title']}"
        )
        result["product_pdf"] = str(prod_pdf_path)
        result["comms_pdf"] = str(comms_pdf_path)
        result["pdf_renderer"] = renderer

    return result
