# VibeCommit Claude Policy

If `vibecommit.toml` exists at repository root and is enabled:

1. Before any `git commit`, ask the human exactly 3 codebase questions about the pending change.
2. Ask questions one at a time (Q1 -> wait for answer -> validate -> Q2 -> wait -> validate -> Q3 -> wait -> validate). Do not batch all 3 in one message.
3. Make questions high-rigor and tied to the real diff:
   - at least 2 of 3 questions must test architecture or implementation details (exact logic, ordering, thresholds, side effects),
   - at least 1 of 3 must test an edge case or failure path,
   - avoid trivial recall-only questions (for example, file-name-only prompts).
4. If an answer is wrong or incomplete, explain what is missing and ask a replacement question; do not proceed until 3 answers are correct.
5. If all 3 answers are correct, run `./scripts/vibecheck-agent-token.sh`.
6. Only then run `git commit`.
7. Never bypass hooks (`--no-verify` is forbidden).

If agent token creation fails or any answer is not correct, stop and ask the human to resolve before committing.

# Obsidian Logging Policy (PaperTool)

For PaperTool study interactions, treat saving to Obsidian as the default workflow when notes are requested.

Vault path:
- `/Users/warrenlow/Documents/llm-notes`

Note format contract:
- `## Summary` for concise paper/session summary
- `## Notes` for normal ask/discussion notes
- `## Q&A` for quiz/review prompts and answers

Mapping rule:
- `papertool ask`/discussion -> `## Notes`
- PaperTool quiz/review prompts -> `## Q&A`

Do not log raw retrieval chunks as final notes.
