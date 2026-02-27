# PaperTool Skill Pack (Claude Plugin)

This repository contains a local Claude plugin that bundles these skills:

- `papertool`
- `obsidian-papertool`
- `manim-slides`

## Manim Slides Orchestration

`manim-slides` is configured as a strict knowledge-first, hard-gated pipeline.

Required phase order:

1. reverse-knowledge-tree
2. manim-code-patterns
3. visual-planner
4. verbose-prompt-builder
5. code synthesis
6. hard-gated renderability checks

Per-topic artifacts are persisted under:

- `/Users/warrenlow/Documents/projects/papertool/.manim-slides/<topic-slug>/`

Required artifacts:

- `knowledge_tree.json`
- `concept_plan.json`
- `visual_plan.json`
- `verbose_prompt.md`
- `slides.py`
- `render_report.json`

## Install locally

From Claude Code:

```text
/plugin install /Users/warrenlow/Documents/projects/papertool
```

Or use plugin discover/install flow and choose this local path.

## Runtime wrappers

The skills use wrapper scripts so you do not need manual `source .venv/bin/activate`:

- PaperTool wrapper: `/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh`
- Manim Slides wrapper: `/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-manim-slides.sh`

## Sync helper

Use this to sync repo skills to Codex/Claude targets:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/sync-skill-targets.sh
```

Optional mirrors:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/sync-skill-targets.sh --mirror-codex-home --mirror-claude-home
```
