#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
# shellcheck source=/dev/null
source "$repo_root/scripts/vibecheck-lib.sh"

if ! vc_config_enabled; then
  exit 0
fi

vc_require_jq
vc_require_hmac_key

commits=()
declare -A seen

while read -r local_ref local_sha remote_ref remote_sha; do
  [[ -z "${local_sha:-}" ]] && continue

  if [[ "$local_sha" =~ ^0+$ ]]; then
    continue
  fi

  range_commits=()
  if [[ "$remote_sha" =~ ^0+$ ]]; then
    while IFS= read -r sha; do
      [[ -z "$sha" ]] && continue
      range_commits+=("$sha")
    done < <(git rev-list "$local_sha" --not --all)
  else
    while IFS= read -r sha; do
      [[ -z "$sha" ]] && continue
      range_commits+=("$sha")
    done < <(git rev-list "$remote_sha..$local_sha")
  fi

  for sha in "${range_commits[@]}"; do
    if [[ -z "${seen[$sha]:-}" ]]; then
      seen["$sha"]=1
      commits+=("$sha")
    fi
  done
done

if (( ${#commits[@]} == 0 )); then
  exit 0
fi

for sha in "${commits[@]}"; do
  if ! vc_verify_receipt_for_commit "$sha"; then
    vc_fail "Push blocked because commit $sha has no valid VibeCommit receipt."
  fi
done

push_context="$(vc_push_context_from_commits "${commits[@]}")"
if ! vc_run_quiz_interactively "push" "$push_context"; then
  vc_fail "Push blocked. You must answer all 3 push quiz questions correctly."
fi

vc_log "Push quiz passed and receipts verified."
