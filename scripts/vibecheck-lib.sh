#!/usr/bin/env bash
set -euo pipefail

VC_RECEIPT_PREFIX="VibeCommit-Receipt:"
VC_AGENT_TOKEN_TTL_SECONDS=900
VC_DEFAULT_RECEIPT_MAX_AGE_HOURS=168

vc_repo_root() {
  git rev-parse --show-toplevel
}

vc_git_dir() {
  git rev-parse --git-dir
}

vc_state_dir() {
  printf "%s/vibecommit" "$(vc_git_dir)"
}

vc_agent_token_file() {
  printf "%s/agent-token" "$(vc_state_dir)"
}

vc_config_file() {
  printf "%s/vibecommit.toml" "$(vc_repo_root)"
}

vc_log() {
  printf "[vibecheck] %s\n" "$*" >&2
}

vc_fail() {
  vc_log "$*"
  exit 1
}

vc_trim() {
  local value="$1"
  value="${value#${value%%[![:space:]]*}}"
  value="${value%${value##*[![:space:]]}}"
  printf "%s" "$value"
}

vc_config_enabled() {
  local config
  config="$(vc_config_file)"
  if [[ ! -f "$config" ]]; then
    return 1
  fi

  local enabled
  enabled="$(
    awk -F'=' '
      {
        sub(/#.*/, "", $0)
      }
      /^[[:space:]]*enabled[[:space:]]*=/ {
        value=$2
        sub(/^[[:space:]]*/, "", value)
        sub(/[[:space:]]*$/, "", value)
        gsub(/^"|"$/, "", value)
        print value
        exit
      }
    ' "$config" 2>/dev/null || true
  )"

  if [[ -z "$enabled" ]]; then
    return 0
  fi

  enabled="$(printf "%s" "$enabled" | tr '[:upper:]' '[:lower:]')"
  case "$enabled" in
    false|0|no|off)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

vc_require_hmac_key() {
  if [[ -z "${VIBECOMMIT_HMAC_KEY:-}" ]]; then
    vc_fail "Missing VIBECOMMIT_HMAC_KEY. Configure the shared secret to sign/verify receipts."
  fi
}

vc_require_claude() {
  if ! command -v claude >/dev/null 2>&1; then
    vc_fail "Claude CLI is required. Install Claude Code and ensure 'claude' is on PATH."
  fi
}

vc_require_jq() {
  if ! command -v jq >/dev/null 2>&1; then
    vc_fail "jq is required for VibeCommit hooks."
  fi
}

vc_b64url_encode_stdin() {
  openssl base64 -A | tr '+/' '-_' | tr -d '='
}

vc_b64url_decode() {
  local value="$1"
  local pad=$(( (4 - ${#value} % 4) % 4 ))
  local padded="$value"
  while (( pad > 0 )); do
    padded+="="
    pad=$((pad - 1))
  done
  printf "%s" "$padded" | tr '_-' '/+' | openssl base64 -d -A
}

vc_hmac_b64url() {
  local payload="$1"
  printf "%s" "$payload" \
    | openssl dgst -sha256 -hmac "$VIBECOMMIT_HMAC_KEY" -binary \
    | vc_b64url_encode_stdin
}

vc_sign_payload() {
  local payload="$1"
  local payload_b64
  payload_b64="$(printf "%s" "$payload" | vc_b64url_encode_stdin)"
  local sig
  sig="$(vc_hmac_b64url "$payload")"
  printf "v1.%s.%s" "$payload_b64" "$sig"
}

vc_verify_token_signature() {
  local token="$1"
  local version payload_b64 signature

  IFS='.' read -r version payload_b64 signature <<<"$token"
  if [[ "$version" != "v1" || -z "$payload_b64" || -z "$signature" ]]; then
    return 1
  fi

  local payload
  if ! payload="$(vc_b64url_decode "$payload_b64" 2>/dev/null)"; then
    return 1
  fi

  local expected
  expected="$(vc_hmac_b64url "$payload")"
  if [[ "$expected" != "$signature" ]]; then
    return 1
  fi

  printf "%s" "$payload"
}

vc_extract_ts_from_payload() {
  local payload="$1"
  jq -er '.ts' <<<"$payload" 2>/dev/null
}

vc_now_epoch() {
  date +%s
}

vc_is_fresh_epoch() {
  local ts="$1"
  local max_age_seconds="$2"
  local now
  now="$(vc_now_epoch)"
  local age=$((now - ts))
  if (( age < 0 )); then
    return 1
  fi
  (( age <= max_age_seconds ))
}

vc_issue_agent_token() {
  vc_require_hmac_key
  mkdir -p "$(vc_state_dir)"
  chmod 700 "$(vc_state_dir)"

  local payload
  payload="$(jq -cn --argjson ts "$(vc_now_epoch)" '{v:1,kind:"agent",ts:$ts}')"

  local token
  token="$(vc_sign_payload "$payload")"

  printf "%s\n" "$token" > "$(vc_agent_token_file)"
  chmod 600 "$(vc_agent_token_file)"
  vc_log "Issued agent token at $(vc_agent_token_file)"
}

vc_consume_agent_token_if_valid() {
  local token_file
  token_file="$(vc_agent_token_file)"

  if [[ ! -f "$token_file" ]]; then
    return 1
  fi

  local token payload ts
  token="$(cat "$token_file")"

  if ! payload="$(vc_verify_token_signature "$token")"; then
    rm -f "$token_file"
    return 1
  fi

  if [[ "$(jq -r '.kind // ""' <<<"$payload")" != "agent" ]]; then
    rm -f "$token_file"
    return 1
  fi

  if ! ts="$(vc_extract_ts_from_payload "$payload")"; then
    rm -f "$token_file"
    return 1
  fi

  if ! vc_is_fresh_epoch "$ts" "$VC_AGENT_TOKEN_TTL_SECONDS"; then
    rm -f "$token_file"
    return 1
  fi

  rm -f "$token_file"
  return 0
}

vc_generate_quiz_json() {
  local stage="$1"
  local context="$2"

  vc_require_claude

  local schema
  schema='{"type":"object","properties":{"questions":{"type":"array","minItems":3,"maxItems":3,"items":{"type":"object","properties":{"question":{"type":"string"},"options":{"type":"array","minItems":4,"maxItems":4,"items":{"type":"string"}},"answer_index":{"type":"integer","minimum":1,"maximum":4},"rationale":{"type":"string"}},"required":["question","options","answer_index","rationale"],"additionalProperties":false}}},"required":["questions"],"additionalProperties":false}'

  local prompt
  prompt=$(cat <<PROMPT
You are generating a strict comprehension quiz for a git ${stage} gate.

Goal:
- Create exactly 3 multiple-choice questions to verify the user understands the code they are committing/pushing.
- Questions must be answerable from the provided context only.
- Avoid trivia; focus on behavior changes, risks, and intent.

Output:
- Return JSON matching the provided schema.
- answer_index must be 1-4.

Context:
${context}
PROMPT
)

  local output
  if ! output="$(claude -p --output-format text --json-schema "$schema" "$prompt" 2>/tmp/vibecheck-claude.err)"; then
    vc_log "Claude invocation failed. Details:"
    sed 's/^/[vibecheck] /' /tmp/vibecheck-claude.err >&2 || true
    vc_fail "Unable to generate quiz with Claude. Ensure Claude is authenticated and reachable."
  fi

  if ! jq -e '.questions | length == 3' >/dev/null 2>&1 <<<"$output"; then
    vc_fail "Claude returned invalid quiz JSON."
  fi

  printf "%s" "$output"
}

vc_read_answer_index() {
  local prompt="$1"
  local answer
  printf "%s" "$prompt" > /dev/tty
  IFS= read -r answer < /dev/tty || return 1
  answer="$(printf "%s" "$answer" | tr '[:lower:]' '[:upper:]' | tr -d '[:space:]')"
  case "$answer" in
    1|A) printf "1" ;;
    2|B) printf "2" ;;
    3|C) printf "3" ;;
    4|D) printf "4" ;;
    *) return 1 ;;
  esac
}

vc_run_quiz_interactively() {
  local stage="$1"
  local context="$2"

  if [[ ! -e /dev/tty ]]; then
    vc_fail "Interactive quiz required but no TTY is available."
  fi

  local quiz
  quiz="$(vc_generate_quiz_json "$stage" "$context")"

  local correct=0
  local idx
  printf "\n[vibecheck] %s quiz: answer all 3 correctly to continue.\n\n" "$stage" > /dev/tty

  for idx in 0 1 2; do
    local question
    question="$(jq -r ".questions[$idx].question" <<<"$quiz")"

    local option1 option2 option3 option4 expected
    option1="$(jq -r ".questions[$idx].options[0]" <<<"$quiz")"
    option2="$(jq -r ".questions[$idx].options[1]" <<<"$quiz")"
    option3="$(jq -r ".questions[$idx].options[2]" <<<"$quiz")"
    option4="$(jq -r ".questions[$idx].options[3]" <<<"$quiz")"
    expected="$(jq -r ".questions[$idx].answer_index" <<<"$quiz")"

    printf "Q%d. %s\n" "$((idx + 1))" "$question" > /dev/tty
    printf "  A) %s\n" "$option1" > /dev/tty
    printf "  B) %s\n" "$option2" > /dev/tty
    printf "  C) %s\n" "$option3" > /dev/tty
    printf "  D) %s\n" "$option4" > /dev/tty

    local answer
    if ! answer="$(vc_read_answer_index "Your answer (A-D or 1-4): ")"; then
      vc_log "Invalid answer format."
      return 1
    fi

    if [[ "$answer" == "$expected" ]]; then
      correct=$((correct + 1))
      printf "Correct.\n\n" > /dev/tty
    else
      printf "Incorrect.\n\n" > /dev/tty
    fi
  done

  if (( correct != 3 )); then
    vc_log "Quiz failed ($correct/3)."
    return 1
  fi

  vc_log "Quiz passed (3/3)."
  return 0
}

vc_append_receipt_to_message_file() {
  local message_file="$1"
  vc_require_hmac_key

  local payload
  payload="$(jq -cn --argjson ts "$(vc_now_epoch)" '{v:1,kind:"receipt",ts:$ts}')"

  local token
  token="$(vc_sign_payload "$payload")"

  local temp_file
  temp_file="$(mktemp)"

  grep -v "^${VC_RECEIPT_PREFIX}[[:space:]]*" "$message_file" > "$temp_file" || true
  {
    cat "$temp_file"
    printf "\n%s %s\n" "$VC_RECEIPT_PREFIX" "$token"
  } > "$message_file"

  rm -f "$temp_file"
}

vc_extract_receipt_from_commit() {
  local commit_sha="$1"
  git show -s --format=%B "$commit_sha" | sed -n "s/^${VC_RECEIPT_PREFIX}[[:space:]]*//p" | tail -n1
}

vc_verify_receipt_for_commit() {
  local commit_sha="$1"
  local max_age_hours="${2:-$VC_DEFAULT_RECEIPT_MAX_AGE_HOURS}"

  vc_require_hmac_key

  local token
  token="$(vc_extract_receipt_from_commit "$commit_sha")"
  if [[ -z "$token" ]]; then
    vc_log "Commit $commit_sha is missing a VibeCommit receipt."
    return 1
  fi

  local payload
  if ! payload="$(vc_verify_token_signature "$token")"; then
    vc_log "Commit $commit_sha has an invalid VibeCommit receipt signature."
    return 1
  fi

  if [[ "$(jq -r '.kind // ""' <<<"$payload")" != "receipt" ]]; then
    vc_log "Commit $commit_sha has a malformed VibeCommit payload."
    return 1
  fi

  local ts max_age_seconds
  if ! ts="$(vc_extract_ts_from_payload "$payload")"; then
    vc_log "Commit $commit_sha has no receipt timestamp."
    return 1
  fi
  max_age_seconds=$((max_age_hours * 3600))

  if ! vc_is_fresh_epoch "$ts" "$max_age_seconds"; then
    vc_log "Commit $commit_sha receipt is expired."
    return 1
  fi

  return 0
}

vc_commit_context() {
  local message_file="$1"
  local commit_message staged_diff

  commit_message="$(cat "$message_file")"
  staged_diff="$(git diff --cached --unified=0 --no-color)"

  printf "Commit message:\n%s\n\nStaged diff:\n%s\n" \
    "$commit_message" \
    "$(printf "%s" "$staged_diff" | head -c 12000)"
}

vc_push_context_from_commits() {
  local commits=("$@")
  local sha

  for sha in "${commits[@]}"; do
    printf "---\n"
    git show --stat --summary --no-color --format='commit %H%nAuthor: %an <%ae>%nDate: %ad%nSubject: %s%n%n%b' "$sha" | head -c 4000
    printf "\n"
  done
}
