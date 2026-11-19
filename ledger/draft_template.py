"""Zero-dependency YAML draft template generator and compiler for fast proposal authoring."""

from __future__ import annotations

import json
from pathlib import Path
import re


def _strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_double:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or line[idx - 1].isspace():
                return line[:idx].rstrip()
    return line.rstrip()


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    depth_brack = 0
    depth_brace = 0
    for ch in text:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_double:
            buf.append(ch)
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
            continue
        if not in_single and not in_double:
            if ch == "[":
                depth_brack += 1
            elif ch == "]":
                depth_brack = max(0, depth_brack - 1)
            elif ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace = max(0, depth_brace - 1)
            elif ch == sep and depth_brack == 0 and depth_brace == 0:
                parts.append("".join(buf).strip())
                buf = []
                continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail or parts:
        parts.append(tail)
    return [p for p in parts if p != ""]


def _split_key_value(content: str) -> tuple[str, str] | None:
    in_single = False
    in_double = False
    escaped = False
    depth_brack = 0
    depth_brace = 0
    for idx, ch in enumerate(content):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_double:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch == "[":
                depth_brack += 1
            elif ch == "]":
                depth_brack = max(0, depth_brack - 1)
            elif ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace = max(0, depth_brace - 1)
            elif ch == ":" and depth_brack == 0 and depth_brace == 0:
                if idx + 1 == len(content) or content[idx + 1].isspace():
                    raw_key = content[:idx].strip()
                    val = content[idx + 1 :].strip()
                    key = _unquote_scalar(raw_key)
                    return str(key), val
    return None


def _unquote_scalar(token: str) -> str:
    s = token.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            return json.loads(s)
        except ValueError:
            return s[1:-1]
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def _parse_inline_value(token: str):
    s = token.strip()
    if s == "":
        return ""
    if (len(s) >= 2 and s[0] == '"' and s[-1] == '"') or (len(s) >= 2 and s[0] == "'" and s[-1] == "'"):
        return _unquote_scalar(s)
    if s in {"null", "~"}:
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_inline_value(item) for item in _split_top_level(inner, ",")]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        out = {}
        for pair in _split_top_level(inner, ","):
            kv = _split_key_value(pair)
            if kv is None:
                raise ValueError(f"Invalid inline mapping entry in YAML: {pair!r}")
            k, v = kv
            out[k] = _parse_inline_value(v)
        return out
    if re.fullmatch(r"[+-]?\d+", s):
        return int(s)
    if re.fullmatch(r"[+-]?\d+\.\d+", s):
        return float(s)
    return s


class _YamlStream:
    def __init__(self, raw_lines: list[str]):
        self.raw_lines = raw_lines
        self.index = 0
        self.total = len(raw_lines)

    def peek_effective(self) -> tuple[int, int, str] | None:
        idx = self.index
        while idx < self.total:
            raw = self.raw_lines[idx]
            if "\t" in raw[: len(raw) - len(raw.lstrip())]:
                raise ValueError(f"YAML line {idx + 1}: use spaces instead of tabs for indentation")
            stripped_comment = _strip_inline_comment(raw)
            if stripped_comment.strip() == "" or stripped_comment.strip() == "---":
                idx += 1
                continue
            indent = len(stripped_comment) - len(stripped_comment.lstrip(" "))
            return idx, indent, stripped_comment.lstrip(" ")
        return None

    def read_block_scalar(self, parent_indent: int, style: str) -> str:
        folded = style.startswith(">")
        keep_trailing = style.endswith("+")
        strip_trailing = style.endswith("-")
        collected: list[tuple[int, str]] = []
        block_indent: int | None = None

        while self.index < self.total:
            raw = self.raw_lines[self.index]
            if raw.strip() == "":
                collected.append((0, ""))
                self.index += 1
                continue
            cur_indent = len(raw) - len(raw.lstrip(" "))
            if cur_indent <= parent_indent:
                break
            if block_indent is None:
                block_indent = cur_indent
            rel_text = raw[block_indent:] if len(raw) >= block_indent else raw.lstrip(" ")
            collected.append((cur_indent, rel_text.rstrip("\r\n")))
            self.index += 1

        while collected and collected[-1][1] == "" and not keep_trailing:
            collected.pop()

        lines = [txt for _, txt in collected]
        if not lines:
            return ""
        if folded:
            paragraphs: list[str] = []
            buf: list[str] = []
            for ln in lines:
                if ln == "":
                    if buf:
                        paragraphs.append(" ".join(buf))
                        buf = []
                    paragraphs.append("")
                else:
                    buf.append(ln.strip())
            if buf:
                paragraphs.append(" ".join(buf))
            text = "\n".join(paragraphs)
        else:
            text = "\n".join(lines)

        if keep_trailing:
            return text + "\n"
        if strip_trailing:
            return text
        return text


def _parse_block(stream: _YamlStream, min_indent: int):
    peek = stream.peek_effective()
    if peek is None or peek[1] < min_indent:
        return {}
    _, base_indent, content = peek
    if content == "-" or content.startswith("- "):
        return _parse_sequence(stream, base_indent)
    return _parse_mapping(stream, base_indent)


def _parse_sequence(stream: _YamlStream, seq_indent: int) -> list:
    items: list = []
    while True:
        peek = stream.peek_effective()
        if peek is None:
            break
        line_idx, indent, content = peek
        if indent < seq_indent:
            break
        if indent > seq_indent:
            raise ValueError(f"YAML line {line_idx + 1}: unexpected indentation inside list")
        if not (content == "-" or content.startswith("- ")):
            break

        stream.index = line_idx + 1
        rest = content[1:].lstrip(" ") if len(content) > 1 else ""
        if rest == "":
            nested_peek = stream.peek_effective()
            if nested_peek is None or nested_peek[1] <= seq_indent:
                items.append(None)
            else:
                items.append(_parse_block(stream, seq_indent + 1))
            continue

        if rest in {"|", "|-", "|+", ">", ">-", ">+"}:
            items.append(stream.read_block_scalar(seq_indent, rest))
            continue

        kv = _split_key_value(rest)
        # Distinguish `- key: val` object entry from `- "src: locator"` quoted string or `- src: locator` citation shorthand!
        # Wait: in `cite:\n - src_id: locator`, if a list item has a single `k: v` and NO sibling keys on following lines,
        # wait: `- id: a1\n  owner: PM` IS a dict, while `- cbr_2025: раздел 1` in `cite:` could become `{"cbr_2025": "раздел 1"}`!
        # Supporting `{"cbr_2025": "раздел 1"}` directly in `_normalize_citation` makes BOTH work seamlessly!
        if kv is not None and not rest.startswith(("'", '"')):
            item_indent = indent + 2
            k, v_str = kv
            obj: dict = {}
            obj[k] = _resolve_mapping_value(stream, item_indent, v_str)
            while True:
                sub_peek = stream.peek_effective()
                if sub_peek is None or sub_peek[1] < item_indent:
                    break
                if sub_peek[1] > item_indent:
                    raise ValueError(f"YAML line {sub_peek[0] + 1}: unexpected indentation in list mapping")
                sub_idx, _, sub_content = sub_peek
                if sub_content == "-" or sub_content.startswith("- "):
                    break
                sub_kv = _split_key_value(sub_content)
                if sub_kv is None:
                    raise ValueError(f"YAML line {sub_idx + 1}: expected key: value, got {sub_content!r}")
                stream.index = sub_idx + 1
                sk, sv = sub_kv
                obj[sk] = _resolve_mapping_value(stream, item_indent, sv)
            items.append(obj)
        else:
            items.append(_parse_inline_value(rest))
    return items


def _resolve_mapping_value(stream: _YamlStream, key_indent: int, v_str: str):
    if v_str in {"|", "|-", "|+", ">", ">-", ">+"}:
        return stream.read_block_scalar(key_indent, v_str)
    if v_str == "":
        nxt = stream.peek_effective()
        if nxt is None or nxt[1] <= key_indent:
            return None
        return _parse_block(stream, key_indent + 1)
    return _parse_inline_value(v_str)


def _parse_mapping(stream: _YamlStream, map_indent: int) -> dict:
    mapping: dict = {}
    while True:
        peek = stream.peek_effective()
        if peek is None:
            break
        line_idx, indent, content = peek
        if indent < map_indent:
            break
        if indent > map_indent:
            raise ValueError(f"YAML line {line_idx + 1}: unexpected indentation ({indent} > {map_indent})")
        if content == "-" or content.startswith("- "):
            break
        kv = _split_key_value(content)
        if kv is None:
            raise ValueError(f"YAML line {line_idx + 1}: expected 'key: value', got {content!r}")
        stream.index = line_idx + 1
        key, v_str = kv
        mapping[key] = _resolve_mapping_value(stream, map_indent, v_str)
    return mapping


def parse_simple_yaml(text: str) -> dict:
    """Parse JSON or clean indentation-based YAML into a Python dictionary using stdlib only."""
    cleaned = text.lstrip("\ufeff")
    stripped = cleaned.strip()
    if not stripped:
        raise ValueError("Draft file is empty")
    if stripped.startswith("{"):
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("Proposal root must be an object")
        return data
    stream = _YamlStream(cleaned.splitlines())
    result = _parse_block(stream, 0)
    extra = stream.peek_effective()
    if extra is not None:
        raise ValueError(f"YAML line {extra[0] + 1}: trailing unparsed content {extra[2]!r}")
    if not isinstance(result, dict):
        raise ValueError("YAML draft root must be a mapping")
    return result


def _as_str_list(val) -> list[str]:
    if val is None or val == "":
        return []
    if isinstance(val, str):
        return [s.strip() for s in val.split(",") if s.strip()]
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    raise ValueError(f"Expected list of IDs, got {val!r}")


def _normalize_citation(item) -> dict:
    if isinstance(item, dict):
        if "source" in item and "locator" in item:
            return {"source": str(item["source"]).strip(), "locator": str(item["locator"]).strip()}
        if len(item) == 1:
            src, loc = next(iter(item.items()))
            return {"source": str(src).strip(), "locator": str(loc).strip()}
        raise ValueError(f"Invalid citation mapping: {item!r}")
    if isinstance(item, str):
        if ":" in item:
            src, loc = item.split(":", 1)
            return {"source": src.strip(), "locator": loc.strip() or "документ целиком"}
        return {"source": item.strip(), "locator": "документ целиком"}
    raise ValueError(f"Unsupported citation entry: {item!r}")


def compile_draft_template(raw_text: str, sources_health: dict) -> dict:
    """Compile a compact YAML/JSON draft template into a canonical Decision Ledger proposal dict."""
    doc = parse_simple_yaml(raw_text)

    active_sources = {
        sid: info for sid, info in sorted(sources_health.items()) if info.get("health") != "retired"
    }
    bad_sources = [f"{sid} ({info.get('health')})" for sid, info in active_sources.items() if info.get("health") != "ok"]
    if bad_sources:
        raise ValueError(f"Cannot compile draft while active sources are unhealthy: {', '.join(bad_sources)}")

    raw_reviewed = doc.get("reviewed", "auto")
    review_notes = doc.get("review_notes") if isinstance(doc.get("review_notes"), dict) else {}
    compiled_reviewed: dict[str, dict[str, str]] = {}

    if raw_reviewed == "auto" or raw_reviewed is True or raw_reviewed is None:
        for sid, info in active_sources.items():
            note = str(review_notes.get(sid) or f"Проверено по актуальному снимку ({info['path']})").strip()
            compiled_reviewed[sid] = {"sha": info["sha"], "note": note}
    elif isinstance(raw_reviewed, dict):
        fill_missing = bool(raw_reviewed.get("_auto") or doc.get("auto_review_all", False))
        for sid, entry in raw_reviewed.items():
            if sid == "_auto":
                continue
            if isinstance(entry, str):
                if sid not in active_sources:
                    raise ValueError(f"Unknown or retired source in reviewed: {sid}")
                compiled_reviewed[sid] = {"sha": active_sources[sid]["sha"], "note": entry.strip()}
            elif isinstance(entry, dict):
                sha = str(entry.get("sha") or "").strip()
                if not sha or sha == "auto":
                    if sid not in active_sources:
                        raise ValueError(f"Unknown or retired source in reviewed: {sid}")
                    sha = active_sources[sid]["sha"]
                note = str(entry.get("note") or f"Проверено ({active_sources.get(sid, {}).get('path', sid)})").strip()
                compiled_reviewed[sid] = {"sha": sha, "note": note}
            else:
                raise ValueError(f"Invalid reviewed entry for {sid!r}")
        if fill_missing:
            for sid, info in active_sources.items():
                compiled_reviewed.setdefault(
                    sid,
                    {"sha": info["sha"], "note": f"Проверено по актуальному снимку ({info['path']})"},
                )
    else:
        raise ValueError("reviewed must be 'auto' or an object mapping source IDs to notes/objects")

    raw_findings = doc.get("findings")
    if not isinstance(raw_findings, dict) or not raw_findings:
        raise ValueError("Draft requires a non-empty 'findings' mapping")
    compiled_findings: dict[str, dict] = {}
    for fid, fval in raw_findings.items():
        if not isinstance(fval, dict):
            raise ValueError(f"Finding {fid!r} must be an object")
        f_out: dict = {
            "kind": str(fval.get("kind") or "").strip(),
            "text": str(fval.get("text") or "").strip(),
        }
        raw_cites = fval.get("citations") if "citations" in fval else fval.get("cite")
        if raw_cites is not None:
            if isinstance(raw_cites, (str, dict)):
                raw_cites = [raw_cites]
            if not isinstance(raw_cites, list):
                raise ValueError(f"citations/cite in finding {fid!r} must be a list")
            f_out["citations"] = [_normalize_citation(c) for c in raw_cites]
        raw_deps = fval.get("depends_on") if "depends_on" in fval else fval.get("deps")
        deps_list = _as_str_list(raw_deps)
        if deps_list:
            f_out["depends_on"] = deps_list
        compiled_findings[str(fid)] = f_out

    raw_options = doc.get("options")
    if not isinstance(raw_options, dict) or not raw_options:
        raise ValueError("Draft requires a non-empty 'options' mapping")
    compiled_options: dict[str, dict] = {}
    for oid, oval in raw_options.items():
        if not isinstance(oval, dict):
            raise ValueError(f"Option {oid!r} must be an object")
        deps = _as_str_list(oval.get("depends_on") if "depends_on" in oval else oval.get("deps"))
        compiled_options[str(oid)] = {
            "title": str(oval.get("title") or "").strip(),
            "type": str(oval.get("type") or "").strip(),
            "case": str(oval.get("case") or "").strip(),
            "objection": str(oval.get("objection") or "").strip(),
            "depends_on": deps,
        }

    raw_strategy = doc.get("strategy")
    if not isinstance(raw_strategy, dict):
        raise ValueError("Draft requires a 'strategy' mapping")
    compiled_strategy: dict[str, dict] = {}
    for skey, sval in raw_strategy.items():
        if isinstance(sval, str):
            raise ValueError(f"strategy.{skey} requires both 'text' and 'deps'/'depends_on'")
        if not isinstance(sval, dict):
            raise ValueError(f"strategy.{skey} must be an object")
        deps = _as_str_list(sval.get("depends_on") if "depends_on" in sval else sval.get("deps"))
        compiled_strategy[str(skey)] = {
            "text": str(sval.get("text") or "").strip(),
            "depends_on": deps,
        }

    raw_actions = doc.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ValueError("Draft requires a non-empty 'actions' list")
    compiled_actions = []
    for act in raw_actions:
        if not isinstance(act, dict):
            raise ValueError("Each action must be an object")
        compiled_actions.append({
            "id": str(act.get("id") or "").strip(),
            "owner": str(act.get("owner") or "").strip(),
            "due": str(act.get("due") or "").strip(),
            "task": str(act.get("task") or "").strip(),
            "trigger": str(act.get("trigger") or "").strip(),
        })

    proposal: dict = {
        "title": str(doc.get("title") or "").strip(),
        "summary": str(doc.get("summary") or "").strip(),
        "recommended": str(doc.get("recommended") or "").strip(),
        "reviewed": compiled_reviewed,
        "findings": compiled_findings,
        "options": compiled_options,
        "strategy": compiled_strategy,
        "actions": compiled_actions,
    }

    raw_meth = doc.get("methodology")
    if isinstance(raw_meth, dict) and raw_meth:
        meth_out: dict = {}
        if "jtbd" in raw_meth and isinstance(raw_meth["jtbd"], dict):
            j = raw_meth["jtbd"]
            meth_out["jtbd"] = {
                "payer_job": str(j.get("payer_job") or "").strip(),
                "user_job": str(j.get("user_job") or "").strip(),
                "forces": {
                    k: str((j.get("forces") or {}).get(k) or "").strip()
                    for k in ("push", "pull", "anxiety", "habit")
                },
                "depends_on": _as_str_list(j.get("depends_on") if "depends_on" in j else j.get("deps")),
            }
        if "ost" in raw_meth and isinstance(raw_meth["ost"], dict):
            o = raw_meth["ost"]
            opps = []
            for opp in o.get("opportunities") or []:
                if not isinstance(opp, dict):
                    continue
                sols = []
                for sol in opp.get("solutions") or []:
                    if isinstance(sol, dict):
                        sols.append({
                            "option_id": str(sol.get("option_id") or sol.get("option") or "").strip(),
                            "assumption_test": str(sol.get("assumption_test") or sol.get("test") or "").strip(),
                        })
                opps.append({
                    "id": str(opp.get("id") or "").strip(),
                    "statement": str(opp.get("statement") or "").strip(),
                    "depends_on": _as_str_list(opp.get("depends_on") if "depends_on" in opp else opp.get("deps")),
                    "solutions": sols,
                })
            meth_out["ost"] = {"outcome": str(o.get("outcome") or "").strip(), "opportunities": opps}
        if "premortem" in raw_meth and isinstance(raw_meth["premortem"], list):
            pm_list = []
            for row in raw_meth["premortem"]:
                if not isinstance(row, dict):
                    continue
                pm_list.append({
                    "failure_mode": str(row.get("failure_mode") or "").strip(),
                    "probability": str(row.get("probability") or "medium").strip(),
                    "impact": str(row.get("impact") or "high").strip(),
                    "preventability": str(row.get("preventability") or "high").strip(),
                    "mitigation": str(row.get("mitigation") or "").strip(),
                    "depends_on": _as_str_list(row.get("depends_on") if "depends_on" in row else row.get("deps")),
                })
            meth_out["premortem"] = pm_list
        if "promise_matrix" in raw_meth and isinstance(raw_meth["promise_matrix"], list):
            pmat = []
            for row in raw_meth["promise_matrix"]:
                if not isinstance(row, dict):
                    continue
                proofs = row.get("proof_findings") if "proof_findings" in row else row.get("proofs")
                pmat.append({
                    "promise": str(row.get("promise") or "").strip(),
                    "capability": str(row.get("capability") or "").strip(),
                    "proof_findings": _as_str_list(proofs),
                    "boundary": str(row.get("boundary") or "").strip(),
                    "channel": str(row.get("channel") or "").strip(),
                })
            meth_out["promise_matrix"] = pmat
        if meth_out:
            proposal["methodology"] = meth_out

    return proposal


def generate_draft_template(case: Path, state: dict, sources_health: dict) -> str:
    """Generate a concise YAML draft template pre-linked to the case's registered sources."""
    active = [
        (sid, info)
        for sid, info in sorted(sources_health.items())
        if info.get("health") != "retired"
    ]
    first_src = active[0][0] if active else "source_id"
    src_comments = [
        f"#   - {sid} ({info['kind']}, {info['health']}): {info['path']}"
        for sid, info in active
    ] or ["#   (нет зарегистрированных источников — сначала выполните ledger source / fetch)"]

    review_notes_lines = [
        f"  {sid}: Проверено ({info['path']})"
        for sid, info in active
    ] or ["  # source_id: Краткая заметка о проверке"]

    req_one_line = " ".join(str(state.get("request") or "").split())
    return "\n".join([
        f"# Шаблон черновика решения Decision Ledger · кейс: {case.name}",
        f"# Исходный запрос: {req_one_line}",
        "# Активные источники кейса (SHA-256 подставляются автоматически при reviewed: auto):",
        *src_comments,
        "# Отправьте заполненный файл напрямую: python3 -m ledger propose <case> draft.yaml --strict-claims",
        "",
        "title: Краткий заголовок рекомендуемого решения",
        "summary: |",
        "  Управленческое резюме в 2–3 предложениях: что меняем в продукте и коммуникации,",
        "  какова расчётная маржа на допущениях и какое стоп-правило защищает пилот.",
        "recommended: opt_change",
        "",
        "reviewed: auto",
        "review_notes:",
        *review_notes_lines,
        "",
        "findings:",
        "  f1_market:",
        "    kind: fact",
        "    text: |",
        "      Проверяемый рыночный или продуктовый факт с точной цифрой из первичного источника.",
        "    cite:",
        f"      - {first_src}: раздел 1 / строка таблицы",
        "  f2_economics:",
        "    kind: inference",
        "    text: |",
        "      Расчёт юнит-экономики или когортного эффекта (явно отделяя факты витрины от допущений модели).",
        "    deps: [f1_market]",
        "  f3_gap:",
        "    kind: unknown",
        "    text: |",
        "      Что остаётся неизвестным до полевого пилота и требует проверки на реальном трафике.",
        "    deps: [f1_market]",
        "",
        "options:",
        "  opt_keep:",
        "    title: Оставить текущую модель без изменений",
        "    type: keep",
        "    case: Нулевые затраты на пересборку линейки и отсутствие риска каннибализации текущей базы.",
        "    objection: Не устраняет корневой барьер роста и уступает альтернативам по целевой экономике.",
        "    deps: [f1_market, f2_economics]",
        "  opt_change:",
        "    title: Целевое изменение продукта и упаковки (рекомендуется)",
        "    type: change",
        "    case: Решает основную задачу сегмента при соблюдении порога маржинального вклада комитета.",
        "    objection: Требует подтверждения конверсии и удержания в контролируемом A/B-пилоте.",
        "    deps: [f1_market, f2_economics, f3_gap]",
        "  opt_close:",
        "    title: Свернуть платное развитие направления",
        "    type: close",
        "    case: Освобождает ресурс команды, если целевой CAC и удержание недостижимы.",
        "    objection: Потеря сервисной выручки и уступка сегмента конкурентам.",
        "    deps: [f1_market, f2_economics]",
        "",
        "strategy:",
        "  product:",
        "    text: |",
        "      1) Что входит в продукт и тарифную сетку.",
        "      2) Экономика и пороги переключения решения (на допущениях модели).",
        "      3) Что сознательно исключено из контура первого релиза.",
        "    deps: [f1_market, f2_economics, f3_gap]",
        "  communication:",
        "    text: |",
        "      Честное ценностное предложение без абсолютных гарантий, при условии явного согласия клиента",
        "      и с прямым указанием технических ограничений и исключений сервиса в CVM-каналах.",
        "    deps: [f1_market, f3_gap]",
        "  experiment:",
        "    text: |",
        "      Контролируемый пилот на выделенной когорте с заранее заданным окном наблюдения",
        "      и жёстким стоп-правилом (guardrail) при превышении порога оттока или CAC.",
        "    deps: [f2_economics, f3_gap]",
        "",
        "methodology:",
        "  jtbd:",
        "    payer_job: Когда возникает риск/задача, я хочу надёжное решение с понятной ценой, чтобы не нести потери.",
        "    user_job: Когда я пользуюсь сервисом каждый день, я хочу минимум трения и ложных срабатываний.",
        "    forces:",
        "      push: Рост частоты проблемы или неудобство текущего сценария.",
        "      pull: Прозрачная выгода нового пакета в привычном интерфейсе оператора.",
        "      anxiety: Сомнение, оправдает ли подписка ежемесячный платёж и нет ли скрытых условий.",
        "      habit: Привычка обходиться бесплатными средствами или откладывать подключение.",
        "    deps: [f1_market, f3_gap]",
        "  ost:",
        "    outcome: Выход на целевую маржинальность и устойчивый рост платящей базы в горизонте 2027 года.",
        "    opportunities:",
        "      - id: opp_core",
        "        statement: Убрать главный барьер первой оплаты и повысить воспринимаемую ценность пакета.",
        "        deps: [f1_market, f2_economics]",
        "        solutions:",
        "          - option_id: opt_change",
        "            assumption_test: A/B-тест конверсии и 30-дневного удержания в собственных CVM-каналах.",
        "  premortem:",
        "    - failure_mode: Фактический CAC во внешних каналах превышает порог окупаемости.",
        "      probability: medium",
        "      impact: high",
        "      preventability: high",
        "      mitigation: Запускать масштабирование только через собственные CVM-каналы с верхним лимитом CAC.",
        "      deps: [f2_economics]",
        "    - failure_mode: Ранний отток после первого расчётного периода обрушивает LTV.",
        "      probability: medium",
        "      impact: high",
        "      preventability: medium",
        "      mitigation: Контроль удержания 60 дней как блокирующий критерий перед раскаткой на всю базу.",
        "      deps: [f2_economics, f3_gap]",
        "    - failure_mode: Претензии регулятора или клиентов к формулировкам рекламных обещаний.",
        "      probability: low",
        "      impact: high",
        "      preventability: high",
        "      mitigation: Обязательный прогон текстов через ledger audit-claims и явная оговорка границ сервиса.",
        "      deps: [f3_gap]",
        "  promise_matrix:",
        "    - promise: Конкретная измеримая польза для абонента без абсолютных обещаний.",
        "      capability: Реальный продуктовый механизм в контуре оператора.",
        "      proofs: [f1_market, f2_economics]",
        "      boundary: Честное ограничение, в каких сценариях защита/сервис не срабатывает автоматически.",
        "      channel: Мобильное приложение и CVM-коммуникации оператора.",
        "",
        "actions:",
        "  - id: act_pilot",
        "    owner: Product Manager",
        "    due: 2027-02-15",
        "    task: Запустить A/B-пилот рекомендуемого пакета и сверить фактический CAC и удержание с моделью.",
        "    trigger: Пересмотреть решение при выходе CAC или оттока за стоп-пороги экономики.",
        "",
    ])
