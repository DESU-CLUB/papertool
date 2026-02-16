# VibeCommit Claude Policy

If `vibecommit.toml` exists at repository root and is enabled:

1. Before any `git commit`, ask the human exactly 3 codebase questions about the pending change.
2. If all 3 answers are correct, run `./scripts/vibecheck-agent-token.sh`.
3. Only then run `git commit`.
4. Never bypass hooks (`--no-verify` is forbidden).

If agent token creation fails or answers are not correct, stop and ask the human to resolve before committing.
