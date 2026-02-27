# Manim Slides API Cheatsheet

Source baseline: Manim Slides quickstart and reference docs.

## Core Classes

- `Slide` for 2D presentation scenes
- `ThreeDSlide` for 3D presentation scenes

## Slide Control Methods

- `next_slide()` to split presenter-controlled steps
- `pause()` for controlled delay when needed

Use `next_slide()` at concept boundaries and major reveal boundaries.

## CLI Workflow

Wrapper command prefix (from repo root):

```bash
scripts/skill-runtime/run-manim-slides.sh
```

Common commands:

```bash
... render slides.py SceneName
... present SceneName
... convert SceneName output.html
... list-scenes slides.py
... checkhealth
```

## Beautiful Slide Practices

- Keep one core idea per slide segment.
- Prefer incremental reveals with `next_slide()`.
- Keep typography and spacing consistent.
- Avoid dense full-screen text blocks.
- Use transitions to preserve viewer context.

## Renderability Gate Integration

Before final output, ensure:

1. script parses
2. scene list resolves
3. render command succeeds or explicit dependency block is reported

If missing dependencies are detected, provide exact failing command and missing component names.
