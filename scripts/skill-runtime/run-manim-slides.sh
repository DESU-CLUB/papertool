#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "run-manim-slides: uv is required. Install from https://docs.astral.sh/uv/." >&2
  exit 1
fi

missing=()
for cmd in ffmpeg; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    missing+=("$cmd")
  fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "run-manim-slides: missing system dependency: ${missing[*]}" >&2
  echo "Install required system deps (ffmpeg/cairo/pango/latex) before rendering complex slides." >&2
  exit 1
fi

if [[ ${1:-} == "python" ]]; then
  shift
  exec uv run --with "manim-slides[manim]" python "$@"
fi

exec uvx --from "manim-slides[manim]" manim-slides "$@"
