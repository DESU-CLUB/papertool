#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_root="$repo_root/skills"
agents_root="$repo_root/.agents/skills"
codex_home_default="$HOME/.codex/skills"
claude_home_default="$HOME/.claude/skills"

mirror_codex=0
mirror_claude=0
check_only=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [--check] [--mirror-codex-home] [--mirror-claude-home]

Sync canonical skills from $source_root to:
  - $agents_root (always)
  - ~/.codex/skills (optional)
  - ~/.claude/skills (optional)

Options:
  --check               Verify parity only; do not copy
  --mirror-codex-home   Also mirror to ~/.codex/skills
  --mirror-claude-home  Also mirror to ~/.claude/skills
  -h, --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      check_only=1
      shift
      ;;
    --mirror-codex-home)
      mirror_codex=1
      shift
      ;;
    --mirror-claude-home)
      mirror_claude=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ ! -d "$source_root" ]]; then
  echo "sync-skill-targets: source directory not found: $source_root" >&2
  exit 1
fi
if [[ ! -f "$repo_root/.claude-plugin/plugin.json" ]]; then
  echo "sync-skill-targets: missing Claude plugin manifest: $repo_root/.claude-plugin/plugin.json" >&2
  exit 1
fi

mapfile -t skill_dirs < <(find "$source_root" -mindepth 1 -maxdepth 1 -type d | sort)
if [[ ${#skill_dirs[@]} -eq 0 ]]; then
  echo "sync-skill-targets: no skills found under $source_root" >&2
  exit 1
fi

for d in "${skill_dirs[@]}"; do
  if [[ ! -f "$d/SKILL.md" ]]; then
    echo "sync-skill-targets: missing SKILL.md in $d" >&2
    exit 1
  fi
done

declare -a targets

targets+=("$agents_root")
if [[ $mirror_codex -eq 1 ]]; then
  targets+=("$codex_home_default")
fi
if [[ $mirror_claude -eq 1 ]]; then
  targets+=("$claude_home_default")
fi

sync_file() {
  local src="$1"
  local dst="$2"

  if [[ $check_only -eq 1 ]]; then
    if [[ ! -f "$dst" ]]; then
      echo "MISSING $dst"
      return 1
    fi
    if ! cmp -s "$src" "$dst"; then
      echo "DIFF $dst"
      return 1
    fi
    return 0
  fi

  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  return 0
}

ok=0
for target_root in "${targets[@]}"; do
  for skill_dir in "${skill_dirs[@]}"; do
    skill_name="$(basename "$skill_dir")"
    src="$skill_dir/SKILL.md"
    dst="$target_root/$skill_name/SKILL.md"
    if sync_file "$src" "$dst"; then
      if [[ $check_only -eq 0 ]]; then
        echo "SYNCED $dst"
      fi
    else
      ok=1
    fi
  done
done

if [[ $check_only -eq 1 ]]; then
  if [[ $ok -ne 0 ]]; then
    echo "sync-skill-targets: parity check failed" >&2
    exit 1
  fi
  echo "sync-skill-targets: parity check passed"
fi
