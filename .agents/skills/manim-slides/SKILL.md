---
name: manim-slides
description: Create animated presentations with Manim Slides from user prompts. Use when the user asks to make slides, generate an animated deck, present with Manim Slides, convert Manim slides to html/pdf/video, or build speaker-note-ready math/technical presentations.
---

# Manim Slides Skill

Use this skill to produce high-quality Manim Slides decks with a strict knowledge-first pipeline.

## Style Target (Default)

Default to a 3b1b-like explanatory style:

- concept-first visual storytelling
- mathematically precise but visually lightweight notation
- smooth continuity across scenes (state evolves, not hard resets)
- minimal clutter and deliberate pacing
- animations that reveal mechanism, not decoration

Always read `benchmark-motifs.md` to anchor style decisions, even when you do not directly implement specific motifs.

## Contract (Mandatory)

Execute phases in exact order for every run:

1. `Subskill A: reverse-knowledge-tree`
2. `Subskill B: manim-code-patterns`
3. `Subskill C: visual-planner`
4. `Subskill D: verbose-prompt-builder`
5. `Slide code synthesis from verbose prompt`
6. `Hard-gated renderability checks`

Do not skip phases unless the user explicitly requests a phase override.

## Internal Subskills (Load Order)

Load and apply these references in sequence:

1. `/Users/warrenlow/Documents/projects/papertool/skills/manim-slides/references/reverse-knowledge-tree.md`
2. `/Users/warrenlow/Documents/projects/papertool/skills/manim-slides/references/manim-code-patterns.md`
3. `/Users/warrenlow/Documents/projects/papertool/skills/manim-slides/references/benchmark-motifs.md`
4. `/Users/warrenlow/Documents/projects/papertool/skills/manim-slides/references/visual-planner.md`
5. `/Users/warrenlow/Documents/projects/papertool/skills/manim-slides/references/verbose-prompt-format.md`
6. `/Users/warrenlow/Documents/projects/papertool/skills/manim-slides/references/manim-slides-api-cheatsheet.md`

Output of each subskill is input to the next subskill.

## Runtime (No Manual Venv)

Use wrappers first:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-manim-slides.sh <args>
```

Primary CLI flow:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-manim-slides.sh render /abs/path/slides.py Deck
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-manim-slides.sh present Deck
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-manim-slides.sh convert Deck /abs/path/output.html
```

Python fallback flow:

```bash
/Users/warrenlow/Documents/projects/papertool/scripts/skill-runtime/run-manim-slides.sh python /abs/path/slides.py
```

## Persistent Topic Cache

For topic `<slug>`, use this directory:

- `/Users/warrenlow/Documents/projects/papertool/.manim-slides/<slug>/`

Required artifacts:

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
