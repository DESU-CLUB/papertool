# PaperTool

PaperTool is a local-first learning system for research papers with:
- Paper ingestion from a folder
- Search and evidence-grounded Q&A
- Citation graph extraction/view export
- Obsidian note syncing
- Daily quiz generation weighted toward newer papers
- MCP server for Codex / Claude Code integration
- URL importing (arXiv/PDF/GitHub/X/web pages)
- Local bridge API for browser extension capture
- Reading queue + daily planner (inbox/today/next/later/done)
- Paper-of-the-day + post-read micro-quiz + spaced review

## What this MVP supports

1. Ask questions through MCP (`ask_papers`) or CLI (`papertool ask`).
2. Build a citation graph from DOI/arXiv links found in references.
3. Save paper summaries and Q&A notes into Obsidian vault Markdown files.
4. Generate daily quiz questions with stronger weighting for recently ingested papers.
5. Recycle previously incorrect quiz prompts in the next batches at an 8:2 new-to-old mix (when enough old prompts exist).
6. Import URLs directly into your library from CLI, MCP, or a browser extension.
7. Plan a focused daily reading list and run a short post-read quiz loop.

## Install

```bash
uv venv --allow-existing .venv --python python3
source .venv/bin/activate
uv pip install -e '.[dev]'
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
  --obsidian-vault /absolute/path/to/your/ObsidianVault \
  --retrieval-backend shadow \
  --rust-index-dir ./.papertool/index/v1 \
  --cluster-mode on_demand
```

This writes `papertool.toml`.

Key config flags:
- `retrieval_backend = "python" | "shadow" | "rust"`
- `rust_index_dir = "/absolute/or/relative/path"`
- `cluster_mode = "on_demand"`

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
```

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
```

Run local bridge server (for extension/app integrations):

```bash
papertool bridge --host 127.0.0.1 --port 17345
```

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
- `ask_papers(question, top_k=6, save_to_obsidian=true, topic=null, community_id=null)`
- `get_daily_quiz(count=5)`
- `submit_quiz_answer(question_id, user_answer, score=null)`
- `citation_graph()`
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

## Obsidian behavior

When `obsidian_vault` is configured:
- Paper notes are written under `Papers/` (configurable)
- Q&A entries append to each paper note
- Q&A also appends to a daily note under `Daily/YYYY-MM-DD.md` (configurable)

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

## Chrome extension integration

A starter Chrome extension is included at `chrome-extension/` that sends the current tab URL to your local bridge server.

1. Start bridge server: `papertool bridge`.
2. Open `chrome://extensions`.
3. Enable Developer Mode.
4. Click \"Load unpacked\" and choose `chrome-extension/`.
5. Open arXiv, Google Search, or Google Scholar; inline `Save to PaperTool` buttons appear beside paper-like result titles.
6. (Optional) Use extension popup to capture any current tab URL.

The resource is downloaded/converted into `library/captures/` and ingested automatically.

## Notes and limitations

- Citation linking currently uses DOI/arXiv identifiers found in reference sections.
- Q&A answering is retrieval-backed and extractive by default (no external LLM call).
- PDF extraction quality depends on text layer quality in PDFs.
- Quiz answers with scores automatically update spaced-review cards (low score resets interval, high score expands interval).

## Run tests

```bash
pytest
```
