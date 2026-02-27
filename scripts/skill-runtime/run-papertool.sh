#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "run-papertool: uv is required. Install from https://docs.astral.sh/uv/." >&2
  exit 1
fi

repo_root="/Users/warrenlow/Documents/projects/papertool"
exec uv run --project "$repo_root" papertool "$@"
