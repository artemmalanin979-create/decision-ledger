"""Audit product-communication alignment, synthetic evidence misuse, promise matrix, and prompt injection."""

from __future__ import annotations

import re

UNCONDITIONAL_PATTERNS = [
    (re.compile(r"\b100\s*%"), "Обещание 100% результата без оговорки границ применимости"),
    (re.compile(r"\bгарантиру(?:ем|ет)\s+(?:продаж|бесперебойн|работ[уы]\s+всех|отсутствие\s+сбоев|полную\s+защит|возврат\s+всех)", re.I),
     "Безусловная гарантия отсутствия сбоев или полной защиты от любых потерь"),
    (re.compile(r"\b(?:всегда|при любых сбоях|на любой кассе|без единого сбоя|от любых мошенников|ни одного спам-звонка)\b", re.I),
     "Абсолютное обещание («всегда / на любой кассе / от любых мошенников»)"),
    (re.compile(r"\bни одной потерянной продажи\b", re.I),
     "Слоган «ни одной потерянной продажи» превышает проверяемые границы продукта"),
]

PROMPT_INJECTION_PATTERNS = [
    ("ignore_instructions", re.compile(r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|system)\s+(?:instructions|rules)", re.I)),
    ("ru_ignore_rules", re.compile(r"(?:игнорируй|забудь|отмени)\s+(?:все\s+)?(?:предыдущие\s+)?(?:правила|инструкции|ограничения)", re.I)),
    ("force_accept_command", re.compile(r"(?:выполни|запусти|вызови|run|execute)\s+(?:python3\s+-m\s+ledger\s+)?accept\b", re.I)),
    ("bypass_claims", re.compile(r"(?:пропусти|отключи|не\s+запускай|bypass|skip)\s+(?:проверку\s+)?(?:audit-claims|strict-claims)", re.I)),
    ("fake_system_tag", re.compile(r"<\s*(?:system|admin_override|developer_instruction)\s*>", re.I)),
]

CONDITION_MARKERS = re.compile(
    r"(при условии|услови[яй]|совместим|после проверки|ограничен|кроме|исключени|при наличии|не гарантиру|не покрыва|в рамках)",
    re.I,
)

STOP_RULE_MARKERS = re.compile(
    r"(стоп|останов|прекрат|откат|порог|лимит|не более|не менее|пересмотр|критери[йи])",
    re.I,
)

DEMAND_CLAIM_MARKERS = re.compile(
    r"(доказывает спрос|подтверждает рыночный спрос|рынок готов платить|массовый спрос доказан)",
    re.I,
)


def scan_untrusted_text(text: str) -> list[dict]:
    """Detect prompt-injection directives embedded inside external documents or web snapshots."""
    hits: list[dict] = []
    for code, pattern in PROMPT_INJECTION_PATTERNS:
        m = pattern.search(text or "")
        if m:
            snippet = re.sub(r"\s+", " ", m.group(0))[:80]
            hits.append({
                "code": code,
                "matched": snippet,
                "action": "quarantined_as_passive_data",
            })
    return hits


def _collect_cited_sources(finding_id: str, findings: dict, visited: set[str] | None = None) -> set[str]:
    visited = visited or set()
    if finding_id in visited or finding_id not in findings:
        return set()
    visited.add(finding_id)
    node = findings[finding_id]
    sources = {item["source"] for item in node.get("citations", [])}
    for parent in node.get("depends_on", []):
        sources |= _collect_cited_sources(parent, findings, visited)
    return sources


def _find_overclaims(text: str) -> list[dict]:
    overclaims = []
    for pattern, reason in UNCONDITIONAL_PATTERNS:
        match = pattern.search(text or "")
        if match:
            start = max(0, match.start() - 35)
            prefix = text[start : match.start()].lower()
            if any(neg in prefix for neg in ("не ", "нельзя", "избега", "отказ от", "без обещан")):
                continue
            overclaims.append({"match": match.group(0), "reason": reason})
    return overclaims


def audit_claims(proposal: dict, sources: dict) -> dict:
    """Inspect a proposal for overclaiming in communication, synthetic-only demand claims, promise matrix, and stop-rules."""
    warnings: list[dict] = []
    checks: list[dict] = []

    findings = proposal.get("findings", {})
    strategy = proposal.get("strategy", {})
    comm_text = strategy.get("communication", {}).get("text", "")
    prod_text = strategy.get("product", {}).get("text", "")
    exp_text = strategy.get("experiment", {}).get("text", "")
    promise_matrix = (proposal.get("methodology") or {}).get("promise_matrix") or []

    has_uncertainties = any(
        f.get("kind") in {"unknown", "hypothesis", "forecast"} for f in findings.values()
    )

    # 1. Check unconditional marketing claims vs uncertainties (both communication text and promise_matrix).
    overclaims = _find_overclaims(comm_text)
    for idx, row in enumerate(promise_matrix, 1):
        for oc in _find_overclaims(row.get("promise", "")):
            overclaims.append({"match": f"promise_matrix[{idx}]: {oc['match']}", "reason": oc["reason"]})

    if overclaims:
        for item in overclaims:
            warnings.append({
                "code": "overclaim_in_communication",
                "severity": "high",
                "message": f"{item['reason']}: найдено «{item['match']}» в коммуникационной стратегии.",
            })
        checks.append({"check": "no_unconditional_overclaims", "passed": False})
    else:
        checks.append({"check": "no_unconditional_overclaims", "passed": True})

    # 2. Check explicit boundary conditions in communication when uncertainties exist.
    has_conditions = bool(CONDITION_MARKERS.search(comm_text))
    if has_uncertainties and not has_conditions:
        warnings.append({
            "code": "missing_claim_boundaries",
            "severity": "medium",
            "message": "В основаниях есть неизвестные или гипотезы, но в коммуникационной стратегии не указаны условия применимости обещания.",
        })
        checks.append({"check": "communication_boundaries_stated", "passed": False})
    else:
        checks.append({"check": "communication_boundaries_stated", "passed": True})

    # 3. Check synthetic-only demand claims across findings and promise_matrix.
    synthetic_misuse = False
    for fid, finding in sorted(findings.items()):
        text = finding.get("text", "")
        cited = _collect_cited_sources(fid, findings)
        only_synthetic = bool(cited) and all(
            sources.get(src, {}).get("kind") == "synthetic" for src in cited
        )
        if only_synthetic and DEMAND_CLAIM_MARKERS.search(text):
            synthetic_misuse = True
            warnings.append({
                "code": "synthetic_demand_claim",
                "severity": "high",
                "message": f"Утверждение {fid} заявляет доказанный спрос, опираясь только на синтетические источники ({', '.join(sorted(cited))}).",
            })

    for idx, row in enumerate(promise_matrix, 1):
        proof_ids = row.get("proof_findings", [])
        cited_union: set[str] = set()
        for fid in proof_ids:
            cited_union |= _collect_cited_sources(fid, findings)
        if cited_union and all(sources.get(src, {}).get("kind") == "synthetic" for src in cited_union):
            synthetic_misuse = True
            warnings.append({
                "code": "synthetic_promise_proof",
                "severity": "high",
                "message": f"Обещание №{idx} в promise_matrix опирается исключительно на синтетические данные ({', '.join(sorted(cited_union))}).",
            })
    checks.append({"check": "synthetic_not_used_as_demand_proof", "passed": not synthetic_misuse})

    # 4. Check stop-rule / guardrail presence in experiment block.
    has_stop_rule = bool(STOP_RULE_MARKERS.search(exp_text))
    if not has_stop_rule:
        warnings.append({
            "code": "missing_experiment_stop_rule",
            "severity": "medium",
            "message": "В плане проверки не найдено явного порога, стоп-правила или критерия пересмотра.",
        })
    checks.append({"check": "experiment_has_stop_rule", "passed": has_stop_rule})

    # 5. Check product-communication scope alignment.
    product_restricts = bool(re.search(r"(только для|исключа|отлож|не допуск)", prod_text, re.I))
    if product_restricts and not has_conditions:
        warnings.append({
            "code": "scope_mismatch_product_vs_comm",
            "severity": "high",
            "message": "Продуктовая стратегия ограничивает сегмент запуска, но в коммуникации нет соответствующей оговорки.",
        })
        checks.append({"check": "product_communication_scope_aligned", "passed": False})
    else:
        checks.append({"check": "product_communication_scope_aligned", "passed": True})

    # 6. Surface quarantined prompt-injection attempts inside sources as informational audit trace.
    flagged_sources = [
        sid for sid, srec in sorted(sources.items())
        if srec.get("injection_flags")
    ]
    if flagged_sources:
        warnings.append({
            "code": "untrusted_source_instruction_quarantined",
            "severity": "low",
            "message": f"Во внешних источниках ({', '.join(flagged_sources)}) обнаружены и изолированы императивные директивы (prompt injection).",
        })
    checks.append({"check": "prompt_injections_quarantined", "passed": True, "flagged_sources": flagged_sources})

    high_count = sum(1 for w in warnings if w["severity"] == "high")
    return {
        "passed": high_count == 0,
        "high_severity_warnings": high_count,
        "total_warnings": len(warnings),
        "checks": checks,
        "warnings": warnings,
    }
