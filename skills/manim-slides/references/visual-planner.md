# Visual Planner (Subskill C)

Purpose: Select the best visual explanation strategy for each concept before verbose prompt assembly and code generation.

## Core Principle

The best animation is not the most complex animation; it is the one that most clearly transfers the concept.

For each concept, generate multiple candidate visualizations, score them, and iteratively refine.

## Inputs

- `knowledge_tree.json`
- `concept_plan.json`
- `benchmark-motifs.md`

## Required Output: `visual_plan.json`

```json
{
  "topic": "...",
  "threshold": 8.0,
  "max_refinement_passes": 3,
  "concept_visuals": [
    {
      "concept": "...",
      "candidates": [
        {
          "id": "cand_a",
          "description": "...",
          "beats": ["..."],
          "score": {
            "faithfulness": 0.0,
            "clarity": 0.0,
            "cognitive_load": 0.0,
            "temporal_coherence": 0.0,
            "overall": 0.0
          },
          "issues": ["..."],
          "refinement_notes": ["..."]
        }
      ],
      "selected_candidate_id": "cand_a",
      "selected_overall": 0.0,
      "iteration_trace": [
        {"pass": 1, "selected_overall": 0.0},
        {"pass": 2, "selected_overall": 0.0}
      ]
    }
  ],
  "generated_at": "ISO-8601"
}
```

## Candidate Generation Rules

For each concept:

1. Create 2-3 distinct visual strategies.
2. Keep one strategy minimal and one strategy interaction-heavy.
3. Decompose into atomic visual beats.
4. Respect one-core-idea-per-slide policy.
5. Bind each candidate to one or more motif IDs from `benchmark-motifs.md`.

## Scoring Rubric

Score each candidate from 0-10 per dimension:

- `faithfulness`: visual truth to the underlying mechanism
- `clarity`: ease of understanding on first viewing
- `cognitive_load`: avoids overloading viewer working memory
- `temporal_coherence`: sequence logically builds and transitions cleanly

Overall score:

```text
overall = 0.35*faithfulness + 0.30*clarity + 0.20*cognitive_load + 0.15*temporal_coherence
```

Selection threshold:

- Target `overall >= 8.0`
- If below threshold, refine and re-score (up to 3 passes)

## Refinement Loop

When selected candidate is below threshold:

1. Identify weakest rubric dimension(s).
2. Modify beats only where needed.
3. Reduce unnecessary object count.
4. Improve transition continuity and pacing.
5. Re-score and log iteration result.

If threshold still not met after max passes, choose highest-scoring candidate and explicitly annotate residual weaknesses.

## Motif Binding Requirement

Every selected candidate must include:

- `motif_ids`: list of benchmark motifs used (for example `["M1", "M5"]`)
- `motif_adaptation_notes`: how motif is adapted to the concept

For algorithmic explainers (FlashAttention, MoE routing, KV cache, etc.), selected candidate must use `M1` or `M5`.

## Decomposition Pattern for Complex Algorithms

For algorithm-heavy topics, explicitly split mechanism into visual chunks.

Example: FlashAttention tiling

1. Show Q, K, V matrices as tiled grids.
2. Animate selection of one Q tile and one K/V tile pair.
3. Show partial score/output computation for that pair.
4. Show accumulation into output tile.
5. Iterate over tile traversal order.
6. Reconstruct full output matrix from accumulated tiles.

This pattern prevents “black-box” animations and improves concept transfer.

## Hard Constraints

- No direct code generation in this subskill.
- No transition to verbose prompt without completed scoring trace.
- No skipping candidate comparison.
