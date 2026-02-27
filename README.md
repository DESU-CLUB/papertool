# PaperTool

PaperTool is a local-first learning system for research papers with:
- Paper ingestion from a folder
- Search and evidence-grounded Q&A
- Citation graph extraction/view export
- Daily quiz generation weighted toward newer papers
- MCP server for Codex / Claude Code integration
- URL importing (arXiv/PDF/GitHub/X/web pages)
- Local bridge API for browser extension capture
- Reading queue + daily planner (inbox/today/next/later/done)
- Paper-of-the-day + post-read micro-quiz + spaced review
- Daily streaks + Bronze/Silver/Gold medals + local HTML dashboard
- Lightweight resource bookmarks (X/blog/web), topic tags, and paper links

## What this MVP supports

1. Ask questions through MCP (`ask_papers`) or CLI (`papertool ask`).
2. Build a citation graph from local IDs plus conservative title fallback.
3. Generate daily quiz questions with stronger weighting for recently ingested papers.
4. Recycle previously incorrect quiz prompts in the next batches at an 8:2 new-to-old mix (when enough old prompts exist).
5. Import URLs directly into your library from CLI, MCP, or a browser extension.
6. Plan a focused daily reading list and run a short post-read quiz loop.
7. Track learning streaks and per-paper medals with reversible Silver state.

## Install

```bash
uv venv --allow-existing .venv --python python3
source .venv/bin/activate
uv pip install -e '.[dev]'
```

## Claude + Codex Skill Pack

This repo now includes a local Claude plugin and canonical skills under `skills/`.

Plugin manifest:
- `.claude-plugin/plugin.json`

Skill source of truth:
- `skills/papertool/SKILL.md`
- `skills/obsidian-papertool/SKILL.md`
- `skills/manim-slides/SKILL.md`

Manim subskill references:
- `skills/manim-slides/references/reverse-knowledge-tree.md`
- `skills/manim-slides/references/manim-code-patterns.md`
- `skills/manim-slides/references/benchmark-motifs.md`
- `skills/manim-slides/references/visual-planner.md`
- `skills/manim-slides/references/verbose-prompt-format.md`
- `skills/manim-slides/references/manim-slides-api-cheatsheet.md`

Manim phase contract (strict order):
1. reverse-knowledge-tree
2. manim-code-patterns
3. visual-planner
4. verbose-prompt-builder
5. code synthesis
6. hard-gated renderability checks

Manim topic cache path:
- `.manim-slides/<topic-slug>/`

Required cache artifacts:
- `knowledge_tree.json`
- `concept_plan.json`
- `visual_plan.json`
- `verbose_prompt.md`
- `slides.py`
- `render_report.json`

Install plugin in Claude Code:

```text
/plugin install /Users/warrenlow/Documents/projects/papertool
```

Sync skill targets:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/sync-skill-targets.sh
```

Optional mirrors:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/sync-skill-targets.sh --mirror-codex-home --mirror-claude-home
```

## Run It (Quickstart)

```bash
# 1) Activate and install
source .venv/bin/activate
uv pip install -e '.[dev]'

# 2) Initialize config
papertool init --library-dir ./library --db-path ./.papertool/papertool.db

# 3) Import at least one resource
papertool import-url "https://arxiv.org/abs/2205.14135"

# 4) Plan today and start reading flow
papertool today --count 3
papertool paper-of-day --quiz

# 5) After reading, mark done and answer quiz
papertool complete-reading --paper-id <paper_id> --quiz-count 3
papertool submit-answer --question-id <question_id> --answer "..." --score 0.7
papertool review-due --count 5
```

If you want agent integration, run:

```bash
papertool mcp-serve
```

If you want browser capture, run:

```bash
papertool bridge --host 127.0.0.1 --port 17345
```

## Configure

Create config:

```bash
papertool init \
  --library-dir ./library \
  --db-path ./.papertool/papertool.db \
  --retrieval-backend shadow \
  --rust-index-dir ./.papertool/index/v1 \
  --cluster-mode on_demand
```

This writes `papertool.toml`.

Key config flags:
- `retrieval_backend = "python" | "shadow" | "rust"`
- `rust_index_dir = "/absolute/or/relative/path"`
- `cluster_mode = "on_demand"`
- `storage_backend = "sqlite" | "hybrid" | "couch"`
- `couchdb_url`, `couchdb_db_meta`, `couchdb_db_events`, `couchdb_db_jobs`
- `remote_api_base_url`, `remote_api_token`
- `minio_endpoint`, `minio_bucket`, `minio_access_key`, `minio_secret_key`
- `sync_enabled`, `sync_pull_interval_sec`, `sync_push_interval_sec`
- `daily_goal`, `goal_timezone`
- `ask_confirmation_mode` (`session|always|never`), `ask_session_ttl_sec`, `ask_cli_auto_session`
- `citation_refresh_on_import`
- `citation_title_match_mode` (`conservative|balanced|aggressive`)

## Usage

Ingest papers:

```bash
papertool ingest
```

List papers:

```bash
papertool list
```

Ask question:

```bash
papertool ask "What are the key differences between diffusion and autoregressive models?"
papertool ask "How does MoE routing work?" --topic moe
papertool ask "Summarize FlashAttention" --confirm-mode always
papertool ask "Summarize FlashAttention-2" --session-id study-fa
```

Session confirmation behavior:
- `session` (default): first ask for a scope requires confirmation; repeated asks with identical paper scope auto-log.
- `always`: always prompt before logging.
- `never`: skip confirmation and log immediately.

Search passages directly:

```bash
papertool search "flash attention io aware" --top-k 8
papertool search "state space" --community comm:0
```

Build retrieval index and clusters:

```bash
papertool index build
papertool index refresh --paper-id <paper_id>
papertool cluster build
papertool cluster list --type topic
papertool cluster papers --topic attention
```

Rebuild and inspect citation links:

```bash
papertool citations rebuild
papertool citations rebuild --paper-id <paper_id>
papertool citations status
papertool citations inspect --paper-id <paper_id>
```

Generate quiz:

```bash
papertool quiz --count 5
```

Plan your day and get one paper prompt:

```bash
papertool today --count 3
papertool paper-of-day
papertool paper-of-day --quiz
```

Mark a paper complete and generate a micro-quiz:

```bash
papertool complete-reading --paper-id <paper_id> --quiz-count 3
papertool submit-answer --question-id <question_id> --answer \"...\" --score 0.6
papertool review-due --count 5
```

Set daily goal and view streak status:

```bash
papertool goal set --daily 2 --timezone America/Los_Angeles
papertool goal status
```

Manage medals, repo links, and dashboard:

```bash
papertool medals status --limit 100
papertool medals link-repo --paper-id <paper_id> --url "https://github.com/DESU-CLUB/your-repo"
papertool medals recompute --from 2026-02-01
papertool medals dashboard --output ./.papertool/medals.html
```

Manage queue status:

```bash
papertool queue list --status inbox
papertool queue set --paper-id <paper_id> --status next --priority 2.0
```

Import any URL:

```bash
papertool import-url "https://arxiv.org/abs/2205.14135"
papertool import-url "https://github.com/Dao-AILab/flash-attention"
papertool import-url "https://x.com/user/status/1234567890"
papertool import-url "https://x.com/user/status/1234567890" --topics "attention,systems" --link-paper-id <paper_id>
papertool import-url "https://myblog.com/post" --kind blog --topics "mamba,architecture"
```

Manage resource bookmarks:

```bash
papertool resource list --kind x_post --limit 50
papertool resource show --resource-id <resource_id>
papertool resource tag --resource-id <resource_id> --topics "attention,systems"
papertool resource link --resource-id <resource_id> --paper-id <paper_id> --type related
papertool resource links --paper-id <paper_id>
papertool paper-of-day --show-resources
```

Run local bridge server (for extension/app integrations):

```bash
papertool bridge --host 127.0.0.1 --port 17345
```

Run remote API and worker (for distributed tailnet captures/sync):

```bash
papertool remote serve --host 0.0.0.0 --port 18443
papertool remote worker --poll-interval-sec 5
papertool sync daemon --pull-interval-sec 30
papertool remote health
papertool sync run
papertool sync status
```

Migration helpers:

```bash
papertool migrate export-sqlite --output ./.papertool/migration-export.json
papertool migrate import-couch --input ./.papertool/migration-export.json
papertool migrate verify
```

For a Docker-based distributed deployment (CouchDB + MinIO + API + worker), see:
- `deploy/docker-compose.yml`
- `deploy/README.md` (includes full `spooky@ghost` setup runbook)

Export graph:

```bash
papertool graph export --format json --output ./.papertool/graph.json
papertool graph export --format mermaid --output ./.papertool/graph.mmd
papertool graph export --format html --output ./.papertool/graph.html
```

## MCP Server

Run:

```bash
papertool mcp-serve
```

Available MCP tools:
- `list_papers(limit=100)`
- `search_papers(query, top_k=6, topic=null, community_id=null)`
- `ask_papers_prepare(question, top_k=6, paper_ids=null, arxiv_ids=null, topic=null, community_id=null, session_id=null, confirm_mode=null)`
- `ask_papers_confirm(pending_id, approve, final_answer=null, session_id=null, confirm_mode=null)`
- `ask_papers(question, top_k=6, final_answer=null, topic=null, community_id=null, paper_ids=null, arxiv_ids=null, session_id=null, confirm_mode=null)`
- `ask_scope_lock_status(session_id, channel="mcp")`
- `get_daily_quiz(count=5)`
- `submit_quiz_answer(question_id, user_answer, score=null)`
- `citation_graph()`
- `rebuild_citations(paper_id=null)`
- `citation_status()`
- `paper_citations(paper_id)`
- `import_resource(url, title=null, context_text=null)`
- `import_resources(urls)`
- `build_retrieval_index(paper_id=null)`
- `build_clusters_index()`
- `clusters_overview(type=\"topic\"|\"community\", limit=50)`
- `cluster_papers(topic=null, community_id=null, limit=100)`
- `queue_overview(status=null, limit=50)`
- `queue_set(paper_id, status, priority=null)`
- `plan_today(max_items=3)`
- `paper_of_day(include_quiz=false, quiz_count=3)`
- `complete_reading(paper_id, quiz_count=3)`
- `due_reviews(count=5)`
- `set_daily_goal(daily_goal, timezone="America/Los_Angeles")`
- `goal_status()`
- `link_paper_repo(paper_id, url)`
- `paper_medals(paper_id)`
- `medals_overview(limit=100)`
- `build_medals_dashboard(output_path=null)`
- `recompute_medals(from_day=null)`
- `add_resource(url, title=null, notes=null, topics=[], paper_id=null, kind=null)`
- `list_resources(kind=null, topic=null, limit=100)`
- `resource_details(resource_id)`
- `tag_resource(resource_id, topics)`
- `link_resource(resource_id, paper_id, link_type="related")`
- `paper_resources(paper_id, limit=20)`

### Example MCP config (Claude Code / Codex)

Use your client's MCP config format and point command to the venv binary, for example:

```json
{
  "mcpServers": {
    "papertool": {
      "command": "/absolute/path/to/.venv/bin/papertool",
      "args": ["mcp-serve"],
      "cwd": "/absolute/path/to/papertool"
    }
  }
}
```

## External notes workflow

PaperTool no longer writes directly to Obsidian.  
If you want vault writes, use a Codex skill workflow (for example `vault-writer`) that:
- resolves the target vault/path from your prompt
- appends your final answer markdown to the target note
- keeps retrieval logs/internal snippets out of notes unless you explicitly ask for them

## Data model

SQLite DB tables:
- `papers` (metadata + extracted full text)
- `chunks` + `chunk_fts` (FTS5 retrieval index)
- `citations` (directed edges between known papers)
- `qa_log` (question/answer history)
- `quiz_history` (quiz prompts + responses)
- `reading_queue` (inbox/today/next/later/done planning state)
- `review_cards` (spaced-review schedule and intervals)
- `retrieval_shadow_log` (Python vs Rust shadow comparisons)
- `topic_catalog` + `paper_topic_scores` (overlapping topic clusters)
- `citation_communities` (citation graph communities)
- `cluster_runs` (cluster build run history)
- `goal_settings` (daily goal + timezone)
- `daily_progress` + `daily_qualified_papers` (goal and streak state by day)
- `paper_medals` + `paper_repo_links` + `medal_events` (Bronze/Silver/Gold and audit)
- `resources` + `resource_topics` + `paper_resource_links` (metadata-only URL enrichment and linking)

## Chrome extension integration

A starter Chrome extension is included at `chrome-extension/` that sends the current tab URL to your local bridge server or remote API.

1. Start bridge server: `papertool bridge`.
2. Open `chrome://extensions`.
3. Enable Developer Mode.
4. Click \"Load unpacked\" and choose `chrome-extension/`.
5. Open arXiv, Google Search, or Google Scholar; inline `Save to PaperTool` buttons appear beside paper-like result titles.
6. (Optional) Use extension popup to capture any current tab URL.
7. For distributed mode, set popup endpoint to your Tailscale host, e.g. `http://ghost:18443`, and set Bearer token.

Upload reliability:
- Queue is durable in `chrome.storage.local`.
- Retry policy is exponential backoff with jitter: 30s -> 60s -> 120s -> 240s -> 480s -> 900s -> 1800s.
- Retries happen on network errors, `429`, and `5xx`.
- Other `4xx` are marked terminal failures and surfaced in popup queue diagnostics.

The resource is downloaded/converted into `library/captures/` and ingested automatically.

## Notes and limitations

- Citation linking currently uses DOI/arXiv identifiers found in reference sections.
- Q&A answering is retrieval-backed and extractive by default (no external LLM call).
- PDF extraction quality depends on text layer quality in PDFs.
- Quiz answers with scores automatically update spaced-review cards (low score resets interval, high score expands interval). Score accepts `0-1` or `0-10` and normalizes to `0-1`.

## Run tests

```bash
pytest
pytest -m integration -k spooky_access
```

Integration note:
- `tests/integration/test_spooky_access.py` runs `ssh spooky@ghost "curl .../v1/health"`.
- If `spooky@ghost` is unreachable or requires interactive Tailscale SSH auth, it is skipped.
