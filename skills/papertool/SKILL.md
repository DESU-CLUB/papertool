---
name: papertool
description: Operate the local PaperTool system for research reading workflows, including importing paper/resource URLs, planning daily reading, running paper-of-the-day prompts, managing queue states (inbox/today/next/later/done), generating quizzes, and submitting scored answers for spaced review. Use when the user asks to study papers, organize reading tasks, run quiz/review loops, or ingest paper-related resources into the PaperTool library.
---

# PaperTool Skill

Use the PaperTool project at `/Users/warrenlow/Documents/projects/papertool`.

## Runtime (No Manual Venv)

Use wrapper commands first:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh <papertool args>
```

Direct fallback:

```bash
uv run --project /Users/warrenlow/Documents/projects/papertool papertool <args>
```

## Core Commands

Initialize config:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh init --library-dir ./library --db-path ./.papertool/papertool.db
```

Ingest local files:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh ingest
```

Import resources by URL:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh import-url "<url>"
```

Query library:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh ask "<question>"
```

Non-interactive ask requires explicit confirmation choice:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh ask --confirm yes "<question>"
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh ask --confirm no "<question>"
```

Scope ask to specific papers:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh ask --paper-id 9047bb47 --confirm yes "<question>"
```

## Daily Reading Workflow

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh today --count 3
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh paper-of-day --quiz
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh complete-reading --paper-id <paper_id> --quiz-count 3
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh submit-answer --question-id <question_id> --answer "<answer>" --score 0.7
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh review-due --count 5
```

## Citations

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh citations rebuild
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh citations inspect --paper-id <paper_id>
```

## Goals and Medals

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh goal set --daily 2 --timezone America/Los_Angeles
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh medals dashboard --output ./.papertool/medals.html
```

## Notes Workflow

For study-heavy interactions, default to saving notes in Obsidian vault `/Users/warrenlow/Documents/llm-notes` using the `obsidian-papertool` skill format (`Summary`, `Notes`, `Q&A`).
