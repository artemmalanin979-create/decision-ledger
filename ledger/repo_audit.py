"""Repository safety and hygiene checks used by git hooks and CI."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

SECRET_PATTERNS = [
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_pem", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]


def audit_repository(root: Path) -> dict:
    """Scan tracked or candidate project files for secrets, stray databases, and broken configs."""
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

        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".pdf", ".zip"}:
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                issues.append({"file": rel, "kind": "secret_leak", "detail": f"Подозрение на секрет ({label})"})

        if path.suffix.lower() == ".json":
            try:
                json.loads(text)
            except ValueError as err:
                issues.append({"file": rel, "kind": "invalid_json", "detail": str(err)})

    return {
        "ok": len(issues) == 0,
        "checked_files": checked,
        "issues": issues,
    }
