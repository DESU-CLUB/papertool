---
name: manim-slides
description: Template skill for creating Manim Slides decks. Discover local paths and wrappers first, then run the knowledge-first generation pipeline.
---

# Manim Slides Skill (Template)

This is a template. Do not assume fixed absolute paths.

## Path Discovery (Required First)

Find repo root, then use repo-relative paths:

- `scripts/skill-runtime/run-manim-slides.sh`
- `skills/manim-slides/references`
- `.manim-slides`

Suggested discovery commands:

```bash
pwd
rg --files | rg '^scripts/skill-runtime/run-manim-slides.sh$|^skills/manim-slides/references/|^pyproject.toml$'
find ~ -type f -path '*/scripts/skill-runtime/run-manim-slides.sh' 2>/dev/null | head
```

## Update Local Skill Paths

After discovery, update your local/private skill copy only if your runtime differs from repo-relative defaults.
Keep this Git-tracked template generic.

## Mandatory Phase Order

1. reverse-knowledge-tree
2. manim-code-patterns
3. visual-planner
4. verbose-prompt-builder
5. code synthesis
6. hard-gated render checks

## Reference Loading

Load required docs from `skills/manim-slides/references/` in this order:

1. `reverse-knowledge-tree.md`
2. `manim-code-patterns.md`
3. `benchmark-motifs.md`
4. `visual-planner.md`
5. `verbose-prompt-format.md`
6. `manim-slides-api-cheatsheet.md`

## Runtime

Preferred wrapper flow:

```bash
scripts/skill-runtime/run-manim-slides.sh render /abs/path/slides.py Deck
scripts/skill-runtime/run-manim-slides.sh present Deck
scripts/skill-runtime/run-manim-slides.sh convert Deck /abs/path/output.html
```

Fallback:

```bash
uvx --from "manim-slides[manim]" manim-slides --help
```

## Cache Artifacts

Per topic slug under `.manim-slides/<slug>/`:

- `knowledge_tree.json`
- `concept_plan.json`
- `visual_plan.json`
- `verbose_prompt.md`
- `slides.py`
- `render_report.json`

Default cache behavior:

- Reuse existing artifacts when present.
- Regenerate only when user asks for rebuild/regenerate-fresh.

## Phase Protocol

### Phase 0: Preprocess

1. Normalize user topic and audience.
2. Compute deterministic slug.
3. Resolve absolute artifact paths.
4. Load existing artifacts if present.

### Phase 1: Reverse Knowledge Tree (Subskill A)

Use `reverse-knowledge-tree.md`.

Requirements:

- Build DAG with target as root.
- Foundation baseline: high-school-level concepts.
- Limits: `max_depth=4`, `max_prerequisites=5`.
- Mark foundation concepts.
- Produce topological order from foundations to target.
- Write `knowledge_tree.json`.

### Phase 2: Visual/Math Planning (Subskill B)

Use `manim-code-patterns.md` and `manim-slides-api-cheatsheet.md`.

Requirements:

- Map each topo-sorted concept to one scene intent.
- Enforce raw-string LaTeX rules.
- Define consistent palette/typography/spacing policy.
- Choose animation motifs and transitions per concept.
- Write `concept_plan.json`.

### Phase 3: Visual Planner (Subskill C)

Use `visual-planner.md` and `manim-slides-api-cheatsheet.md`.

Requirements:

- For each concept, propose 2-3 candidate visual explanations.
- Decompose complex mechanisms into atomic visual beats.
  - Example: FlashAttention tiling should break into Q/K/V chunk creation, tile interaction, partial accumulation, and output matrix assembly.
- Score candidates with explicit rubric dimensions:
  - concept faithfulness
  - explanatory clarity
  - cognitive load
  - temporal coherence
- Iteratively refine until selected plan reaches threshold (`>= 8.0/10`) or max 3 refinement passes.
- Keep a score trace for each iteration.
- Write `visual_plan.json`.

### Phase 4: Verbose Prompt Assembly (Subskill D)

Use `verbose-prompt-format.md`.

Requirements:

- Generate complete scene-by-scene verbose prompt.
- Source scene directives from selected entries in `visual_plan.json`.
- Include timestamps, animation ordering, transition hooks.
- Include explicit math notation and positioning details.
- Include color and pacing constraints.
- Write `verbose_prompt.md`.

### Phase 5: Code Synthesis

Generate `slides.py` only from `verbose_prompt.md` + API constraints.

Requirements:

- Use `Slide`/`ThreeDSlide` where appropriate.
- Use `next_slide()` boundaries for presentation control.
- Keep one concept per slide.
- Avoid text walls.
- Ensure code is deterministic and rerunnable.

### Phase 6: Hard-Gated Renderability

Final output must be blocked until renderability passes or missing dependency is explicitly diagnosed.

Required checks:

1. Python syntax sanity on `slides.py`.
2. Manim Slides command sanity using wrapper.
3. Render attempt for target scene.

Failure behavior:

- Capture failing command + stderr.
- Patch only relevant block.
- Retry in bounded loop.
- On dependency failure, stop with explicit missing dependency diagnosis.

Write `render_report.json` with:

- `status`: `passed|failed|blocked_missing_dependency`
- `attempts`
- `failed_command`
- `errors`
- `last_updated_at`

## Visual Quality Rubric

Enforce all items:

- One concept per slide.
- Consistent palette and typography.
- Incremental reveals over dense text.
- Explicit transition intent for each slide.
- Math labels aligned to narration and timing.

## Final Response Format

Return:

1. Absolute artifact paths for all 6 artifacts.
2. Rerunnable commands (`render`, `present`, `convert`).
3. Hard-gate pass/fail summary from `render_report.json`.
4. Short notes on what was reused from cache vs rebuilt.
