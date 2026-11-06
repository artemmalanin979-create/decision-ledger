"""Validate a recommendation and trace changes through its evidence links."""

from datetime import date

KINDS = {"fact", "observation", "inference", "hypothesis", "forecast", "unknown"}
BLOCKS = {"product", "communication", "experiment"}


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


def validate(proposal, sources):
    required_text(proposal, "title", "summary", "recommended")
    reviewed = require_map(proposal, "reviewed")
    if not reviewed or reviewed.keys() != sources.keys():
        raise ValueError("Review all registered sources before proposing")
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
    changed = {
        key for key, review in proposal["reviewed"].items()
        if key not in sources or sources[key]["health"] != "ok" or sources[key]["sha"] != review["sha"]
    }
    affected = set()
    for key, finding in order_findings(proposal["findings"]).items():
        cited = {citation["source"] for citation in finding.get("citations", [])}
        if changed & cited or affected.intersection(finding.get("depends_on", [])):
            affected.add(key)
    return {
        "sources": sorted(changed),
        "unreviewed": sorted(sources.keys() - proposal["reviewed"].keys()),
        "findings": sorted(affected),
        "options": sorted(key for key, option in proposal["options"].items()
                          if affected.intersection(option["depends_on"])),
        "strategy": sorted(key for key, block in proposal["strategy"].items()
                           if affected.intersection(block["depends_on"])),
    }


def render(proposal, source_records, decision=None):
    lines = [f"# {proposal['title']}", ""]
    if decision:
        lines += [f"Решение №{decision['revision']} · {decision['by']} · {decision['at']}",
                  "", f"Основание выбора: {decision['reason']}", ""]
    else:
        lines += ["Статус: рекомендация на согласовании", ""]
    lines += [proposal["summary"], "", "## Варианты", ""]
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
        completion = action.get("completion")
        state = f"выполнено ({completion['note']})" if completion else "открыто"
        lines.append(f"- **{action['id']}** · {action['owner']} · до {action['due']} · {state}. "
                     f"{action['task']} Условие пересмотра — {action['trigger']}.")
    return "\n".join(lines) + "\n"
