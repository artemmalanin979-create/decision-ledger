#!/usr/bin/env sh
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
git -C "$ROOT" config core.hooksPath hooks
chmod +x "$ROOT/hooks/pre-commit"
echo "Installed git hooks from $ROOT/hooks"
