# Benchmark Motifs (Top 5)

Purpose: Provide reusable, human-intuitive animation motifs adapted from strong transformer pedagogy patterns.

Use these motifs as building blocks in `visual_plan.json` and bind selected motif IDs in `verbose_prompt.md`.

## M1: Rowwise Product Reveal (Matrix Mechanism)

Use when explaining matrix multiplication, projection, or attention score accumulation.

```python
from manim import *

def animate_rowwise_product(scene, mat_rows, vec_entries, out_entries):
    last = VGroup()
    for row, out in zip(mat_rows, out_entries):
        row_boxes = VGroup(*[SurroundingRectangle(x, buff=0.08) for x in row]).set_stroke(YELLOW, 2)
        vec_boxes = VGroup(*[SurroundingRectangle(x, buff=0.08) for x in vec_entries]).set_stroke(YELLOW, 2)
        scene.play(FadeOut(last), ShowIncreasingSubsets(row_boxes), ShowIncreasingSubsets(vec_boxes))
        scene.play(out.animate.set_value(sum(float(a.get_value()) * float(b.get_value()) for a, b in zip(row, vec_entries))))
        last = VGroup(row_boxes, vec_boxes)
    scene.play(FadeOut(last))
```

## M2: Weighted Context Arcs (Attention Influence)

Use when showing how source tokens influence a target token with variable strengths.

```python
from manim import *

class WeightedContextArcs(LaggedStart):
    def __init__(self, target, sources, strengths, direction=UP, path_arc=PI/2, **kwargs):
        arcs = VGroup()
        for src, s in zip(sources, strengths):
            sign = direction[1] * (-1 if src.get_x() < target.get_x() else 1)
            arc = Line(src.get_edge_center(direction), target.get_edge_center(direction), path_arc=sign * path_arc)
            arc.set_stroke(width=interpolate(0.5, 6.0, s), opacity=interpolate(0.2, 1.0, s))
            arcs.add(arc)
        super().__init__(*[ShowCreation(a) for a in arcs], lag_ratio=0.05, **kwargs)
```

## M3: Next-Token Probability Panel (Autoregressive Output)

Use when explaining logits, softmax, sampling temperature, or candidate token ranking.

```python
from manim import *

def build_token_prob_panel(tokens, probs, width_100=3.0, bar_h=0.24):
    rows = VGroup()
    for t, p in zip(tokens, probs):
        bar = Rectangle(width=(p ** 0.75) * width_100, height=bar_h).set_fill(opacity=1).set_stroke(WHITE, 1)
        label = Text(t, font_size=26).next_to(bar, LEFT, buff=0.15)
        pct = Integer(int(100 * p), unit="%", font_size=22).next_to(bar, RIGHT, buff=0.12)
        rows.add(VGroup(label, bar, pct))
    rows.arrange(DOWN, aligned_edge=LEFT, buff=0.12)
    rows.set_submobject_colors_by_gradient(TEAL, YELLOW)
    return rows
```

## M4: Multi-Head Stack + Projection Labels

Use when introducing multi-head structure and per-head projection maps (`W_Q`, `W_K`, `W_V`).

```python
from manim import *

def make_multihead_stack(head_cards, n_show=8):
    stack = Group(*head_cards)
    stack.arrange(OUT, buff=0.8).set_height(4.2).move_to(DOWN)
    labels = VGroup(*[
        Tex(rf"W_Q^{{({i+1})}}", font_size=32).next_to(stack[-1-i], UP, buff=0.12)
        for i in range(min(n_show, len(stack)))
    ]).set_color(YELLOW).set_backstroke(BLACK, 4)
    return stack, labels
```

## M5: Head Aggregation Into Output Matrix

Use when showing how per-head values are concatenated/combined into final output representation.

```python
from manim import *

def animate_head_aggregation(scene, head_value_maps):
    copies = VGroup(*[m.copy() for m in head_value_maps])
    copies.arrange(RIGHT, buff=SMALL_BUFF).scale(1.3)
    out_brackets = VGroup(Brace(copies, LEFT).set_stroke(PINK, 2), Brace(copies, RIGHT).set_stroke(PINK, 2))
    out_label = Text("Output matrix", font_size=30, color=PINK).next_to(copies, UP, buff=0.2)
    scene.play(LaggedStart(*[TransformFromCopy(m, c) for m, c in zip(head_value_maps, copies)], lag_ratio=0.03), FadeIn(out_brackets), FadeIn(out_label, shift=0.2 * UP))
```

## Motif Selection Rules

- Always evaluate at least 3 motifs per topic.
- Must include at least one mechanism motif (`M1` or `M5`) for algorithmic topics.
- Prefer `M2` + `M3` for probabilistic/attention narratives.
- Record selected motif IDs and rationale in `visual_plan.json`.
