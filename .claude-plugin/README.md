# PaperTool Skill Pack (Local Claude Plugin)

This plugin bundles local skills for:
- `papertool`
- `obsidian-papertool`
- `manim-slides`

It is a local-path plugin, not a marketplace package.

## What You Get

1. PaperTool workflows from Claude without manually activating a venv.
2. Obsidian logging workflow for study notes and Q&A.
3. Manim Slides generation with a strict knowledge-first pipeline.

## Prerequisites

1. Claude Code installed and working.
2. `uv` installed.
3. Repo checked out at:
   `/Users/warrenlow/Documents/projects/papertool`

Optional but recommended for slide rendering:
1. `ffmpeg` (video encoding)
2. LaTeX toolchain (`latex`, `dvisvgm`) for `MathTex` rendering
3. Cairo/Pango (`pkg-config`, `cairo`, `pango`) required by Manim

Example installs:

macOS (Homebrew):
```bash
brew install ffmpeg pkg-config cairo pango mactex-no-gui dvisvgm
```

Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install -y ffmpeg pkg-config libcairo2-dev libpango1.0-dev texlive-full dvisvgm
```

## Install The Plugin (Local Path)

Run in Claude Code:

```text
/plugin install /Users/warrenlow/Documents/projects/papertool
```

Then restart Claude Code (or reload plugins if your client supports it).

## Verify It Loaded

Confirm these files exist:
- `/Users/warrenlow/Documents/projects/papertool/.claude-plugin/plugin.json`
- `/Users/warrenlow/Documents/projects/papertool/skills/papertool/SKILL.md`
- `/Users/warrenlow/Documents/projects/papertool/skills/obsidian-papertool/SKILL.md`
- `/Users/warrenlow/Documents/projects/papertool/skills/manim-slides/SKILL.md`

## Manim Slides Pipeline

`manim-slides` runs this phase order:
1. reverse-knowledge-tree
2. manim-code-patterns
3. visual-planner
4. verbose-prompt-builder
5. code synthesis
6. hard-gated render checks

Per-topic artifacts are written to:
- `/Users/warrenlow/Documents/projects/papertool/.manim-slides/<topic-slug>/`

Artifacts:
- `knowledge_tree.json`
- `concept_plan.json`
- `visual_plan.json`
- `verbose_prompt.md`
- `slides.py`
- `render_report.json`

## Runtime Wrappers

These wrappers are the default execution path:
- `/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-papertool.sh`
- `/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-manim-slides.sh`

This avoids `source .venv/bin/activate` in normal skill use.

## Usage Examples

1. PaperTool:
   - “Use papertool to import this arXiv URL and queue it for today.”
2. Obsidian logging:
   - “Save this PaperTool session to Obsidian in the DeepSeek note.”
3. Slide deck:
   - “Create a Manim Slides deck explaining FlashAttention tiling for ML engineers.”

## Operational Docs

Operational command usage and deeper behavior notes are maintained in:
- `/Users/warrenlow/Documents/projects/papertool/README.md`

See these sections there:
- `Operational Reference`
- `Usage`
- `Medal Logic`
- `Graph export internals`
- `Manim Slides Optional Dependencies`

## Attribution

This skill-pack’s slide workflow builds on ideas and tooling from:
- [Math-To-Manim](https://github.com/HarleyCoops/Math-To-Manim) by HarleyCoops (prompting and pedagogy inspiration)
- [Manim](https://github.com/3b1b/manim) by 3Blue1Brown (original Manim engine lineage)
- [Manim Slides](https://github.com/jeertmans/manim-slides) by Jean Eertmans (slide/presenter workflow)

## Developer Appendix (Optional)

Skill sync is mainly for maintainers:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/sync-skill-targets.sh
```

Optional mirrors:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/sync-skill-targets.sh --mirror-codex-home --mirror-claude-home
```

Parity check only:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/sync-skill-targets.sh --check
```
