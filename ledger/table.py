"""Summarize experiment counts from a UTF-8 CSV file."""

import csv
from collections import Counter, defaultdict
from pathlib import Path


def _count(value: str, column: str, line: int) -> int:
    try:
        if value.removeprefix("+").isdecimal():
            return int(value)
    except ValueError as error:
        raise ValueError(f"Line {line}: invalid integer in column {column!r}: {error}") from error
    raise ValueError(f"Line {line}: column {column!r} requires a non-negative integer")


def _group_rates(
    groups: dict[str, Counter[str]], denominator: str, numerators: list[str]
) -> dict:
    result = {}
    for group, totals in sorted(groups.items()):
        total = totals[denominator]
        result[group] = {
            "denominator": total,
            "numerators": {
                name: {"count": totals[name], "rate": totals[name] / total if total else None}
                for name in numerators
            },
        }
    return result


def summarize_rates(
    path: Path, *, group: str, segment: str | None, denominator: str, numerators: list[str]
) -> dict:
    """Sum counts by group and optional segment before calculating rates."""
    labels = [group] if segment is None else [group, segment]
    count_columns = [denominator, *numerators]
    required = [*labels, *count_columns]
    if len(set(required)) != len(required):
        raise ValueError("Group, segment, denominator and numerator columns must be distinct")

    overall: dict[str, Counter[str]] = defaultdict(Counter)
    segments: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    rows = 0
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, strict=True)
        try:
            header = [cell.strip() for cell in next(reader, [])]
            if not header:
                raise ValueError("CSV header is missing")
            if len(set(header)) != len(header):
                raise ValueError("Duplicate CSV headers")
            missing = [name for name in required if name not in header]
            if missing:
                raise ValueError(f"Missing CSV columns: {', '.join(missing)}")

            for row in reader:
                line = reader.line_num
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
        except csv.Error as error:
            raise ValueError(f"Invalid CSV at line {reader.line_num}: {error}") from error
        except UnicodeDecodeError as error:
            raise ValueError("CSV must be encoded as UTF-8") from error

    return {
        "rows": rows,
        "overall": _group_rates(overall, denominator, numerators),
        "segments": {
            label: _group_rates(groups, denominator, numerators)
            for label, groups in sorted(segments.items())
        },
    }
