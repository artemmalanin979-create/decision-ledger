"""Per-step cost ledger (.cases/<case>/cost.jsonl) with runtime-log import and provenance checks."""

from __future__ import annotations

from datetime import datetime
import gzip
import json
from pathlib import Path

from .store import now

COST_LOG_FILE = "cost.jsonl"

# Verified public API pricing per 1,000,000 tokens as of 2026-11-17 (used for measured API runs or explicit estimates).
USD_TO_RUB_RATE = 98.50
FX_SOURCE = "Банк России (ориентир расчёта курса): https://cbr.ru/currency_base/daily/"
FX_DATE = "2026-11-17"
DEFAULT_PM_HOURLY_RUB = 3000.0  # Used ONLY when human_minutes is actually measured (not None)

MODEL_PRICING: dict[str, dict] = {
    "zero_llm": {
        "input_per_1m": 0.0,
        "cached_input_per_1m": 0.0,
        "output_per_1m": 0.0,
        "source_url": "local://python3-stdlib-cli",
        "verified_at": "2026-11-17",
        "label": "Локальный детерминированный CLI (0 токенов LLM)",
    },
    "antigravity-subscription": {
        "input_per_1m": None,
        "cached_input_per_1m": None,
        "output_per_1m": None,
        "source_url": "subscription://antigravity (квота подписки IDE; модель в журнале не указана; денежная оценка не производится)",
        "verified_at": "2026-11-17",
        "label": "Модель хоста Antigravity (не раскрыта в журнале; квота подписки, ₽ не рассчитываются)",
        "billing": "subscription_quota",
    },
    "grok-cli-subscription": {
        "input_per_1m": None,
        "cached_input_per_1m": None,
        "output_per_1m": None,
        "source_url": "subscription://grok-build-cli (квота подписки; поле total_cost_usd — расчётная оценка самого CLI, ₽ не списывались)",
        "verified_at": "2026-11-18",
        "label": "Grok Build CLI (квота подписки; оценка USD из лога CLI, ₽ не рассчитываются)",
        "billing": "cli_runtime_estimate",
    },
    "codex-cli-subscription": {
        "input_per_1m": None,
        "cached_input_per_1m": None,
        "output_per_1m": None,
        "source_url": "subscription://codex-cli (квота подписки IDE; ₽ не рассчитываются)",
        "verified_at": "2026-11-17",
        "label": "Codex CLI (квота подписки IDE; ₽ не рассчитываются)",
        "billing": "subscription_quota",
    },
    "gpt-5": {
        "input_per_1m": 1.25,
        "cached_input_per_1m": 0.125,
        "output_per_1m": 10.00,
        "source_url": "https://openai.com/api/pricing",
        "verified_at": "2026-11-17",
        "label": "OpenAI GPT-5 Standard",
    },
    "gpt-5-mini": {
        "input_per_1m": 0.25,
        "cached_input_per_1m": 0.025,
        "output_per_1m": 1.00,
        "source_url": "https://openai.com/api/pricing",
        "verified_at": "2026-11-17",
        "label": "OpenAI GPT-5 mini",
    },
    "gpt-4.1": {
        "input_per_1m": 2.00,
        "cached_input_per_1m": 0.50,
        "output_per_1m": 8.00,
        "source_url": "https://openai.com/api/pricing",
        "verified_at": "2026-11-17",
        "label": "OpenAI GPT-4.1",
    },
    "gpt-6.1-sol": {
        "input_per_1m": 1.25,
        "cached_input_per_1m": 0.125,
        "output_per_1m": 10.00,
        "source_url": "https://openai.com/api/pricing (эквивалент frontier-mid; фактическое списание в подписке Codex = 0 ₽)",
        "verified_at": "2026-11-17",
        "label": "Codex CLI (квота подписки IDE, денежный эквивалент оценочный)",
        "default_estimated": True,
    },
    "gemini-2.5-pro": {
        "input_per_1m": 1.25,
        "cached_input_per_1m": 0.125,
        "output_per_1m": 10.00,
        "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
        "verified_at": "2026-11-17",
        "label": "Google Gemini 2.5 Pro (<=200K context)",
    },
    "gemini-2.5-flash": {
        "input_per_1m": 0.30,
        "cached_input_per_1m": 0.03,
        "output_per_1m": 2.50,
        "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
        "verified_at": "2026-11-17",
        "label": "Google Gemini 2.5 Flash",
    },
    "claude-sonnet-4": {
        "input_per_1m": 3.00,
        "cached_input_per_1m": 0.30,
        "output_per_1m": 15.00,
        "source_url": "https://www.anthropic.com/pricing",
        "verified_at": "2026-11-17",
        "label": "Anthropic Claude Sonnet 4",
    },
}

TIER_ALIASES = {
    "zero_llm+fast_scout": "gemini-2.5-flash",
    "fast": "gemini-2.5-flash",
    "fast_multimodal": "gemini-2.5-flash",
    "reasoning_strategist": "gemini-2.5-pro",
    "deep_reasoning": "gemini-2.5-pro",
}


def resolve_pricing(model: str) -> tuple[dict, bool]:
    """Return (pricing_spec, is_alias_or_subscription_estimate)."""
    key = (model or "zero_llm").strip()
    if key in MODEL_PRICING:
        spec = MODEL_PRICING[key]
        return spec, bool(spec.get("default_estimated", False))
    alias = TIER_ALIASES.get(key)
    if alias and alias in MODEL_PRICING:
        return MODEL_PRICING[alias], True
    lower = key.lower()
    if "antigravity" in lower:
        return MODEL_PRICING["antigravity-subscription"], False
    if "grok" in lower:
        return MODEL_PRICING["grok-cli-subscription"], True
    if "flash" in lower or "mini" in lower:
        return MODEL_PRICING["gemini-2.5-flash"], True
    if "claude" in lower:
        return MODEL_PRICING["claude-sonnet-4"], True
    if "zero" in lower or "cli" in lower:
        return MODEL_PRICING["zero_llm"], False
    return MODEL_PRICING["gpt-5"], True


def compute_token_cost(
    model: str, input_tokens: int, cached_tokens: int, output_tokens: int
) -> tuple[float | None, float | None, dict, bool]:
    """Return (usd, rub, spec, auto_estimated); usd/rub are None for subscription quotas without a price."""
    spec, auto_estimated = resolve_pricing(model)
    if spec.get("input_per_1m") is None:
        return None, None, spec, auto_estimated
    cached = min(max(cached_tokens, 0), max(input_tokens, 0))
    uncached = max(input_tokens - cached, 0)
    usd = (
        (uncached / 1_000_000.0) * spec["input_per_1m"]
        + (cached / 1_000_000.0) * spec["cached_input_per_1m"]
        + (max(output_tokens, 0) / 1_000_000.0) * spec["output_per_1m"]
    )
    rub = usd * USD_TO_RUB_RATE
    return round(usd, 6), round(rub, 2), spec, auto_estimated


def _cost_file(case: Path) -> Path:
    path = case / COST_LOG_FILE
    if path.is_symlink():
        raise ValueError("cost.jsonl must be a regular file inside the case")
    return path


def _entry_is_verified(entry: dict) -> bool:
    if "verified" in entry:
        return bool(entry["verified"])
    origin = str(entry.get("telemetry_origin") or "").strip()
    if origin in {"cli_runtime_log", "local_deterministic_cli"}:
        return True
    if origin == "self_reported_unverified":
        return False
    method = str(entry.get("method") or "").lower()
    if "из журнала хоста" in method or "из сырых логов" in method or "transcript_full" in method:
        return True
    if entry.get("model") == "zero_llm" and int(entry.get("input_tokens", 0)) == 0 and int(entry.get("output_tokens", 0)) == 0:
        return True
    return False


def _build_cost_entry(
    *,
    step: str,
    session: str = "session-1",
    model: str = "zero_llm",
    input_tokens: int = 0,
    cached_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    model_calls: int | None = None,
    wall_seconds: float = 0.0,
    tool_calls: int = 1,
    retries: int = 0,
    human_minutes: float | None = None,
    human_intervention: str = "",
    estimated: bool = False,
    method: str = "",
    note: str = "",
    verified: bool = False,
    telemetry_origin: str = "",
    at_override: str | None = None,
    use_cost_overrides: bool = False,
    cost_usd_override: float | None = None,
    cost_rub_override: float | None = None,
    billing_override: str | None = None,
    pricing_source_override: str | None = None,
    pricing_date_override: str | None = None,
) -> dict:
    if any(v < 0 for v in (input_tokens, cached_tokens, output_tokens, reasoning_tokens, wall_seconds, tool_calls, retries)):
        raise ValueError("Cost counters must be non-negative")
    if human_minutes is not None and human_minutes < 0:
        raise ValueError("human_minutes must be non-negative when provided")
    if not step or not step.strip():
        raise ValueError("Step name is required for cost.jsonl")

    mdl = model.strip() or "zero_llm"
    is_local_zero = mdl == "zero_llm" and int(input_tokens) == 0 and int(output_tokens) == 0
    is_verified = True if is_local_zero else bool(verified)
    if not telemetry_origin:
        if is_local_zero:
            telemetry_origin = "local_deterministic_cli"
        elif is_verified:
            telemetry_origin = "cli_runtime_log"
        else:
            telemetry_origin = "self_reported_unverified"

    usd, rub, spec, auto_estimated = compute_token_cost(mdl, input_tokens, cached_tokens, output_tokens)
    if use_cost_overrides:
        usd = round(float(cost_usd_override), 6) if cost_usd_override is not None else None
        rub = round(float(cost_rub_override), 2) if cost_rub_override is not None else None

    is_est = bool(estimated or auto_estimated or (telemetry_origin == "self_reported_unverified"))
    if method.strip():
        method_str = method.strip()
    elif usd is None:
        method_str = f"Токены и время — из журнала хоста; {spec['label']}"
    elif is_est:
        method_str = f"Оценка эквивалента токенов по публичному прайсу ({spec['label']}, {spec['source_url']})"
    else:
        method_str = "Прямой замер локального CLI (0 токенов)" if is_local_zero else "Прямой замер счётчиков рантайма"

    if telemetry_origin == "self_reported_unverified" and "самоотчёт" not in method_str.lower():
        method_str = f"[НЕ ВЕРИФИЦИРОВАНО · самоотчёт модели] {method_str}"

    human_rub = round((human_minutes / 60.0) * DEFAULT_PM_HOURLY_RUB, 2) if human_minutes is not None else None
    total_rub = round(rub + human_rub, 2) if (human_rub is not None and rub is not None) else None

    eff_cached = int(min(cached_tokens, input_tokens))
    eff_in = int(input_tokens)
    entry = {
        "at": at_override or now(),
        "session": session.strip() or "session-1",
        "step": step.strip(),
        "model": mdl,
        "verified": is_verified,
        "telemetry_origin": telemetry_origin,
        "estimated": is_est,
        "method": method_str,
        "input_tokens": eff_in,
        "uncached_input_tokens": max(eff_in - eff_cached, 0),
        "cached_tokens": eff_cached,
        "output_tokens": int(output_tokens),
        "reasoning_tokens": int(reasoning_tokens),
        "model_calls": int(model_calls) if model_calls is not None else None,
        "cost_usd": usd,
        "cost_rub": rub,
        "wall_seconds": round(float(wall_seconds), 2),
        "tool_calls": int(tool_calls),
        "retries": int(retries),
        "human_minutes": round(float(human_minutes), 2) if human_minutes is not None else None,
        "human_cost_rub": human_rub,
        "total_step_rub": total_rub,
        "human_intervention": human_intervention.strip(),
        "billing": billing_override or spec.get("billing", "api_price_list"),
        "pricing_source": pricing_source_override or spec["source_url"],
        "pricing_date": pricing_date_override or spec["verified_at"],
        "note": note.strip(),
    }
    return entry


def append_cost_entry(
    case: Path,
    *,
    step: str,
    session: str = "session-1",
    model: str = "zero_llm",
    input_tokens: int = 0,
    cached_tokens: int = 0,
    output_tokens: int = 0,
    wall_seconds: float = 0.0,
    tool_calls: int = 1,
    retries: int = 0,
    human_minutes: float | None = None,
    human_intervention: str = "",
    estimated: bool = False,
    method: str = "",
    note: str = "",
    verified: bool = False,
    telemetry_origin: str = "",
) -> dict:
    entry = _build_cost_entry(
        step=step,
        session=session,
        model=model,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        output_tokens=output_tokens,
        wall_seconds=wall_seconds,
        tool_calls=tool_calls,
        retries=retries,
        human_minutes=human_minutes,
        human_intervention=human_intervention,
        estimated=estimated,
        method=method,
        note=note,
        verified=verified,
        telemetry_origin=telemetry_origin,
    )
    path = _cost_file(case)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _open_maybe_gzip(path: Path):
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _strip_stream_stem(path: Path) -> str:
    name = path.name
    for suffix in (".stream.jsonl.gz", ".jsonl.gz", ".stream.jsonl", ".jsonl", ".json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _parse_iso_seconds(start_iso: str, end_iso: str) -> float:
    try:
        t0 = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return max(round((t1 - t0).total_seconds(), 2), 0.0)
    except ValueError:
        return 0.0


def _load_turns_index(turns_path: Path | None) -> tuple[list[dict], dict[tuple[str, str], dict], dict[str, list[dict]]]:
    if not turns_path or not turns_path.is_file():
        return [], {}, {}
    ordered: list[dict] = []
    by_label_session: dict[tuple[str, str], dict] = {}
    by_label: dict[str, list[dict]] = {}
    with _open_maybe_gzip(turns_path) as fh:
        for idx, line in enumerate(fh):
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            row = json.loads(line)
            row["_order"] = idx
            label = str(row.get("label") or "").strip()
            sess = str(row.get("session") or "").strip()
            ordered.append(row)
            by_label.setdefault(label, []).append(row)
            if label and sess and int(row.get("exit", 0)) == 0:
                by_label_session[(label, sess)] = row
    return ordered, by_label_session, by_label


def _parse_measured_telemetry_json(path: Path, *, include_baseline: bool = False) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    runs = doc.get("runs")
    if not isinstance(runs, list):
        raise ValueError(f"Unsupported JSON telemetry summary: {path}")
    entries: list[dict] = []
    for run in runs:
        run_id = str(run.get("run_id") or "")
        if not include_baseline and run_id.startswith("baseline"):
            continue
        total_in = int(run.get("total_input_tokens_measured", 0))
        cached = int(run.get("cache_read_tokens_measured", 0))
        out_tok = int(run.get("output_tokens_measured", 0))
        wall_s = float(run.get("wall_seconds_measured", 0.0))
        t_calls = int(run.get("tool_calls_measured", 0))
        m_turns = run.get("planner_turns_measured")
        err_steps = int(run.get("error_steps_measured", 0))
        human_min = run.get("human_minutes_measured")
        conv_id = str(run.get("host_transcript_conversation_id") or run_id or "session-1")
        started = str(run.get("started_at_utc") or "")
        ended = str(run.get("ended_at_utc") or now())
        purpose = str(run.get("purpose") or run_id)
        entries.append(
            _build_cost_entry(
                step=run_id or "step",
                session=conv_id,
                model="antigravity-subscription",
                input_tokens=total_in,
                cached_tokens=cached,
                output_tokens=out_tok,
                model_calls=int(m_turns) if m_turns is not None else None,
                wall_seconds=wall_s,
                tool_calls=t_calls,
                retries=err_steps,
                human_minutes=float(human_min) if human_min is not None else None,
                human_intervention=(
                    ""
                    if human_min is not None
                    else "Не измерялось: участие человека секундомером не фиксировалось"
                ),
                estimated=False,
                method=(
                    f"Верифицированный импорт из журнала рантайма ({path.name}): "
                    "время по created_at, токены из PLANNER_RESPONSE, квота подписки IDE (₽ не рассчитываются)"
                ),
                note=f"{run_id} ({started} – {ended}): {purpose}".strip(),
                verified=True,
                telemetry_origin="cli_runtime_log",
                at_override=ended,
                use_cost_overrides=True,
                cost_usd_override=None,
                cost_rub_override=None,
                billing_override="subscription_quota",
                pricing_source_override=MODEL_PRICING["antigravity-subscription"]["source_url"],
                pricing_date_override=MODEL_PRICING["antigravity-subscription"]["verified_at"],
            )
        )
    return entries


def _parse_jsonl_runtime_log(
    path: Path,
    *,
    by_label_session: dict[tuple[str, str], dict],
    by_label: dict[str, list[dict]],
) -> tuple[int, dict]:
    """Parse Grok Build CLI / Claude Code stream.jsonl(.gz), Antigravity transcript.jsonl, or Codex CLI jsonl."""
    label = _strip_stream_stem(path)
    end_event: dict | None = None
    stream_tool_calls = 0

    ag_planner_turns = 0
    ag_in = 0
    ag_cache = 0
    ag_out = 0
    ag_tools = 0
    ag_errors = 0
    ag_timestamps: list[str] = []

    codex_turns = 0
    codex_in = 0
    codex_cache = 0
    codex_out = 0
    codex_tools = 0
    codex_thread_id = ""

    with _open_maybe_gzip(path) as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            obj = json.loads(line)
            ev_type = obj.get("type")

            # Format 1: Grok Build CLI / Claude Code stream JSONL
            if ev_type in {"tool_call", "tool_use", "function_call"}:
                stream_tool_calls += 1
            if ev_type in {"end", "result"} and ("usage" in obj or "modelUsage" in obj):
                end_event = obj

            # Format 2: Antigravity transcript JSONL
            if "step_index" in obj and "source" in obj:
                if obj.get("created_at"):
                    ag_timestamps.append(str(obj["created_at"]))
                if obj.get("status") == "ERROR" or ev_type == "ERROR_MESSAGE":
                    ag_errors += 1
                if ev_type == "PLANNER_RESPONSE":
                    ag_planner_turns += 1
                    ag_in += int(obj.get("input_tokens", 0))
                    ag_cache += int(obj.get("cache_read_tokens", 0))
                    ag_out += int(obj.get("output_tokens", 0))
                    tcs = obj.get("tool_calls") or []
                    if isinstance(tcs, list):
                        ag_tools += len(tcs)

            # Format 3: Codex CLI JSONL
            if ev_type == "thread.started" and obj.get("thread_id"):
                codex_thread_id = str(obj["thread_id"])
            elif ev_type == "item.completed":
                item = obj.get("item") or {}
                if item.get("type") in {"command_execution", "mcp_tool_call", "file_change"}:
                    codex_tools += 1
            elif ev_type == "turn.completed" and isinstance(obj.get("usage"), dict):
                codex_turns += 1
                u = obj["usage"]
                codex_in += int(u.get("input_tokens", 0))
                codex_cache += int(u.get("cached_input_tokens", 0))
                codex_out += int(u.get("output_tokens", 0))

    if end_event is not None:
        sess_id = str(end_event.get("sessionId") or end_event.get("session_id") or label)
        usage = end_event.get("usage") or {}
        uncached_in = int(usage.get("input_tokens", 0))
        cache_read = int(usage.get("cache_read_input_tokens", 0))
        cache_create = int(usage.get("cache_creation_input_tokens", 0))
        total_in = uncached_in + cache_read + cache_create
        out_tok = int(usage.get("output_tokens", 0))
        reasoning_tok = int(
            usage.get("reasoning_tokens")
            or (usage.get("output_tokens_details") or {}).get("thinking_tokens")
            or 0
        )

        model_usage = end_event.get("modelUsage") or {}
        model_names = sorted(model_usage.keys())
        raw_model = model_names[0] if model_names else str(end_event.get("model") or "cli-runtime")
        model_calls = sum(int(v.get("modelCalls", 0)) for v in model_usage.values() if isinstance(v, dict))
        if not model_calls:
            model_calls = int(end_event.get("num_turns") or 1)

        turn_meta = by_label_session.get((label, sess_id))
        if turn_meta is None:
            ok_rows = [r for r in by_label.get(label, []) if int(r.get("exit", 0)) == 0]
            if ok_rows:
                turn_meta = ok_rows[-1]

        aborted_rows = [r for r in by_label.get(label, []) if int(r.get("exit", 0)) != 0]
        retries = len(aborted_rows)

        order_key = int(turn_meta["_order"]) if turn_meta else 10_000
        if turn_meta and turn_meta.get("wall_s") is not None:
            wall_s = float(turn_meta["wall_s"])
        elif end_event.get("duration_ms") is not None:
            wall_s = round(float(end_event["duration_ms"]) / 1000.0, 2)
        else:
            wall_s = 0.0

        at_ts = str(turn_meta.get("end")) if (turn_meta and turn_meta.get("end")) else now()
        model_label = str(turn_meta.get("model") or raw_model) if turn_meta else raw_model
        cli_usd = end_event.get("total_cost_usd")
        has_cli_usd = cli_usd is not None

        note_parts = [f"лог={path.name}", f"model={raw_model}", f"вызовов модели={model_calls}"]
        if reasoning_tok:
            note_parts.append(f"токены рассуждений={reasoning_tok}")
        if aborted_rows:
            aborted_s = sum(float(r.get("wall_s", 0)) for r in aborted_rows)
            note_parts.append(f"прерванных попыток до успеха={retries} ({aborted_s:g} с)")

        entry = _build_cost_entry(
            step=label,
            session=sess_id,
            model=model_label,
            input_tokens=total_in,
            cached_tokens=cache_read,
            output_tokens=out_tok,
            reasoning_tokens=reasoning_tok,
            model_calls=model_calls,
            wall_seconds=wall_s,
            tool_calls=stream_tool_calls or model_calls,
            retries=retries,
            human_minutes=None,
            human_intervention="Не измерялось: участие человека секундомером не фиксировалось",
            estimated=has_cli_usd,
            method=(
                "Верифицированный импорт из сырых логов CLI"
                + (" + turns.jsonl" if turn_meta else "")
                + ": токены и время — прямой замер рантайма; "
                + (
                    "cost_usd — расчётная оценка поля total_cost_usd самого CLI (квота подписки, списания в ₽ не было)"
                    if has_cli_usd
                    else "работа в квоте подписки, денежная оценка не рассчитывается"
                )
            ),
            note="; ".join(note_parts),
            verified=True,
            telemetry_origin="cli_runtime_log",
            at_override=at_ts,
            use_cost_overrides=True,
            cost_usd_override=float(cli_usd) if has_cli_usd else None,
            cost_rub_override=None,
            billing_override="cli_runtime_estimate" if has_cli_usd else "subscription_quota",
            pricing_source_override=f"cli-log://{path.name}#end.total_cost_usd" if has_cli_usd else "cli-log://subscription",
            pricing_date_override=at_ts[:10] if len(at_ts) >= 10 else FX_DATE,
        )
        return order_key, entry

    if ag_planner_turns > 0:
        wall_s = _parse_iso_seconds(ag_timestamps[0], ag_timestamps[-1]) if len(ag_timestamps) >= 2 else 0.0
        at_ts = ag_timestamps[-1] if ag_timestamps else now()
        entry = _build_cost_entry(
            step=label,
            session=label,
            model="antigravity-subscription",
            input_tokens=ag_in + ag_cache,
            cached_tokens=ag_cache,
            output_tokens=ag_out,
            model_calls=ag_planner_turns,
            wall_seconds=wall_s,
            tool_calls=ag_tools,
            retries=ag_errors,
            human_minutes=None,
            human_intervention="Не измерялось: участие человека секундомером не фиксировалось",
            estimated=False,
            method=(
                f"Верифицированный импорт из {path.name}: сумма input_tokens + cache_read_tokens и output_tokens "
                "по PLANNER_RESPONSE, время по created_at; квота подписки IDE (₽ не рассчитываются)"
            ),
            note=f"лог={path.name}; шагов планировщика={ag_planner_turns}",
            verified=True,
            telemetry_origin="cli_runtime_log",
            at_override=at_ts,
            use_cost_overrides=True,
            cost_usd_override=None,
            cost_rub_override=None,
            billing_override="subscription_quota",
            pricing_source_override=MODEL_PRICING["antigravity-subscription"]["source_url"],
            pricing_date_override=at_ts[:10] if len(at_ts) >= 10 else FX_DATE,
        )
        return 10_000, entry

    if codex_turns > 0:
        # In Codex CLI usage, input_tokens already includes cached_input_tokens when input_tokens >= cached_input_tokens.
        total_in = codex_in if codex_in >= codex_cache else (codex_in + codex_cache)
        entry = _build_cost_entry(
            step=label,
            session=codex_thread_id or label,
            model="codex-cli-subscription",
            input_tokens=total_in,
            cached_tokens=codex_cache,
            output_tokens=codex_out,
            model_calls=codex_turns,
            wall_seconds=0.0,
            tool_calls=codex_tools or codex_turns,
            retries=0,
            human_minutes=None,
            human_intervention="Не измерялось: участие человека секундомером не фиксировалось",
            estimated=False,
            method=f"Верифицированный импорт из лога Codex CLI ({path.name}); квота подписки IDE (₽ не рассчитываются)",
            note=f"лог={path.name}; ходов={codex_turns}",
            verified=True,
            telemetry_origin="cli_runtime_log",
            use_cost_overrides=True,
            cost_usd_override=None,
            cost_rub_override=None,
            billing_override="subscription_quota",
            pricing_source_override=MODEL_PRICING["codex-cli-subscription"]["source_url"],
            pricing_date_override=FX_DATE,
        )
        return 10_000, entry

    raise ValueError(f"Log file does not contain recognizable CLI telemetry events: {path}")


def import_cost_from_logs(
    case: Path,
    log_paths: list[Path],
    *,
    turns_path: Path | None = None,
    append: bool = False,
    include_baseline: bool = False,
) -> dict:
    """Parse raw CLI runtime logs and rebuild (or append to) .cases/<case>/cost.jsonl."""
    if not log_paths:
        raise ValueError("Provide at least one log file or run directory to --from-log")

    resolved_turns = turns_path.resolve() if turns_path else None
    candidate_files: list[Path] = []
    for raw_p in log_paths:
        p = raw_p.resolve()
        if not p.exists():
            raise ValueError(f"Log path does not exist: {raw_p}")
        if p.is_dir():
            if resolved_turns is None:
                if (p / "turns.jsonl").is_file():
                    resolved_turns = p / "turns.jsonl"
                elif (p.parent / "turns.jsonl").is_file():
                    resolved_turns = p.parent / "turns.jsonl"
            logs_subdir = p / "logs"
            if logs_subdir.is_dir():
                stream_files = sorted(
                    f for f in logs_subdir.iterdir()
                    if f.is_file() and (f.name.endswith(".jsonl") or f.name.endswith(".jsonl.gz"))
                )
                candidate_files.extend(stream_files)
            elif (p / "measured-telemetry.json").is_file():
                candidate_files.append(p / "measured-telemetry.json")
            else:
                found = sorted(
                    f for f in p.iterdir()
                    if f.is_file()
                    and (f.name.endswith(".jsonl") or f.name.endswith(".jsonl.gz") or f.name == "measured-telemetry.json")
                    and not f.name.startswith("cost")
                    and f.name != "turns.jsonl"
                )
                candidate_files.extend(found)
        else:
            if resolved_turns is None:
                if (p.parent / "turns.jsonl").is_file():
                    resolved_turns = p.parent / "turns.jsonl"
                elif (p.parent.parent / "turns.jsonl").is_file():
                    resolved_turns = p.parent.parent / "turns.jsonl"
            candidate_files.append(p)

    if not candidate_files:
        raise ValueError("No runtime log files found in the specified --from-log path(s)")

    _, by_label_session, by_label = _load_turns_index(resolved_turns)
    ordered_entries: list[tuple[int, str, dict]] = []
    for idx, fpath in enumerate(candidate_files):
        if fpath.name.endswith(".json") and not fpath.name.endswith(".jsonl"):
            parsed_list = _parse_measured_telemetry_json(fpath, include_baseline=include_baseline)
            for sub_idx, item in enumerate(parsed_list):
                ordered_entries.append((idx * 100 + sub_idx, fpath.name, item))
        else:
            ord_key, item = _parse_jsonl_runtime_log(
                fpath,
                by_label_session=by_label_session,
                by_label=by_label,
            )
            ordered_entries.append((ord_key if ord_key < 10_000 else 10_000 + idx, fpath.name, item))

    ordered_entries.sort(key=lambda t: (t[0], t[1]))
    new_entries = [item for _, _, item in ordered_entries]
    if not new_entries:
        raise ValueError("No telemetry steps extracted from logs")

    dest = _cost_file(case)
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = read_cost_entries(case) if dest.is_file() else []
    prev_unverified = sum(1 for e in existing if not _entry_is_verified(e))

    final_entries = (existing + new_entries) if append else new_entries
    tmp = dest.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in final_entries),
        encoding="utf-8",
    )
    tmp.replace(dest)

    return {
        "rebuilt": not append,
        "imported_steps": len(new_entries),
        "replaced_previous_entries": 0 if append else len(existing),
        "removed_unverified_self_reports": 0 if append else prev_unverified,
        "turns_index_used": str(resolved_turns) if resolved_turns else None,
        "cost": summarize_case_cost(case),
    }


def read_cost_entries(case: Path, fallback_telemetry: list[dict] | None = None) -> list[dict]:
    path = _cost_file(case)
    if path.is_file():
        entries = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                item = json.loads(raw)
                item.setdefault("verified", _entry_is_verified(item))
                if "telemetry_origin" not in item:
                    item["telemetry_origin"] = (
                        "cli_runtime_log" if item["verified"] else "self_reported_unverified"
                    )
                entries.append(item)
        return entries

    synthesized = []
    for item in fallback_telemetry or []:
        inp = int(item.get("input_tokens", 0))
        cac = int(item.get("cached_tokens", 0))
        out = int(item.get("output_tokens", 0))
        mdl = item.get("model", "zero_llm")
        is_zero = mdl == "zero_llm" and inp == 0 and out == 0
        usd, rub, spec, auto_est = compute_token_cost(mdl, inp, cac, out)
        synthesized.append({
            "at": item.get("at", ""),
            "session": "legacy",
            "step": item.get("step", ""),
            "model": mdl,
            "verified": is_zero,
            "telemetry_origin": "local_deterministic_cli" if is_zero else "self_reported_unverified",
            "estimated": True if (inp or out) else auto_est,
            "method": (
                "Прямой замер локального CLI (0 токенов)"
                if is_zero
                else "[НЕ ВЕРИФИЦИРОВАНО · самоотчёт модели] Ретроспективная сводка из legacy telemetry SQLite"
            ),
            "input_tokens": inp,
            "uncached_input_tokens": max(inp - min(cac, inp), 0),
            "cached_tokens": min(cac, inp),
            "output_tokens": out,
            "reasoning_tokens": 0,
            "model_calls": None,
            "cost_usd": usd,
            "cost_rub": rub,
            "wall_seconds": float(item.get("seconds", 0.0)),
            "tool_calls": int(item.get("tool_calls", 0)),
            "retries": int(item.get("retries", 0)),
            "human_minutes": None,
            "human_cost_rub": None,
            "total_step_rub": None,
            "human_intervention": item.get("human_intervention", ""),
            "billing": spec.get("billing", "api_price_list"),
            "pricing_source": spec["source_url"],
            "pricing_date": spec["verified_at"],
            "note": item.get("note", ""),
        })
    return synthesized


def summarize_case_cost(case: Path, fallback_telemetry: list[dict] | None = None) -> dict:
    entries = read_cost_entries(case, fallback_telemetry)
    total_in = sum(int(e.get("input_tokens", 0)) for e in entries)
    total_cached = sum(int(e.get("cached_tokens", 0)) for e in entries)
    total_out = sum(int(e.get("output_tokens", 0)) for e in entries)
    total_reasoning = sum(int(e.get("reasoning_tokens") or 0) for e in entries)
    model_call_vals = [int(e["model_calls"]) for e in entries if e.get("model_calls") is not None]
    total_model_calls = sum(model_call_vals) if model_call_vals else None

    usd_entries = [e for e in entries if e.get("cost_usd") is not None]
    rub_entries = [e for e in entries if e.get("cost_rub") is not None]
    usd_complete = bool(entries) and len(usd_entries) == len(entries)
    rub_complete = bool(entries) and len(rub_entries) == len(entries)

    api_usd = round(sum(float(e["cost_usd"]) for e in usd_entries), 6) if usd_complete else None
    api_rub = round(sum(float(e["cost_rub"]) for e in rub_entries), 2) if rub_complete else None
    wall_s = round(sum(float(e.get("wall_seconds", 0.0)) for e in entries), 2)
    retries = sum(int(e.get("retries", 0)) for e in entries)
    tool_calls = sum(int(e.get("tool_calls", 0)) for e in entries)

    verified_steps = sum(1 for e in entries if _entry_is_verified(e))
    unverified_steps = len(entries) - verified_steps
    telemetry_verified = bool(entries) and unverified_steps == 0

    has_estimated = any(bool(e.get("estimated", False)) for e in entries)
    methods = []
    for e in entries:
        m = (e.get("method") or "").strip()
        if m and m not in methods:
            methods.append(m)

    measured_human = [float(e["human_minutes"]) for e in entries if e.get("human_minutes") is not None]
    human_measured = bool(entries) and len(measured_human) == len(entries)
    human_min = round(sum(measured_human), 2) if measured_human else None
    human_rub = round((human_min / 60.0) * DEFAULT_PM_HOURLY_RUB, 2) if human_min is not None else None
    total_dec_rub = round(api_rub + human_rub, 2) if (human_rub is not None and api_rub is not None) else None

    sources = sorted({
        f"{e.get('model')} -> {e.get('pricing_source')} ({e.get('pricing_date')})"
        for e in entries if e.get("pricing_source")
    })
    sessions = sorted({e.get("session", "session-1") for e in entries if e.get("session")})
    origins = sorted({str(e.get("telemetry_origin") or "") for e in entries if e.get("telemetry_origin")})
    return {
        "log_file": str((case / COST_LOG_FILE).name),
        "has_jsonl_file": (case / COST_LOG_FILE).is_file(),
        "steps": len(entries),
        "verified_steps": verified_steps,
        "unverified_self_reported_steps": unverified_steps,
        "telemetry_verified": telemetry_verified,
        "telemetry_origins": origins,
        "sessions": sessions,
        "estimated": has_estimated,
        "methods": methods,
        "input_tokens": total_in,
        "cached_tokens": total_cached,
        "uncached_input_tokens": max(total_in - total_cached, 0),
        "cache_hit_ratio": round(total_cached / total_in, 4) if total_in else 0.0,
        "output_tokens": total_out,
        "reasoning_tokens": total_reasoning,
        "model_calls": total_model_calls,
        "api_cost_computed": rub_complete,
        "api_cost_usd_available": usd_complete,
        "billing": sorted({e.get("billing", "api_price_list") for e in entries}),
        "api_cost_usd": api_usd,
        "api_cost_rub": api_rub,
        "wall_seconds": wall_s,
        "tool_calls": tool_calls,
        "retries": retries,
        "human_minutes_measured": human_measured,
        "human_minutes": human_min,
        "human_cost_rub": human_rub,
        "total_decision_cost_rub": total_dec_rub,
        "fx_rate_usd_rub": USD_TO_RUB_RATE,
        "fx_source": f"{FX_SOURCE} ({FX_DATE})",
        "pm_hourly_rate_rub": DEFAULT_PM_HOURLY_RUB if human_min is not None else None,
        "pricing_sources": sources,
    }
