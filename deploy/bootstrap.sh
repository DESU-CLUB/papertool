#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/deploy/.env"
EXAMPLE_FILE="${ROOT_DIR}/deploy/.env.example"

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${EXAMPLE_FILE}" "${ENV_FILE}"
  echo "Created ${ENV_FILE}. Update secrets before production use."
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

COUCH_URL="http://${COUCHDB_USER}:${COUCHDB_PASSWORD}@127.0.0.1:5984"

echo "Ensuring CouchDB databases..."
for db in papertool_meta papertool_events papertool_jobs; do
  curl -fsS -u "${COUCHDB_USER}:${COUCHDB_PASSWORD}" -X PUT "${COUCH_URL}/${db}" >/dev/null || true
  echo "  - ${db}"
done

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl not found; skipping token generation."
else
  if [[ "${PAPERTOOL_REMOTE_API_TOKEN:-change_me_with_long_random_token}" == "change_me_with_long_random_token" ]]; then
    token="$(openssl rand -hex 24)"
    tmp_file="$(mktemp)"
    sed "s#^PAPERTOOL_REMOTE_API_TOKEN=.*#PAPERTOOL_REMOTE_API_TOKEN=${token}#" "${ENV_FILE}" >"${tmp_file}"
    mv "${tmp_file}" "${ENV_FILE}"
    echo "Generated PAPERTOOL_REMOTE_API_TOKEN in deploy/.env"
  fi
fi

if command -v mc >/dev/null 2>&1; then
  echo "Ensuring MinIO bucket papertool-files..."
  mc alias set papertool "http://127.0.0.1:9000" "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" >/dev/null
  mc mb -p papertool/papertool-files >/dev/null 2>&1 || true
  echo "  - papertool-files"
else
  echo "mc (MinIO client) not found; skipping bucket bootstrap."
fi

echo "Bootstrap complete."
