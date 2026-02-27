# Verbose Prompt Format (Subskill D)

Purpose: Produce a deterministic, scene-complete generation prompt from `visual_plan.json` that avoids ambiguity.

## Why This Is Mandatory

- Precise LaTeX + timing + positioning yields better code quality.
- Clear transitions prevent broken scene continuity.
- Complete specifications reduce patch-loop churn.

## Required Inputs

- `knowledge_tree.json`
- `concept_plan.json`
- `visual_plan.json`
- `benchmark-motifs.md`

## Required Sections

1. Header with target concept and progression summary.
2. Runtime constraints (Manim CE + Manim Slides, raw-string LaTeX).
3. Scene sequence, one segment per concept, based on selected visual candidates.
4. Final generation instruction: produce complete runnable code.
5. Motif binding section indicating which benchmark motifs are used per scene.

## Scene Segment Contract

Each scene segment must include:

- Scene title + timestamp window
- Opening action
- Equations with exact LaTeX
- Visual elements + positions + colors
- Ordered animation steps with timing
- Transition hook into next scene

## Timing Defaults

- Simple equation: 2-3s
- Complex equation explanation: 4-5s
- Graph creation: 3-4s
- Transition: 1-2s
- Pause: ~1s

## Color Defaults

- Primary equations: BLUE
- Secondary equations: YELLOW
- Graphs: GREEN/YELLOW
- Highlights: GOLD/ORANGE
- Warnings: RED

## Required Output: `verbose_prompt.md`

The file must be directly usable as input context for code synthesis and must include:

- total concept count
- progression order
- expected total duration
- complete scene sequence with timestamps
- explicit mapping to selected `visual_plan.json` candidates
- explicit mapping to motif IDs (`M1`..`M5`) and adaptation notes

## Hard Requirements

- No vague wording like "show equation".
- No omitted positions/colors.
- No missing transition instructions.
- All LaTeX content must be raw-string-safe.
