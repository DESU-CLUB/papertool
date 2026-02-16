#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
# shellcheck source=/dev/null
source "$repo_root/scripts/vibecheck-lib.sh"

if ! vc_config_enabled; then
  exit 0
fi

vc_require_jq
vc_issue_agent_token
