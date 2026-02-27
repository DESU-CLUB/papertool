# Manim Code Patterns (Subskill B)

Purpose: Convert concept plan to robust Manim CE + Manim Slides structures.

## Structural Pattern

Use methodized scene organization:

1. Setup shared styling constants.
2. Foundation scene methods.
3. Buildup scene methods.
4. Target scene methods.

Prefer explicit methods over monolithic `construct` bodies.

## LaTeX Rules

- Always use raw strings: `r"..."`.
- Use `MathTex` for equations.
- Use `Tex` for mixed prose + math.
- For complex formulas, break into components for selective coloring.

## Positioning Rules

- Favor relative positioning (`next_to`, `to_edge`, `arrange`) over hard-coded coordinates.
- Group related objects with `VGroup`.
- Maintain stable anchors across transitions.

## Animation Rules

- Use sequential animation for concept introduction.
- Use simultaneous animation only when semantically coupled.
- Use `Transform`/`ReplacementTransform` for continuity.
- Add bounded waits for comprehension pacing.

## Visual Consistency

Define and reuse a palette map:

- primary equation color
- secondary equation color
- highlights
- graph color
- label color

Never randomly change palette mid-deck.

## Dynamic Constructs

Use `ValueTracker`/`always_redraw` for parameter demonstrations.
Use updaters sparingly and clear them when done.

## Defensive Patterns

- Check object lifecycle before transforming/fading.
- Set z-index for overlap-heavy scenes.
- Constrain run_time for complex draws.
- Keep clean teardown helpers.

## Required Output: `concept_plan.json`

```json
{
  "topic": "...",
  "scene_plan": [
    {
      "scene_id": "scene_01",
      "concept": "...",
      "prerequisites": ["..."],
      "equations": ["..."],
      "visual_motif": "...",
      "transition_in": "...",
      "transition_out": "...",
      "estimated_seconds": 15
    }
  ],
  "style": {
    "palette": {},
    "typography": {},
    "spacing": {}
  },
  "generated_at": "ISO-8601"
}
```
