"""Summarize experiment counts from a UTF-8 CSV/TSV or OpenXML (.xlsx) file with Wilson CIs and guardrails."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from .stats import detect_cohort_anomalies, evaluate_guardrail, parse_guardrail, wilson_interval
from .xlsx import read_xlsx_rows


def _count(value: str, column: str, line: int) -> int:
    try:
        if value.removeprefix("+").isdecimal():
            return int(value)
    except ValueError as error:
        raise ValueError(f"Line {line}: invalid integer in column {column!r}: {error}") from error
    raise ValueError(f"Line {line}: column {column!r} requires a non-negative integer")


def _group_rates(
    groups: dict[str, Counter[str]],
    denominator: str,
    numerators: list[str],
    *,
    include_ci: bool = False,
    parsed_guardrails: list[tuple[str, str, float]] | None = None,
) -> dict:
    guardrail_map = {}
    for metric, op, threshold in parsed_guardrails or []:
        guardrail_map.setdefault(metric, []).append((op, threshold))

    result = {}
    for group, totals in sorted(groups.items()):
        total = totals[denominator]
        num_summary = {}
        for name in numerators:
            count = totals[name]
            rate = count / total if total else None
            entry: dict = {"count": count, "rate": rate}
            if include_ci or parsed_guardrails:
                bounds = wilson_interval(count, total)
                entry["ci95_low"] = bounds[0] if bounds else None
                entry["ci95_high"] = bounds[1] if bounds else None
                if name in guardrail_map:
                    entry["guardrails"] = {
                        f"{op}{threshold}": evaluate_guardrail(rate, bounds, op, threshold)
                        for op, threshold in guardrail_map[name]
                    }
            num_summary[name] = entry
        result[group] = {
            "denominator": total,
            "numerators": num_summary,
        }
    return result


def _iter_table_rows(path: Path, *, sheet: str | int | None = None) -> tuple[str | None, list[str], list[tuple[int, list[str]]]]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        sheet_name, xlsx_rows = read_xlsx_rows(path, sheet=sheet)
        if not xlsx_rows:
            raise ValueError("XLSX header is missing")
        _, raw_header = xlsx_rows[0]
        header = [cell.strip() for cell in raw_header]
        while header and header[-1] == "":
            header.pop()
        if not header:
            raise ValueError("XLSX header is missing")
        body: list[tuple[int, list[str]]] = []
        for line_num, vals in xlsx_rows[1:]:
            if len(vals) > len(header) and any(c.strip() for c in vals[len(header):]):
                raise ValueError(f"Line {line_num}: expected {len(header)} fields, got {len(vals)}")
            padded = list(vals[: len(header)]) + [""] * max(0, len(header) - len(vals))
            body.append((line_num, padded))
        return sheet_name, header, body

    if sheet is not None:
        raise ValueError("--sheet is supported only for .xlsx files")

    delim = "\t" if suffix == ".tsv" else ","
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, delimiter=delim, strict=True)
        try:
            header = [cell.strip() for cell in next(reader, [])]
            if not header:
                raise ValueError("CSV header is missing")
            body = [(reader.line_num, row) for row in reader]
        except csv.Error as error:
            raise ValueError(f"Invalid CSV at line {reader.line_num}: {error}") from error
        except UnicodeDecodeError as error:
            raise ValueError("CSV must be encoded as UTF-8") from error
    return None, header, body


def summarize_rates(
    path: Path,
    *,
    group: str,
    segment: str | None,
    denominator: str,
    numerators: list[str],
    ci: bool = False,
    guardrails: list[str] | None = None,
    sheet: str | int | None = None,
) -> dict:
    """Sum counts by group and optional segment before calculating rates."""
    labels = [group] if segment is None else [group, segment]
    count_columns = [denominator, *numerators]
    required = [*labels, *count_columns]
    if len(set(required)) != len(required):
        raise ValueError("Group, segment, denominator and numerator columns must be distinct")

    parsed_guardrails = [parse_guardrail(item) for item in (guardrails or [])]
    for metric, _, _ in parsed_guardrails:
        if metric not in numerators:
            raise ValueError(f"Guardrail metric {metric!r} must be listed in numerators")

    overall: dict[str, Counter[str]] = defaultdict(Counter)
    segments: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    rows = 0

    sheet_name, header, body = _iter_table_rows(path, sheet=sheet)
    if len(set(header)) != len(header):
        raise ValueError("Duplicate CSV headers")
    missing = [name for name in required if name not in header]
    if missing:
        raise ValueError(f"Missing CSV columns: {', '.join(missing)}")

    for line, row in body:
        if len(row) != len(header):
            raise ValueError(f"Line {line}: expected {len(header)} fields, got {len(row)}")
        fields = dict(zip(header, (cell.strip() for cell in row)))
        for name in labels:
            if not fields[name]:
                raise ValueError(f"Line {line}: empty label in column {name!r}")

        counts = {name: _count(fields[name], name, line) for name in count_columns}
        for name in numerators:
            if counts[name] > counts[denominator]:
                raise ValueError(
                    f"Line {line}: {name!r} exceeds denominator {denominator!r}"
                )

        group_label = fields[group]
        overall[group_label].update(counts)
        if segment is not None:
            segments[fields[segment]][group_label].update(counts)
        rows += 1

    extended = bool(ci or parsed_guardrails)
    overall_rates = _group_rates(
        overall, denominator, numerators, include_ci=extended, parsed_guardrails=parsed_guardrails
    )
    segment_rates = {
        label: _group_rates(
            groups, denominator, numerators, include_ci=extended, parsed_guardrails=parsed_guardrails
        )
        for label, groups in sorted(segments.items())
    }
    response: dict = {
        "rows": rows,
        "overall": overall_rates,
        "segments": segment_rates,
    }
    if sheet_name is not None:
        response["sheet"] = sheet_name
    if extended:
        response["anomalies"] = detect_cohort_anomalies(
            overall_rates, segment_rates, numerators, parsed_guardrails
        )
    return response
