"""Multi-feature task router, cost-tier selector, error recovery, and run telemetry."""

from __future__ import annotations

from pathlib import Path

from .review import impact
from .store import now

TABLE_EXTS = {".csv", ".tsv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
AUDIO_VIDEO_EXTS = {".mp3", ".wav", ".aac", ".ogg", ".m4a", ".mp4", ".webm"}
DOC_EXTS = {".md", ".txt", ".json", ".pdf", ".html"}


def inspect_workspace_inputs(case: Path, registered_paths: set[str]) -> dict:
    """Scan case directory for unregistered files and classify modalities and sizes."""
    unregistered = []
    modalities = {"table": 0, "document": 0, "image_ux": 0, "audio_video": 0}
    total_bytes = 0

    if case.is_dir():
        for item in sorted(case.rglob("*")):
            if not item.is_file() or item.name.startswith("case.sqlite3"):
                continue
            rel = item.relative_to(case).as_posix()
            if (
                rel in {"proposal.json", "recommendation.json", "cost.jsonl"}
                or rel.startswith("proposal-")
                or rel.endswith(".html")
            ):
                continue
            ext = item.suffix.lower()
            size = item.stat().st_size
            total_bytes += size
            if ext in TABLE_EXTS:
                modalities["table"] += 1
            elif ext in IMAGE_EXTS:
                modalities["image_ux"] += 1
            elif ext in AUDIO_VIDEO_EXTS:
                modalities["audio_video"] += 1
            elif ext in DOC_EXTS:
                modalities["document"] += 1
            if rel not in registered_paths:
                unregistered.append({"path": rel, "bytes": size, "ext": ext})

    return {
        "unregistered_files": unregistered,
        "modalities": modalities,
        "total_bytes": total_bytes,
    }


def route_next_step(
    case: Path,
    state: dict,
    sources_health: dict,
    *,
    budget: str = "balanced",
    failed_tool: str | None = None,
    error_reason: str | None = None,
) -> dict:
    """Score the current case state and select the lowest-cost sufficient process, model tier, and fallback."""
    if budget not in {"lean", "balanced", "thorough"}:
        raise ValueError("budget must be lean, balanced, or thorough")

    registered_paths = {
        info["path"] for info in sources_health.values() if "path" in info
    }
    scan = inspect_workspace_inputs(case, registered_paths)
    stage = state["checkpoint"]["stage"]
    decisions = state["decisions"]
    draft = state["draft"]
    approved_imp = impact(decisions[-1]["proposal"], sources_health) if decisions else None

    steps: list[dict] = []
    recovery = None

    # Handle explicit failure recovery without repeating completed work.
    if failed_tool:
        if failed_tool in {"web_search", "web_fetch"}:
            recovery = {
                "failed_tool": failed_tool,
                "reason": error_reason or "Источник в сети недоступен или закрыт",
                "corrected_action": "Зафиксировать недоступность URL в checkpoint/findings как 'unknown', опереться на локальные первичные документы и назначить повторную проверку в actions.",
                "cost_tier": "zero_llm",
            }
        elif failed_tool in {"model_call", "subagent"}:
            recovery = {
                "failed_tool": failed_tool,
                "reason": error_reason or "Модель недоступна или превышен лимит",
                "corrected_action": "Переключиться на доступную модель меньшего/соседнего тира, восстановить состояние из SQLite checkpoint без повторного чтения уже зарегистрированных CSV.",
                "cost_tier": "low",
            }
        elif failed_tool == "rates":
            recovery = {
                "failed_tool": "rates",
                "reason": error_reason or "Ошибка схемы или кодировки CSV",
                "corrected_action": "Проверить data_dictionary и заголовок CSV (UTF-8, без пустых меток и числителей > знаменателя) перед повторным вызовом.",
                "cost_tier": "zero_llm",
            }
        else:
            recovery = {
                "failed_tool": failed_tool,
                "reason": error_reason or "Сбой инструмента",
                "corrected_action": "Сохранить checkpoint с причиной сбоя и перейти к локальному детерминированному шагу CLI.",
                "cost_tier": "low",
            }

    # Feature-based routing rules:
    # 1. Post-decision changed or unreviewed evidence -> targeted follow-up!
    if decisions and approved_imp and any(approved_imp.values()):
        steps.append({
            "step": "targeted_impact_recount",
            "process": "follow-up",
            "tool": "ledger status + ledger rates --ci --guardrail",
            "skill": "decision-followup",
            "subagent": None,
            "model_tier": "zero_llm_then_reasoning",
            "estimated_cost_tier": "low",
            "why": (
                f"Обнаружены изменения в источниках ({', '.join(approved_imp['sources'] or approved_imp['unreviewed'])}). "
                f"Пересчитать только {len(approved_imp['findings'])} затронутых утверждений вместо полного перезапуска."
            ),
            "stop_condition": "Все затронутые узлы обновлены в новом черновике и проверены через audit-claims.",
        })

    # 2. Unregistered files in workspace -> register and route by modality!
    if scan["unregistered_files"]:
        steps.append({
            "step": "triage_new_inputs",
            "process": "intake_or_followup",
            "tool": "ledger source",
            "skill": "problem-framing",
            "subagent": "evidence-extractor" if scan["total_bytes"] > 12_000 and budget != "lean" else None,
            "model_tier": "fast" if scan["total_bytes"] > 12_000 else "zero_llm",
            "estimated_cost_tier": "low",
            "why": f"Найдено {len(scan['unregistered_files'])} незарегистрированных файлов ({scan['total_bytes']} байт).",
            "stop_condition": "Все разрешённые входные файлы зарегистрированы с SHA-256.",
        })

    # 3. Quantitative CSV tables -> ALWAYS route to zero-LLM deterministic CLI!
    has_tables = any(s["kind"] in {"table", "synthetic"} and s["path"].endswith(".csv") for s in sources_health.values())
    if has_tables and stage in {"intake", "research", "follow-up"}:
        steps.append({
            "step": "cohort_and_wilson_audit",
            "process": "quantitative_analysis",
            "tool": "ledger rates --ci --guardrail + ledger economics",
            "skill": "cohort-and-economics",
            "subagent": None,
            "model_tier": "zero_llm",
            "estimated_cost_tier": "zero_llm",
            "why": "Точный расчёт долей, 95% ДИ Вильсона, проверка парадокса Симпсона и юнит-экономики без траты токенов LLM.",
            "stop_condition": "Получены общие и когортные доли, интервалы и запас маржинальности.",
        })

    # 4. UX screenshots or interview / synthetic persona probes when qualitative gaps exist.
    has_ux_or_interview = (
        scan["modalities"]["image_ux"] > 0
        or any(s["kind"] in {"ux", "interview"} for s in sources_health.values())
    )
    if has_ux_or_interview and stage in {"intake", "research"}:
        steps.append({
            "step": "ux_and_interview_synthesis",
            "process": "qualitative_analysis",
            "tool": "read_file (image/transcript) + ledger kb",
            "skill": "synthetic-interviews-ux",
            "subagent": "synthetic-respondent" if budget == "thorough" else "evidence-extractor",
            "model_tier": "fast_multimodal",
            "estimated_cost_tier": "low",
            "why": "Разбор видимых элементов интерфейса и кодирование интервью/синтетических респондентов без смешения с рыночными фактами.",
            "stop_condition": "Зафиксированы барьеры восприятия и проверяемые гипотезы для коммуникации.",
        })

    # 5. Strategy synthesis + skeptic review before human gate.
    if not draft and stage in {"intake", "research", "review"}:
        steps.append({
            "step": "strategy_and_skeptic_gate",
            "process": "strategy_synthesis",
            "tool": "ledger propose --strict-claims + ledger audit-claims + ledger dashboard",
            "skill": "product-frameworks + strategy-and-claims",
            "subagent": "skeptic-reviewer" if budget != "lean" else None,
            "model_tier": "deep_reasoning",
            "estimated_cost_tier": "medium",
            "why": "Сборка JTBD, дерева OST, Pre-mortem, матрицы обещаний и >=3 вариантов (keep/change/close) перед гейтом человека.",
            "stop_condition": "propose и audit-claims проходят без high-severity предупреждений; ждём подтверждения человека.",
        })
    elif draft:
        steps.append({
            "step": "human_approval_gate",
            "process": "review",
            "tool": "ledger view --draft + ledger dashboard",
            "skill": "strategy-and-claims",
            "subagent": None,
            "model_tier": "human_in_the_loop",
            "estimated_cost_tier": "zero_llm",
            "why": "Черновик подготовлен. Принятие решения требует явной команды руководителя с проверкой SHA.",
            "stop_condition": "Руководитель утвердил вариант (accept) или дал правки.",
        })

    if not steps:
        steps.append({
            "step": "monitor_commitments",
            "process": "follow-up",
            "tool": "ledger status + ledger dashboard",
            "skill": "decision-followup",
            "subagent": None,
            "model_tier": "zero_llm",
            "estimated_cost_tier": "zero_llm",
            "why": "Решение утверждено, источники актуальны. Контроль сроков обязательств и триггеров пересмотра.",
            "stop_condition": "Новые данные поступили или обязательства закрыты через done.",
        })

    return {
        "stage": stage,
        "budget": budget,
        "workspace_scan": scan,
        "recovery": recovery,
        "primary_route": steps[0],
        "pipeline": steps,
    }


def record_telemetry(
    state: dict,
    *,
    step: str,
    model: str = "zero_llm",
    seconds: float = 0.0,
    input_tokens: int = 0,
    cached_tokens: int = 0,
    output_tokens: int = 0,
    tool_calls: int = 1,
    retries: int = 0,
    human_intervention: str = "",
    note: str = "",
) -> dict:
    if seconds < 0 or input_tokens < 0 or cached_tokens < 0 or output_tokens < 0 or tool_calls < 0 or retries < 0:
        raise ValueError("Telemetry counters must be non-negative")
    log = state.setdefault("telemetry", [])
    entry = {
        "at": now(),
        "step": step.strip(),
        "model": model.strip() or "zero_llm",
        "seconds": round(float(seconds), 2),
        "input_tokens": int(input_tokens),
        "cached_tokens": int(cached_tokens),
        "output_tokens": int(output_tokens),
        "tool_calls": int(tool_calls),
        "retries": int(retries),
        "human_intervention": human_intervention.strip() or None,
        "note": note.strip() or None,
    }
    log.append(entry)
    return summarize_telemetry(log)


def summarize_telemetry(log: list[dict]) -> dict:
    return {
        "events": len(log),
        "total_seconds": round(sum(item["seconds"] for item in log), 2),
        "input_tokens": sum(item["input_tokens"] for item in log),
        "cached_tokens": sum(item["cached_tokens"] for item in log),
        "output_tokens": sum(item["output_tokens"] for item in log),
        "tool_calls": sum(item["tool_calls"] for item in log),
        "retries": sum(item["retries"] for item in log),
        "human_interventions": [item["human_intervention"] for item in log if item.get("human_intervention")],
        "models_used": sorted({item["model"] for item in log}),
        "recent": log[-5:],
    }
