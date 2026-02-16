#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: scripts/vibecheck-commit.sh <commit-message-file>" >&2
  exit 2
fi

message_file="$1"
repo_root="$(git rev-parse --show-toplevel)"
# shellcheck source=/dev/null
source "$repo_root/scripts/vibecheck-lib.sh"

if ! vc_config_enabled; then
  exit 0
fi

vc_require_jq
vc_require_hmac_key

if vc_consume_agent_token_if_valid; then
  vc_log "Valid agent token found; skipping manual quiz."
  vc_append_receipt_to_message_file "$message_file"
  exit 0
fi

context="$(vc_commit_context "$message_file")"
if ! vc_run_quiz_interactively "commit" "$context"; then
  vc_fail "Commit blocked. You must answer all 3 quiz questions correctly."
fi

vc_append_receipt_to_message_file "$message_file"
vc_log "Commit receipt attached."
