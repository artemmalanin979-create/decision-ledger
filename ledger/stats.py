"""Statistical confidence bounds, guardrail checks, and cohort anomaly detection."""

from __future__ import annotations

import math
import re

Z_95 = 1.959963984540054
_GUARDRAIL_RE = re.compile(r"^([a-zA-Z0-9_.-]+)\s*(<=|>=)\s*(0(?:\.\d+)?|1(?:\.0+)?|\d+(?:\.\d+)?%)$")


def wilson_interval(count: int, total: int, z: float = Z_95) -> tuple[float, float] | None:
    """Compute the 95% Wilson score confidence interval for a binomial proportion."""
    if total <= 0:
        return None
    if count < 0 or count > total:
        raise ValueError("Count must be between 0 and denominator")
    p = count / total
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denom
    half_width = (z / denom) * math.sqrt((p * (1.0 - p) / total) + (z2 / (4.0 * total * total)))
    low = max(0.0, center - half_width)
    high = min(1.0, center + half_width)
    return round(low, 6), round(high, 6)


def parse_guardrail(spec: str) -> tuple[str, str, float]:
    """Parse a guardrail specification like 'checkout_completed>=0.90' or 'false_success<=3%'."""
    match = _GUARDRAIL_RE.match(spec.strip())
    if not match:
        raise ValueError(
            f"Invalid guardrail {spec!r}: expected '<metric>>=<0..1>' or '<metric><=<0..1>' (or '%')"
        )
    metric, op, raw_value = match.groups()
    if raw_value.endswith("%"):
        threshold = float(raw_value[:-1]) / 100.0
    else:
        threshold = float(raw_value)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"Guardrail threshold out of [0, 1] range: {spec!r}")
    return metric, op, threshold


def evaluate_guardrail(
    rate: float | None, ci95: tuple[float, float] | None, op: str, threshold: float
) -> str:
    """Classify guardrail status distinguishing point-estimate pass from small-sample uncertainty."""
    if rate is None or ci95 is None:
        return "no_data"
    low, high = ci95
    if op == ">=":
        if rate < threshold:
            return "violated"
        if low < threshold:
            return "uncertain_small_sample"
        return "pass"
    if op == "<=":
        if rate > threshold:
            return "violated"
        if high > threshold:
            return "uncertain_small_sample"
        return "pass"
    raise ValueError(f"Unsupported operator: {op}")


def detect_cohort_anomalies(
    overall: dict,
    segments: dict[str, dict],
    numerators: list[str],
    parsed_guardrails: list[tuple[str, str, float]],
) -> list[dict]:
    """Detect Simpson's paradox reversals and segment guardrail failures masked by overall aggregates."""
    anomalies: list[dict] = []
    if not segments:
        return anomalies

    # 1. Masked guardrail failures or small-sample risks inside segments.
    for metric, op, threshold in parsed_guardrails:
        for arm, arm_summary in sorted(overall.items()):
            overall_metric = arm_summary["numerators"].get(metric)
            if not overall_metric:
                continue
            overall_rate = overall_metric["rate"]
            if overall_rate is None:
                continue
            overall_passes_point = (
                overall_rate >= threshold if op == ">=" else overall_rate <= threshold
            )
            if not overall_passes_point:
                continue
            for seg_name, seg_arms in sorted(segments.items()):
                if arm not in seg_arms:
                    continue
                seg_metric = seg_arms[arm]["numerators"][metric]
                seg_rate = seg_metric["rate"]
                seg_ci = (seg_metric["ci95_low"], seg_metric["ci95_high"]) if seg_rate is not None else None
                status = evaluate_guardrail(seg_rate, seg_ci, op, threshold)
                if status == "violated":
                    anomalies.append({
                        "kind": "masked_segment_failure",
                        "group": arm,
                        "segment": seg_name,
                        "metric": metric,
                        "guardrail": f"{metric}{op}{threshold}",
                        "overall_rate": round(overall_rate, 6),
                        "segment_rate": round(seg_rate, 6),
                        "segment_denominator": seg_arms[arm]["denominator"],
                    })
                elif status == "uncertain_small_sample":
                    anomalies.append({
                        "kind": "masked_segment_uncertainty",
                        "group": arm,
                        "segment": seg_name,
                        "metric": metric,
                        "guardrail": f"{metric}{op}{threshold}",
                        "overall_rate": round(overall_rate, 6),
                        "segment_rate": round(seg_rate, 6),
                        "ci95": list(seg_ci),
                        "segment_denominator": seg_arms[arm]["denominator"],
                    })

    # 2. Simpson's paradox reversals across pairs of arms.
    arms = sorted(overall.keys())
    for i, arm_a in enumerate(arms):
        for arm_b in arms[i + 1 :]:
            for metric in numerators:
                rate_a = overall[arm_a]["numerators"][metric]["rate"]
                rate_b = overall[arm_b]["numerators"][metric]["rate"]
                if rate_a is None or rate_b is None or rate_a == rate_b:
                    continue
                overall_sign = 1 if rate_a > rate_b else -1
                segment_signs = []
                compared_segments = []
                for seg_name, seg_arms in sorted(segments.items()):
                    if arm_a not in seg_arms or arm_b not in seg_arms:
                        continue
                    sa = seg_arms[arm_a]["numerators"][metric]["rate"]
                    sb = seg_arms[arm_b]["numerators"][metric]["rate"]
                    if sa is None or sb is None or sa == sb:
                        continue
                    segment_signs.append(1 if sa > sb else -1)
                    compared_segments.append(seg_name)
                if len(segment_signs) >= 2 and all(s == -overall_sign for s in segment_signs):
                    leader = arm_a if overall_sign > 0 else arm_b
                    segment_leader = arm_b if overall_sign > 0 else arm_a
                    anomalies.append({
                        "kind": "simpson_reversal",
                        "metric": metric,
                        "overall_leader": leader,
                        "segment_leader": segment_leader,
                        "segments": compared_segments,
                    })

    return anomalies
