---
name: papertool
description: Operate a local PaperTool system for research reading workflows, including import, queueing, ask, quiz/review loops, citations, and medals.
---

# PaperTool Skill

Use this skill when the user asks to study papers, ingest resources, run quizzes/reviews, or manage reading workflow state.

## Path Discovery (Required First)

1. Locate PaperTool repo root (look for `papertool/cli.py` and `pyproject.toml`).
2. From repo root, use wrapper path: `scripts/skill-runtime/run-papertool.sh`.
3. If wrapper is missing, use uv fallback from repo root.

Suggested discovery commands:

```bash
pwd
rg --files | rg '^papertool/cli.py$|^scripts/skill-runtime/run-papertool.sh$|^pyproject.toml$'
find ~ -type f -path '*/papertool/cli.py' 2>/dev/null | head
```

## Update Local Skill Paths

After discovery, update your local/private skill copy if your environment differs from repo-relative defaults.
Keep this Git-tracked skill generic.

## Runtime (No Manual Venv)

Preferred wrapper:

```bash
scripts/skill-runtime/run-papertool.sh <papertool args>
```

Fallback:

```bash
uv run --project . papertool <args>
```

## Core Commands

Initialize config:

```bash
scripts/skill-runtime/run-papertool.sh init --library-dir ./library --db-path ./.papertool/papertool.db
```

Ingest local files:

```bash
scripts/skill-runtime/run-papertool.sh ingest
```

Import resource by URL:

```bash
scripts/skill-runtime/run-papertool.sh import-url "<url>"
```

Query library:

```bash
scripts/skill-runtime/run-papertool.sh ask "<question>"
```

Non-interactive ask requires explicit confirmation choice:

```bash
scripts/skill-runtime/run-papertool.sh ask --confirm yes "<question>"
scripts/skill-runtime/run-papertool.sh ask --confirm no "<question>"
```

Scope ask to specific paper(s):

```bash
scripts/skill-runtime/run-papertool.sh ask --paper-id <paper_id_or_prefix> --confirm yes "<question>"
```

## Daily Reading Workflow

```bash
scripts/skill-runtime/run-papertool.sh today --count 3
scripts/skill-runtime/run-papertool.sh paper-of-day --quiz
scripts/skill-runtime/run-papertool.sh complete-reading --paper-id <paper_id> --quiz-count 3
scripts/skill-runtime/run-papertool.sh submit-answer --question-id <question_id> --answer "<answer>" --score 0.7
scripts/skill-runtime/run-papertool.sh review-due --count 5
```

## Citations

```bash
scripts/skill-runtime/run-papertool.sh citations rebuild
scripts/skill-runtime/run-papertool.sh citations status
scripts/skill-runtime/run-papertool.sh citations inspect --paper-id <paper_id>
```

## Goals and Medals

```bash
scripts/skill-runtime/run-papertool.sh goal set --daily 2 --timezone America/Los_Angeles
scripts/skill-runtime/run-papertool.sh goal status
scripts/skill-runtime/run-papertool.sh medals status --limit 100
scripts/skill-runtime/run-papertool.sh medals dashboard --output ./.papertool/medals.html
```

## Notes Workflow

For study-heavy interactions, pair with `obsidian-papertool` skill.
Use `Summary`, `Notes`, and `Q&A` structure and avoid raw retrieval chunks in final notes.
