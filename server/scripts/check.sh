#!/usr/bin/env bash
# Server checks: tests, lint, format, types. Run from anywhere.
# ruff/pyright versions match the dev-group pins in pyproject.toml; pyright
# resolves imports from server/.venv, which `uv run` (pytest) syncs first.
set -euo pipefail
cd "$(dirname "$0")/.."

uv run --frozen pytest -q -p no:cacheprovider
uvx ruff@0.16.8 check app tests
uvx ruff@0.16.8 format --check app tests
uvx pyright@1.1.414

echo "Server checks passed. After an API change also run \`yarn gen:api\` / \`yarn check:api\` in client/ (schema.d.ts vs openapi.json)."
