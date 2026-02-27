# Reverse Knowledge Tree (Subskill A)

Purpose: Build a prerequisite DAG that orders teaching from foundations to target concept, without training-data dependency.

## Core Rule

For concept `X`, recursively ask:

- "What must a learner understand before `X`?"

This produces a Directed Acyclic Graph (DAG) of knowledge dependencies.

## Node Schema

Use this JSON-compatible schema:

```json
{
  "concept": "string",
  "depth": 0,
  "is_foundation": false,
  "prerequisites": []
}
```

Enrichment fields allowed:

```json
{
  "equations": ["..."],
  "definitions": {"var": "meaning"},
  "visual_spec": {},
  "narrative": "..."
}
```

## Defaults

- `max_depth = 4`
- `max_prerequisites = 5`
- Foundation baseline = high-school understanding

## Foundation Heuristic

Treat as foundation when a typical high-school graduate can understand without further decomposition.

Foundation examples:

- velocity, time, acceleration
- force, mass, energy
- numbers, functions, graphs
- frequency, wavelength

Non-foundation examples:

- tensor calculus
- Hilbert spaces
- Lorentz transforms
- gauge theory

## Algorithm

1. Start with target concept at depth `0`.
2. If depth limit hit or concept foundation, mark `is_foundation=true` and stop recursion.
3. Discover prerequisites (3-5 max, essential only).
4. Recurse prerequisites.
5. Build DAG and deduplicate repeated concepts by normalized name.
6. Topologically sort from leaf foundations to target.

## Required Output: `knowledge_tree.json`

```json
{
  "target": "...",
  "max_depth": 4,
  "max_prerequisites": 5,
  "nodes": [],
  "edges": [],
  "topological_order": [],
  "foundations": [],
  "generated_at": "ISO-8601"
}
```

## Prompting Guidance

When discovering prerequisites, request only essential enabling concepts, not historical context.

Use strict output shape (JSON list of concept names).
