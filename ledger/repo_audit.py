"""Repository safety, git-history secret scan, and personal-data hygiene checks."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
import re
import subprocess

SECRET_PATTERNS = [
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("xai_api_key", re.compile(r"\bxai-[A-Za-z0-9_-]{20,}\b")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("yandex_iam_or_oauth", re.compile(r"\by0_[A-Za-z0-9_-]{25,}\b")),
    ("private_key_pem", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]

# Detect raw Russian mobile phone numbers (+7 9XX XXX-XX-XX) that could represent personal data (excludes 8-800 hotlines).
PERSONAL_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*9\d{2}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".pdf", ".zip"}
MAX_GZIP_TEXT_BYTES = 32 * 1024 * 1024


def _decode_blob_or_gzip(raw: bytes, *, is_gzip: bool) -> str | None:
    if is_gzip:
        try:
            with gzip.GzipFile(fileobj=__import__("io").BytesIO(raw)) as gz:
                raw = gz.read(MAX_GZIP_TEXT_BYTES + 1)
            if len(raw) > MAX_GZIP_TEXT_BYTES:
                return None
        except OSError:
            return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _scan_text_blob(rel: str, text: str, issues: list[dict], *, check_json: bool = True) -> None:
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            issues.append({"file": rel, "kind": "secret_leak", "detail": f"Подозрение на секрет ({label})"})

    if PERSONAL_PHONE_PATTERN.search(text):
        issues.append({"file": rel, "kind": "personal_data_phone", "detail": "Обнаружен мобильный номер формата +7/8-9XX (риск ПДн)"})

    if check_json and rel.lower().endswith(".json"):
        try:
            json.loads(text)
        except ValueError as err:
            issues.append({"file": rel, "kind": "invalid_json", "detail": str(err)})


def _scan_git_history(root: Path, issues: list[dict]) -> int:
    rev_res = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--all"],
        capture_output=True, text=True, check=False,
    )
    commits = [c.strip() for c in rev_res.stdout.splitlines() if c.strip()]
    seen_blobs: set[str] = set()
    scanned_blobs = 0

    for commit in commits:
        tree_res = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", commit],
            capture_output=True, text=True, check=False,
        )
        for line in tree_res.stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            _, obj_type, blob_sha, rel_path = parts
            if obj_type != "blob" or blob_sha in seen_blobs:
                continue
            seen_blobs.add(blob_sha)

            fname = Path(rel_path).name
            suffix = Path(rel_path).suffix.lower()
            if fname.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm")) or fname.startswith(".env"):
                issues.append({
                    "file": f"{commit[:7]}:{rel_path}",
                    "kind": "forbidden_history_artifact",
                    "detail": "В истории git найден запрещённый артефакт (.sqlite3 или .env)",
                })
                continue
            if suffix in BINARY_SUFFIXES:
                continue

            cat_res = subprocess.run(
                ["git", "-C", str(root), "cat-file", "-p", blob_sha],
                capture_output=True, check=False,
            )
            text = _decode_blob_or_gzip(cat_res.stdout, is_gzip=(suffix == ".gz"))
            if text is None:
                continue
            scanned_blobs += 1
            _scan_text_blob(f"history:{commit[:7]}:{rel_path}", text, issues, check_json=False)

    return scanned_blobs


def audit_repository(root: Path, *, scan_history: bool = False) -> dict:
    """Scan tracked project files (and optionally full git history) for secrets, PII phones, stray DBs, and invalid JSON."""
    root = root.resolve()
    issues: list[dict] = []

    git_dir = root / ".git"
    if git_dir.exists():
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, check=False,
        )
        paths = [root / line for line in res.stdout.splitlines() if line.strip()]
    else:
        paths = [
            p for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
            and ".cases" not in p.parts and ".kb" not in p.parts
        ]

    checked = 0
    for path in sorted(paths):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        checked += 1

        if path.name.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm")) or path.name.startswith(".env"):
            issues.append({"file": rel, "kind": "forbidden_artifact", "detail": "Базы состояния и .env не должны попадать в git"})
            continue

        if path.suffix.lower() in BINARY_SUFFIXES:
            continue

        is_gzip = path.suffix.lower() == ".gz"
        text = _decode_blob_or_gzip(path.read_bytes(), is_gzip=is_gzip)
        if text is None:
            continue

        _scan_text_blob(rel, text, issues, check_json=not is_gzip)

    history_blobs = 0
    if scan_history and git_dir.exists():
        history_blobs = _scan_git_history(root, issues)

    return {
        "ok": len(issues) == 0,
        "checked_files": checked,
        "scanned_history_blobs": history_blobs,
        "issues": issues,
    }
