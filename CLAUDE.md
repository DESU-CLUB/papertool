# VibeCommit Claude Policy

If `vibecommit.toml` exists at repository root and is enabled:

1. Before any `git commit`, ask the human exactly 3 codebase questions about the pending change.
2. Ask questions one at a time (Q1 -> wait for answer -> validate -> Q2 -> wait -> validate -> Q3 -> wait -> validate). Do not batch all 3 in one message.
3. If all 3 answers are correct, run `./scripts/vibecheck-agent-token.sh`.
4. Only then run `git commit`.
5. Never bypass hooks (`--no-verify` is forbidden).

If agent token creation fails or any answer is not correct, stop and ask the human to resolve before committing.
