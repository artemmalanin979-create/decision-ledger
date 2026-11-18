"""Validate a recommendation and trace changes through its evidence links."""

from datetime import date

KINDS = {"fact", "observation", "inference", "hypothesis", "forecast", "unknown"}
BLOCKS = {"product", "communication", "experiment"}
LEVELS = {"low", "medium", "high"}


def required_text(record, *fields):
    if not isinstance(record, dict):
        raise ValueError("Expected an object")
    for field in fields:
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"Missing or empty field: {field}")


def require_map(record, field):
    value = record.get(field)
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def dependencies(record, findings, *, required=False):
    links = record.get("depends_on", [])
    if not isinstance(links, list) or any(not isinstance(link, str) or link not in findings for link in links):
        raise ValueError("depends_on must reference existing finding IDs")
    if required and not links:
        raise ValueError("At least one finding is required in depends_on")
    return links


def validate_methodology(methodology, findings, options):
    if methodology is None:
        return
    if not isinstance(methodology, dict):
        raise ValueError("methodology must be an object")

    if "jtbd" in methodology:
        jtbd = methodology["jtbd"]
        required_text(jtbd, "payer_job", "user_job")
        forces = require_map(jtbd, "forces")
        required_text(forces, "push", "pull", "anxiety", "habit")
        dependencies(jtbd, findings, required=True)

    if "ost" in methodology:
        ost = methodology["ost"]
        required_text(ost, "outcome")
        opps = ost.get("opportunities")
        if not isinstance(opps, list) or not opps:
            raise ValueError("methodology.ost.opportunities must be a non-empty list")
        seen_opps = set()
        for opp in opps:
            required_text(opp, "id", "statement")
            if opp["id"] in seen_opps:
                raise ValueError(f"Duplicate OST opportunity ID: {opp['id']}")
            seen_opps.add(opp["id"])
            dependencies(opp, findings, required=True)
            sols = opp.get("solutions")
            if not isinstance(sols, list) or not sols:
                raise ValueError(f"OST opportunity {opp['id']} must list at least one solution")
            for sol in sols:
                required_text(sol, "option_id", "assumption_test")
                if sol["option_id"] not in options:
                    raise ValueError(f"OST solution references unknown option: {sol['option_id']}")

    if "premortem" in methodology:
        pm = methodology["premortem"]
        if not isinstance(pm, list) or len(pm) < 3:
            raise ValueError("methodology.premortem must contain at least 3 failure scenarios")
        for row in pm:
            required_text(row, "failure_mode", "probability", "impact", "preventability", "mitigation")
            for lvl_field in ("probability", "impact", "preventability"):
                if row[lvl_field] not in LEVELS:
                    raise ValueError(f"Invalid level in premortem.{lvl_field}: {row[lvl_field]}")
            dependencies(row, findings, required=True)

    if "promise_matrix" in methodology:
        matrix = methodology["promise_matrix"]
        if not isinstance(matrix, list) or not matrix:
            raise ValueError("methodology.promise_matrix must be a non-empty list")
        for row in matrix:
            required_text(row, "promise", "capability", "boundary", "channel")
            proofs = row.get("proof_findings")
            if not isinstance(proofs, list) or not proofs or any(p not in findings for p in proofs):
                raise ValueError("promise_matrix.proof_findings must reference existing finding IDs")


def validate(proposal, sources):
    required_text(proposal, "title", "summary", "recommended")
    reviewed = require_map(proposal, "reviewed")
    active_sources = {key for key, source in sources.items() if source["health"] != "retired"}
    if not reviewed or reviewed.keys() != active_sources:
        raise ValueError("Review all active sources before proposing")
    for key, review in reviewed.items():
        required_text(review, "sha", "note")
        if review["sha"] != sources[key]["sha"] or sources[key]["health"] != "ok":
            raise ValueError(f"Source {key} has changed or is unavailable")

    findings = require_map(proposal, "findings")
    if not findings:
        raise ValueError("At least one finding is required")
    for key, finding in findings.items():
        required_text(finding, "text", "kind")
        if finding["kind"] not in KINDS:
            raise ValueError(f"Invalid finding kind: {key}")
        links = dependencies(finding, findings)
        citations = finding.get("citations", [])
        if not isinstance(citations, list):
            raise ValueError(f"citations must be a list: {key}")
        for citation in citations:
            required_text(citation, "source", "locator")
            if citation["source"] not in reviewed:
                raise ValueError(f"Unknown source: {citation['source']}")
            if finding["kind"] == "fact" and sources[citation["source"]]["kind"] == "synthetic":
                raise ValueError("Synthetic data support observations inside the scenario, never external facts")
        if finding["kind"] in {"fact", "observation"} and not citations:
            raise ValueError(f"Missing direct citation: {key}")
        if finding["kind"] == "inference" and not (citations or links):
            raise ValueError(f"Missing inference basis: {key}")

    # A topological walk also detects cycles without relying on recursion.
    order_findings(findings)
    options = require_map(proposal, "options")
    types = set()
    for option in options.values():
        required_text(option, "title", "type", "case", "objection")
        if option["type"] not in {"keep", "change", "close"}:
            raise ValueError("Invalid option type")
        types.add(option["type"])
        dependencies(option, findings, required=True)
    if types != {"keep", "change", "close"} or proposal["recommended"] not in options:
        raise ValueError("Provide keep, change and close options and name the recommended option")

    strategy = require_map(proposal, "strategy")
    if strategy.keys() != BLOCKS:
        raise ValueError("Strategy requires product, communication and experiment")
    for block in strategy.values():
        required_text(block, "text")
        dependencies(block, findings, required=True)

    validate_methodology(proposal.get("methodology"), findings, options)

    actions = proposal.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("Include at least one follow-up action")
    ids = set()
    for action in actions:
        required_text(action, "id", "owner", "due", "task", "trigger")
        if date.fromisoformat(action["due"]).isoformat() != action["due"]:
            raise ValueError("Use YYYY-MM-DD for action due dates")
        if action["id"] in ids or "completion" in action:
            raise ValueError("Actions must be unique and start open")
        ids.add(action["id"])


def order_findings(findings):
    pending = dict(findings)
    ordered = {}
    while pending:
        ready = [key for key, finding in pending.items() if ordered.keys() >= set(finding.get("depends_on", []))]
        if not ready:
            raise ValueError("Findings contain a dependency cycle")
        for key in ready:
            ordered[key] = pending.pop(key)
    return ordered


def impact(proposal, sources):
    unreviewed = sorted(
        key for key, source in sources.items()
        if source["health"] != "retired" and key not in proposal["reviewed"]
    )
    changed = {
        key for key, review in proposal["reviewed"].items()
        if key not in sources or sources[key]["health"] != "ok" or sources[key]["sha"] != review["sha"]
    }
    seeded_findings = set()
    for key in unreviewed:
        for target in sources[key].get("updates") or []:
            if target in proposal["reviewed"]:
                changed.add(target)
            elif target in proposal["findings"]:
                seeded_findings.add(target)

    affected = set(seeded_findings)
    for key, finding in order_findings(proposal["findings"]).items():
        cited = {citation["source"] for citation in finding.get("citations", [])}
        if key in seeded_findings or (changed & cited) or affected.intersection(finding.get("depends_on", [])):
            affected.add(key)

    methodology_affected = []
    meth = proposal.get("methodology") or {}
    if "jtbd" in meth and affected.intersection(meth["jtbd"].get("depends_on", [])):
        methodology_affected.append("jtbd")
    for opp in (meth.get("ost") or {}).get("opportunities", []):
        if affected.intersection(opp.get("depends_on", [])):
            methodology_affected.append(f"ost:{opp['id']}")
    for idx, row in enumerate(meth.get("premortem") or [], 1):
        if affected.intersection(row.get("depends_on", [])):
            methodology_affected.append(f"premortem:{idx}")
    for idx, row in enumerate(meth.get("promise_matrix") or [], 1):
        if affected.intersection(row.get("proof_findings", [])):
            methodology_affected.append(f"promise_matrix:{idx}")

    res = {
        "sources": sorted(changed),
        "unreviewed": unreviewed,
        "findings": sorted(affected),
        "options": sorted(key for key, option in proposal["options"].items()
                          if affected.intersection(option["depends_on"])),
        "strategy": sorted(key for key, block in proposal["strategy"].items()
                           if affected.intersection(block["depends_on"])),
    }
    if meth:
        res["methodology"] = methodology_affected
    return res


def render_cost_section(cost_summary: dict) -> list[str]:
    """Markdown lines for the cost/human-time section (money is null for subscription quotas)."""
    est_tag = "оценка по публичному тарифу (`estimated: true`)" if cost_summary.get("estimated") else "прямой замер"
    if cost_summary.get("human_minutes") is None:
        human_str = "`не измерялось`"
    elif cost_summary.get("total_decision_cost_rub") is None:
        human_str = f"`{cost_summary['human_minutes']} мин` (`{cost_summary['human_cost_rub']:.2f} ₽`)"
    else:
        human_str = (
            f"`{cost_summary['human_minutes']} мин` (`{cost_summary['human_cost_rub']:.2f} ₽`) · "
            f"полная стоимость решения: **`{cost_summary['total_decision_cost_rub']:.2f} ₽`**"
        )
    methods_str = "; ".join(cost_summary.get("methods") or [])
    out = [
        "## Прозрачность затрат и участие человека",
        "",
        f"- Шагов в журнале `{cost_summary['log_file']}`: `{cost_summary['steps']}` · время прогонов: `{cost_summary['wall_seconds']} с` · вызовы инструментов: `{cost_summary.get('tool_calls', 0)}` · повторов: `{cost_summary['retries']}`.",
        f"- Токены: полный вход `{cost_summary['input_tokens']}` (из кэша `{cost_summary['cached_tokens']}`, hit `{round(cost_summary['cache_hit_ratio']*100, 1)}%`), выход `{cost_summary['output_tokens']}`.",
        (
            f"- Стоимость в ₽: `не рассчитывается` (квота подписки IDE, модель в журнале хоста не указана) · участие человека: {human_str}."
            if cost_summary.get("api_cost_rub") is None
            else f"- Эквивалент API ({est_tag}): `${cost_summary['api_cost_usd']:.4f}` (`{cost_summary['api_cost_rub']:.2f} ₽`) · участие человека: {human_str}."
        ),
    ]
    if methods_str:
        out.append(f"- Методика учёта: {methods_str}")
    return out


def render(proposal, source_records, decision=None, cost_summary=None):
    lines = [f"# {proposal['title']}", ""]
    if decision:
        lines += [f"Решение №{decision['revision']} · {decision['by']} · {decision['at']}",
                  "", f"Основание выбора: {decision['reason']}", ""]
    else:
        lines += ["Статус: рекомендация на согласовании", ""]
    lines += [proposal["summary"], ""]

    meth = proposal.get("methodology") or {}
    if "jtbd" in meth:
        jtbd = meth["jtbd"]
        forces = {k: str(v).strip().rstrip(". ") for k, v in jtbd["forces"].items()}
        lines += [
            "## Задача аудитории (JTBD)",
            "",
            f"- **Покупатель (плательщик)**: {jtbd['payer_job']}",
            f"- **Конечный пользователь**: {jtbd['user_job']}",
            f"- **Силы переключения**: толчок — {forces['push']}; притяжение — {forces['pull']}; "
            f"тревога — {forces['anxiety']}; инерция — {forces['habit']}.",
            "",
        ]

    if "ost" in meth:
        ost = meth["ost"]
        lines += ["## Дерево возможностей и решений (Opportunity Solution Tree)", "",
                  f"**Целевой исход**: {ost['outcome']}", ""]
        for opp in ost["opportunities"]:
            lines.append(f"- **Возможность `{opp['id']}`**: {opp['statement']} *(опора: {', '.join(opp['depends_on'])})*")
            for sol in opp["solutions"]:
                lines.append(f"  - Решение `{sol['option_id']}` -> тест допущения: {sol['assumption_test']}")
        lines.append("")

    lines += ["## Варианты", ""]
    for key, option in proposal["options"].items():
        chosen = " · выбранный" if key == proposal["recommended"] else ""
        lines += [f"### {option['title']}{chosen}", "", option["case"], "",
                  f"Сильное возражение — {option['objection']}", "",
                  "Основания — " + ", ".join(option["depends_on"]), ""]
    for key, label in (("product", "Продуктовая стратегия"),
                       ("communication", "Коммуникационная стратегия"),
                       ("experiment", "Проверка")):
        block = proposal["strategy"][key]
        lines += [f"## {label}", "", block["text"], "", "Основания — " + ", ".join(block["depends_on"]), ""]

    if "promise_matrix" in meth:
        lines += [
            "## Матрица «Обещание коммуникации ↔ Возможность продукта»",
            "",
            "| Обещание аудитории | Возможность продукта | Основания | Честная граница | Канал |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in meth["promise_matrix"]:
            lines.append(
                f"| {row['promise']} | {row['capability']} | {', '.join(row['proof_findings'])} "
                f"| {row['boundary']} | {row['channel']} |"
            )
        lines.append("")

    if "premortem" in meth:
        lines += [
            "## Pre-mortem выбранного варианта (через 6 месяцев)",
            "",
            "| Сценарий провала | Вероятность | Влияние | Предотвратимость | Защитная мера |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in meth["premortem"]:
            lines.append(
                f"| {row['failure_mode']} | {row['probability']} | {row['impact']} "
                f"| {row['preventability']} | {row['mitigation']} |"
            )
        lines.append("")

    labels = {
        "fact": "факт", "observation": "наблюдение в данных", "inference": "вывод",
        "hypothesis": "гипотеза", "forecast": "прогноз", "unknown": "неизвестно",
    }
    lines += ["## Основания", ""]
    for key, finding in proposal["findings"].items():
        citations = [f"{item['source']} · {item['locator']}" for item in finding.get("citations", [])]
        references = citations + finding.get("depends_on", [])
        suffix = f" Основания — {'; '.join(references)}." if references else ""
        lines.append(f"- **{key} · {labels[finding['kind']]}**. {finding['text']}{suffix}")
    lines += ["", "## Источники", ""]
    for key, review in proposal["reviewed"].items():
        record = source_records[key]
        version = next(version for version in record["versions"] if version["sha"] == review["sha"])
        url = f" · [открыть сайт]({record['url']})" if record["url"] else ""
        lines.append(f"- **{key}** · `{version['path']}` · {record['kind']}{url}. {review['note']} "
                     f"Снимок `{review['sha']}`.")
    lines += ["", "## Обязательства", ""]
    for action in proposal["actions"]:
        completion = decision.get("completions", {}).get(action["id"]) if decision else None
        state = f"выполнено ({completion['note']})" if completion else "открыто"
        lines.append(f"- **{action['id']}** · {action['owner']} · до {action['due']} · {state}. "
                     f"{action['task']} Условие пересмотра — {action['trigger']}.")

    if cost_summary and cost_summary.get("steps", 0) > 0:
        lines += [""] + render_cost_section(cost_summary)
    return "\n".join(lines) + "\n"
