"""Per-step cost ledger (.cases/<case>/cost.jsonl) with cited public model pricing."""

from __future__ import annotations

import json
from pathlib import Path

from .store import now

COST_LOG_FILE = "cost.jsonl"

# Verified public API pricing per 1,000,000 tokens as of 2026-11-17.
USD_TO_RUB_RATE = 98.50
FX_SOURCE = "Банк России (официальные курсы валют, ориентир расчёта): https://cbr.ru/currency_base/daily/"
FX_DATE = "2026-11-17"
DEFAULT_PM_HOURLY_RUB = 3000.0  # 50 RUB / min for Total Cost of Decision evaluation

MODEL_PRICING: dict[str, dict] = {
    "zero_llm": {
        "input_per_1m": 0.0,
        "cached_input_per_1m": 0.0,
        "output_per_1m": 0.0,
        "source_url": "local://python3-stdlib-cli",
        "verified_at": "2026-11-17",
        "label": "Локальный детерминированный CLI (0 токенов LLM)",
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
        "source_url": "https://openai.com/api/pricing",
        "verified_at": "2026-11-17",
        "label": "Codex CLI (эквивалент публичного тарифа frontier-mid)",
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

# Map symbolic routing tiers to concrete reference pricing models when a symbolic name is logged.
TIER_ALIASES = {
    "zero_llm+fast_scout": "gemini-2.5-flash",
    "fast": "gemini-2.5-flash",
    "fast_multimodal": "gemini-2.5-flash",
    "reasoning_strategist": "gemini-2.5-pro",
    "deep_reasoning": "gemini-2.5-pro",
}


def resolve_pricing(model: str) -> dict:
    key = (model or "zero_llm").strip()
    if key in MODEL_PRICING:
        return MODEL_PRICING[key]
    alias = TIER_ALIASES.get(key)
    if alias and alias in MODEL_PRICING:
        return MODEL_PRICING[alias]
    lower = key.lower()
    if "flash" in lower or "mini" in lower:
        return MODEL_PRICING["gemini-2.5-flash"]
    if "claude" in lower:
        return MODEL_PRICING["claude-sonnet-4"]
    if "zero" in lower or "cli" in lower:
        return MODEL_PRICING["zero_llm"]
    return MODEL_PRICING["gpt-5"]


def compute_token_cost(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> tuple[float, float, dict]:
    spec = resolve_pricing(model)
    cached = min(max(cached_tokens, 0), max(input_tokens, 0))
    uncached = max(input_tokens - cached, 0)
    usd = (
        (uncached / 1_000_000.0) * spec["input_per_1m"]
        + (cached / 1_000_000.0) * spec["cached_input_per_1m"]
        + (max(output_tokens, 0) / 1_000_000.0) * spec["output_per_1m"]
    )
    rub = usd * USD_TO_RUB_RATE
    return round(usd, 6), round(rub, 2), spec


def _cost_file(case: Path) -> Path:
    path = case / COST_LOG_FILE
    if path.is_symlink():
        raise ValueError("cost.jsonl must be a regular file inside the case")
    return path


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
    human_minutes: float = 0.0,
    human_intervention: str = "",
    note: str = "",
) -> dict:
    if any(v < 0 for v in (input_tokens, cached_tokens, output_tokens, wall_seconds, tool_calls, retries, human_minutes)):
        raise ValueError("Cost counters must be non-negative")
    if not step or not step.strip():
        raise ValueError("Step name is required for cost.jsonl")

    usd, rub, spec = compute_token_cost(model, input_tokens, cached_tokens, output_tokens)
    human_rub = round((human_minutes / 60.0) * DEFAULT_PM_HOURLY_RUB, 2)
    entry = {
        "at": now(),
        "session": session.strip() or "session-1",
        "step": step.strip(),
        "model": model.strip() or "zero_llm",
        "input_tokens": int(input_tokens),
        "cached_tokens": int(min(cached_tokens, input_tokens)),
        "output_tokens": int(output_tokens),
        "cost_usd": usd,
        "cost_rub": rub,
        "wall_seconds": round(float(wall_seconds), 2),
        "tool_calls": int(tool_calls),
        "retries": int(retries),
        "human_minutes": round(float(human_minutes), 2),
        "human_cost_rub": human_rub,
        "total_step_rub": round(rub + human_rub, 2),
        "human_intervention": human_intervention.strip(),
        "pricing_source": spec["source_url"],
        "pricing_date": spec["verified_at"],
        "note": note.strip(),
    }
    path = _cost_file(case)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_cost_entries(case: Path, fallback_telemetry: list[dict] | None = None) -> list[dict]:
    path = _cost_file(case)
    if path.is_file():
        entries = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                entries.append(json.loads(raw))
        return entries

    # Graceful fallback for older cases that only stored in-SQLite telemetry
    synthesized = []
    for item in fallback_telemetry or []:
        inp = int(item.get("input_tokens", 0))
        cac = int(item.get("cached_tokens", 0))
        out = int(item.get("output_tokens", 0))
        mdl = item.get("model", "zero_llm")
        usd, rub, spec = compute_token_cost(mdl, inp, cac, out)
        synthesized.append({
            "at": item.get("at", ""),
            "session": "legacy",
            "step": item.get("step", ""),
            "model": mdl,
            "input_tokens": inp,
            "cached_tokens": cac,
            "output_tokens": out,
            "cost_usd": usd,
            "cost_rub": rub,
            "wall_seconds": float(item.get("seconds", 0.0)),
            "tool_calls": int(item.get("tool_calls", 0)),
            "retries": int(item.get("retries", 0)),
            "human_minutes": 0.0,
            "human_cost_rub": 0.0,
            "total_step_rub": rub,
            "human_intervention": item.get("human_intervention", ""),
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
    api_usd = round(sum(float(e.get("cost_usd", 0.0)) for e in entries), 6)
    api_rub = round(sum(float(e.get("cost_rub", 0.0)) for e in entries), 2)
    wall_s = round(sum(float(e.get("wall_seconds", 0.0)) for e in entries), 2)
    retries = sum(int(e.get("retries", 0)) for e in entries)
    tool_calls = sum(int(e.get("tool_calls", 0)) for e in entries)
    human_min = round(sum(float(e.get("human_minutes", 0.0)) for e in entries), 2)
    human_rub = round((human_min / 60.0) * DEFAULT_PM_HOURLY_RUB, 2)
    sources = sorted({
        f"{e.get('model')} -> {e.get('pricing_source')} ({e.get('pricing_date')})"
        for e in entries if e.get("pricing_source")
    })
    sessions = sorted({e.get("session", "session-1") for e in entries if e.get("session")})
    return {
        "log_file": str((case / COST_LOG_FILE).name),
        "has_jsonl_file": (case / COST_LOG_FILE).is_file(),
        "steps": len(entries),
        "sessions": sessions,
        "input_tokens": total_in,
        "cached_tokens": total_cached,
        "uncached_input_tokens": max(total_in - total_cached, 0),
        "cache_hit_ratio": round(total_cached / total_in, 4) if total_in else 0.0,
        "output_tokens": total_out,
        "api_cost_usd": api_usd,
        "api_cost_rub": api_rub,
        "wall_seconds": wall_s,
        "tool_calls": tool_calls,
        "retries": retries,
        "human_minutes": human_min,
        "human_cost_rub": human_rub,
        "total_decision_cost_rub": round(api_rub + human_rub, 2),
        "fx_rate_usd_rub": USD_TO_RUB_RATE,
        "fx_source": f"{FX_SOURCE} ({FX_DATE})",
        "pm_hourly_rate_rub": DEFAULT_PM_HOURLY_RUB,
        "pricing_sources": sources,
    }
