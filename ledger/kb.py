"""Searchable cross-session knowledge base with epistemic guardrails and retraction."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from .store import now

KB_FILENAME = "knowledge.sqlite3"
ORIGIN_CLASSES = {"primary_evidence", "synthetic_probe", "prior_conclusion"}
RELATIONS = {"supports", "contradicts", "refines", "supersedes"}

EPISTEMIC_NOTES = {
    "primary_evidence": "Проверенный источник с привязкой к снимку: допустим для фактов и наблюдений в рамках его охвата.",
    "synthetic_probe": "Синтетический материал или модельный респондент: допустим только для генерации гипотез и сценариев, не доказывает спрос и рыночный факт.",
    "prior_conclusion": "Прежний вывод или решение агента: память между сеансами, не превращается в первичный факт без проверки исходных источников.",
}


def _tokenize_ru_en(query: str) -> list[str]:
    tokens = re.findall(r"[0-9A-Za-zА-Яа-яЁё_-]{2,}", query.lower())
    stems = []
    for token in tokens:
        # Strip common Russian case endings on words >= 5 chars so prefix FTS matches inflections.
        if len(token) >= 6 and re.fullmatch(r"[а-яё]+", token):
            stem = re.sub(r"(ами|ями|ого|его|ому|ему|ыми|ими|иях|ах|ях|ов|ев|ей|ий|ый|ая|яя|ое|ее|ую|юю|ом|ем|ам|ям|ами|ии|ия|ие|ью|а|я|о|е|ы|и|у|ю)$", "", token)
            stems.append(stem if len(stem) >= 3 else token[:4])
        else:
            stems.append(token)
    return list(dict.fromkeys(stems))


@contextmanager
def _open_kb(kb_dir: Path, *, write: bool = False):
    kb_dir.mkdir(parents=True, exist_ok=True)
    db_path = kb_dir / KB_FILENAME
    if db_path.is_symlink():
        raise ValueError("Knowledge base file must not be a symlink")
    with closing(sqlite3.connect(str(db_path))) as db, db:
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_entries (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                origin_class TEXT NOT NULL,
                case_name TEXT,
                source_sha TEXT,
                locator TEXT,
                tags TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                retracted_reason TEXT,
                superseded_by TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS kb_links (
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (from_id, to_id, relation)
            )
            """
        )
        has_fts = True
        try:
            db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
                    id UNINDEXED,
                    title,
                    body,
                    tags,
                    tokenize='unicode61'
                )
                """
            )
        except sqlite3.OperationalError:
            has_fts = False
        yield db, has_fts


def upsert_entry(
    kb_dir: Path,
    *,
    entry_id: str,
    title: str,
    body: str,
    origin_class: str,
    case_name: str = "",
    source_sha: str = "",
    locator: str = "",
    tags: list[str] | None = None,
) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]*", entry_id):
        raise ValueError("KB entry ID must use lowercase letters, digits, dots, colons, hyphens or underscores")
    if origin_class not in ORIGIN_CLASSES:
        raise ValueError(f"origin_class must be one of {sorted(ORIGIN_CLASSES)}")
    if not title.strip() or not body.strip():
        raise ValueError("KB entry requires non-empty title and body")
    if origin_class == "primary_evidence" and not source_sha.strip():
        raise ValueError("primary_evidence entries require source_sha to preserve provenance")

    clean_tags = sorted({tag.strip().lower() for tag in (tags or []) if tag.strip()})
    tags_text = " ".join(clean_tags)
    timestamp = now()

    with _open_kb(kb_dir, write=True) as (db, has_fts):
        db.execute(
            """
            INSERT INTO kb_entries (
                id, title, body, origin_class, case_name, source_sha, locator,
                tags, status, retracted_reason, superseded_by, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                body = excluded.body,
                origin_class = excluded.origin_class,
                case_name = excluded.case_name,
                source_sha = excluded.source_sha,
                locator = excluded.locator,
                tags = excluded.tags,
                status = 'active',
                retracted_reason = NULL,
                superseded_by = NULL,
                updated_at = excluded.updated_at
            """,
            (entry_id, title.strip(), body.strip(), origin_class, case_name.strip(),
             source_sha.strip(), locator.strip(), json.dumps(clean_tags, ensure_ascii=False), timestamp),
        )
        if has_fts:
            db.execute("DELETE FROM kb_fts WHERE id = ?", (entry_id,))
            db.execute(
                "INSERT INTO kb_fts (id, title, body, tags) VALUES (?, ?, ?, ?)",
                (entry_id, title.strip(), body.strip(), tags_text),
            )

    return {
        "id": entry_id,
        "origin_class": origin_class,
        "can_cite_as_fact": origin_class == "primary_evidence",
        "status": "active",
        "updated_at": timestamp,
    }


def link_entries(kb_dir: Path, *, from_id: str, to_id: str, relation: str, note: str = "") -> dict:
    if relation not in RELATIONS:
        raise ValueError(f"relation must be one of {sorted(RELATIONS)}")
    if from_id == to_id:
        raise ValueError("Self-links are not allowed")

    timestamp = now()
    with _open_kb(kb_dir, write=True) as (db, _):
        found = {
            row[0]
            for row in db.execute(
                "SELECT id FROM kb_entries WHERE id IN (?, ?)", (from_id, to_id)
            ).fetchall()
        }
        if found != {from_id, to_id}:
            raise ValueError("Both KB entries must exist before linking")
        db.execute(
            """
            INSERT OR REPLACE INTO kb_links (from_id, to_id, relation, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (from_id, to_id, relation, note.strip(), timestamp),
        )
        if relation == "supersedes":
            db.execute(
                "UPDATE kb_entries SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?",
                (from_id, timestamp, to_id),
            )
    return {"from": from_id, "to": to_id, "relation": relation}


def retract_entry(kb_dir: Path, *, entry_id: str, reason: str, superseded_by: str = "") -> dict:
    if not reason.strip():
        raise ValueError("Retraction reason is required")
    timestamp = now()
    with _open_kb(kb_dir, write=True) as (db, _):
        row = db.execute("SELECT id FROM kb_entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown KB entry: {entry_id}")
        new_status = "superseded" if superseded_by.strip() else "retracted"
        db.execute(
            """
            UPDATE kb_entries
            SET status = ?, retracted_reason = ?, superseded_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_status, reason.strip(), superseded_by.strip() or None, timestamp, entry_id),
        )
        if superseded_by.strip():
            target = db.execute("SELECT id FROM kb_entries WHERE id = ?", (superseded_by.strip(),)).fetchone()
            if not target:
                raise ValueError(f"Replacement entry does not exist: {superseded_by}")
            db.execute(
                """
                INSERT OR REPLACE INTO kb_links (from_id, to_id, relation, note, created_at)
                VALUES (?, ?, 'supersedes', ?, ?)
                """,
                (superseded_by.strip(), entry_id, reason.strip(), timestamp),
            )
    return {"id": entry_id, "status": new_status, "reason": reason.strip(), "superseded_by": superseded_by.strip() or None}


def _format_row(row: tuple, db: sqlite3.Connection) -> dict:
    (
        entry_id, title, body, origin_class, case_name, source_sha,
        locator, tags_json, status, retracted_reason, superseded_by, updated_at
    ) = row
    links_out = [
        {"to": r[0], "relation": r[1], "note": r[2]}
        for r in db.execute(
            "SELECT to_id, relation, note FROM kb_links WHERE from_id = ? ORDER BY relation, to_id",
            (entry_id,),
        ).fetchall()
    ]
    links_in = [
        {"from": r[0], "relation": r[1], "note": r[2]}
        for r in db.execute(
            "SELECT from_id, relation, note FROM kb_links WHERE to_id = ? ORDER BY relation, from_id",
            (entry_id,),
        ).fetchall()
    ]
    can_cite_as_fact = origin_class == "primary_evidence" and status == "active"
    return {
        "id": entry_id,
        "title": title,
        "body": body,
        "origin_class": origin_class,
        "can_cite_as_fact": can_cite_as_fact,
        "epistemic_note": EPISTEMIC_NOTES[origin_class],
        "case": case_name or None,
        "source_sha": source_sha or None,
        "locator": locator or None,
        "tags": json.loads(tags_json),
        "status": status,
        "retracted_reason": retracted_reason,
        "superseded_by": superseded_by,
        "updated_at": updated_at,
        "links": {"outgoing": links_out, "incoming": links_in},
    }


def search_kb(
    kb_dir: Path,
    query: str = "",
    *,
    tag: str | None = None,
    origin_class: str | None = None,
    include_inactive: bool = False,
    limit: int = 10,
) -> dict:
    if origin_class and origin_class not in ORIGIN_CLASSES:
        raise ValueError(f"Invalid origin_class: {origin_class}")

    with _open_kb(kb_dir, write=False) as (db, has_fts):
        matched_ids: list[str] | None = None
        stems = _tokenize_ru_en(query) if query.strip() else []
        if stems:
            if has_fts:
                fts_expr = " OR ".join(f'"{stem}"*' for stem in stems)
                rows = db.execute(
                    "SELECT id FROM kb_fts WHERE kb_fts MATCH ? ORDER BY bm25(kb_fts) LIMIT ?",
                    (fts_expr, max(limit * 4, 20)),
                ).fetchall()
                matched_ids = [r[0] for r in rows]
            if not matched_ids:
                # Substring fallback for inflected stems or custom tokens.
                all_rows = db.execute("SELECT id, title, body, tags FROM kb_entries").fetchall()
                scored = []
                for eid, title, body, tags_json in all_rows:
                    haystack = f"{title} {body} {tags_json}".lower()
                    score = sum(1 for stem in stems if stem in haystack)
                    if score > 0:
                        scored.append((-score, eid))
                scored.sort()
                matched_ids = [eid for _, eid in scored]

        sql = """
            SELECT id, title, body, origin_class, case_name, source_sha, locator,
                   tags, status, retracted_reason, superseded_by, updated_at
            FROM kb_entries
        """
        clauses = []
        params: list = []
        if not include_inactive:
            clauses.append("status = 'active'")
        if origin_class:
            clauses.append("origin_class = ?")
            params.append(origin_class)
        if matched_ids is not None:
            if not matched_ids:
                return {"query": query, "count": 0, "results": []}
            placeholders = ", ".join("?" for _ in matched_ids)
            clauses.append(f"id IN ({placeholders})")
            params.extend(matched_ids)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"

        fetched = db.execute(sql, params).fetchall()
        rank = {eid: idx for idx, eid in enumerate(matched_ids)} if matched_ids is not None else {}
        if matched_ids is not None:
            fetched.sort(key=lambda r: rank.get(r[0], 10**9))

        results = []
        tag_norm = tag.strip().lower() if tag else None
        for row in fetched:
            formatted = _format_row(row, db)
            if tag_norm and tag_norm not in formatted["tags"]:
                continue
            results.append(formatted)
            if len(results) >= limit:
                break

    return {
        "query": query,
        "include_inactive": include_inactive,
        "count": len(results),
        "results": results,
    }


def ingest_case_into_kb(kb_dir: Path, case_dir: Path, state: dict) -> dict:
    """Index active case sources and approved decision conclusions with strict epistemic tags."""
    case_name = case_dir.name
    indexed_sources = 0
    indexed_decisions = 0

    for source_id, record in sorted(state.get("sources", {}).items()):
        if record.get("retired"):
            continue
        latest = record["versions"][-1]
        kind = record["kind"]
        origin = "synthetic_probe" if kind == "synthetic" else "primary_evidence"
        snippet = ""
        source_path = case_dir / latest["path"]
        if source_path.is_file() and kind in {"document", "web", "interview", "ux", "synthetic"}:
            try:
                snippet = source_path.read_text(encoding="utf-8-sig")[:1200]
            except UnicodeDecodeError:
                snippet = f"Бинарный или нетекстовый источник ({latest['path']})."
        else:
            snippet = f"Источник типа {kind} ({latest['path']})."

        upsert_entry(
            kb_dir,
            entry_id=f"{case_name}.source.{source_id}",
            title=f"[{case_name}] Источник {source_id} ({kind})",
            body=snippet,
            origin_class=origin,
            case_name=case_name,
            source_sha=latest["sha"] if origin == "primary_evidence" else "",
            locator=latest["path"],
            tags=[case_name, kind, source_id],
        )
        indexed_sources += 1

    decisions = state.get("decisions", [])
    prev_entry_id = None
    for decision in decisions:
        rev = decision["revision"]
        prop = decision["proposal"]
        entry_id = f"{case_name}.decision.r{rev}"
        upsert_entry(
            kb_dir,
            entry_id=entry_id,
            title=f"[{case_name}] Решение №{rev}: {prop['title']}",
            body=f"{prop['summary']}\nВыбранный вариант: {decision['option']}. Основание: {decision['reason']}",
            origin_class="prior_conclusion",
            case_name=case_name,
            locator=f"decision:{rev}",
            tags=[case_name, "decision", decision["option"]],
        )
        if prev_entry_id:
            link_entries(
                kb_dir,
                from_id=entry_id,
                to_id=prev_entry_id,
                relation="supersedes",
                note=f"Пересмотр решения в ревизии №{rev}",
            )
        prev_entry_id = entry_id
        indexed_decisions += 1

    return {
        "case": case_name,
        "indexed_sources": indexed_sources,
        "indexed_decisions": indexed_decisions,
    }
