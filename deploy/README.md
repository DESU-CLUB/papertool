# PaperTool Main Server Setup (`spooky@ghost`)

This sets up your shared PaperTool backend on your tailnet:
- CouchDB (`papertool_meta`, `papertool_events`, `papertool_jobs`)
- MinIO (`papertool-files`)
- PaperTool API (`/v1/*`)
- PaperTool worker (processes queued captures)

## 1) Prerequisites on `spooky@ghost`

Install:
- Docker + Docker Compose plugin
- Tailscale (connected to your tailnet)

Verify:

```bash
docker --version
docker compose version
tailscale status
tailscale ip -4
```

## 2) Clone and enter repo on server

```bash
ssh spooky@ghost
cd /path/to
git clone <your-papertool-repo-url> papertool
cd papertool
```

## 3) Configure deployment env

```bash
cp deploy/.env.example deploy/.env
```

Edit `deploy/.env`:
- `TAILSCALE_BIND_IP=<your spooky@ghost tailscale IPv4>` (from `tailscale ip -4`)
- strong values for:
  - `COUCHDB_PASSWORD`
  - `MINIO_ROOT_PASSWORD`
  - `PAPERTOOL_REMOTE_API_TOKEN`

Example:

```dotenv
TAILSCALE_BIND_IP=100.101.102.103
COUCHDB_USER=papertool
COUCHDB_PASSWORD=<strong-secret>
MINIO_ROOT_USER=papertool
MINIO_ROOT_PASSWORD=<strong-secret>
PAPERTOOL_REMOTE_API_TOKEN=<long-random-token>
```

## 4) Start services

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d
```

## 5) Bootstrap databases/token/bucket

```bash
./deploy/bootstrap.sh
```

What bootstrap does:
- ensures CouchDB DBs exist
- auto-generates API token if still placeholder
- creates MinIO bucket (`papertool-files`) if `mc` is installed

## 6) Verify server health

From `spooky@ghost`:

```bash
source deploy/.env
curl -fsS -H "Authorization: Bearer ${PAPERTOOL_REMOTE_API_TOKEN}" \
  "http://127.0.0.1:18443/v1/health"
```

From another tailnet device:

```bash
curl -fsS -H "Authorization: Bearer <token>" \
  "http://<spooky-tailscale-ip>:18443/v1/health"
```

Expected JSON includes `"ok": true`.

## 7) Configure PaperTool clients

On each client machine:

```bash
papertool init \
  --storage-backend hybrid \
  --couchdb-url "http://papertool:<COUCHDB_PASSWORD>@<spooky-tailscale-ip>:5984" \
  --couchdb-db-meta papertool_meta \
  --couchdb-db-events papertool_events \
  --couchdb-db-jobs papertool_jobs \
  --remote-api-base-url "http://<spooky-tailscale-ip>:18443" \
  --remote-api-token "<token>" \
  --minio-endpoint "http://<spooky-tailscale-ip>:9000" \
  --minio-bucket papertool-files \
  --minio-access-key papertool \
  --minio-secret-key "<MINIO_ROOT_PASSWORD>" \
  --sync-enabled
```

Then run:

```bash
papertool sync run
papertool sync status
papertool remote health
```

## 8) Configure browser extension

In extension popup:
- Endpoint: `http://<spooky-tailscale-ip>:18443`
- Bearer token: same `PAPERTOOL_REMOTE_API_TOKEN`

Extension behavior:
- queues captures durably in `chrome.storage.local`
- retries failed uploads with exponential backoff (30s to 30m with jitter)

## 9) Optional integration test for `spooky@ghost`

From a client with repo checked out:

```bash
export PAPERTOOL_REMOTE_API_TOKEN="<token>"
pytest -m integration -k spooky_access
```

The test SSHes to `spooky@ghost` and calls `http://127.0.0.1:18443/v1/health`.

## Operations

Update stack:

```bash
git pull
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

View logs:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs -f papertool-api papertool-worker
```

Stop:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml down
```
