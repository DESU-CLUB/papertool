---
name: obsidian-papertool
description: Save PaperTool session notes and quiz Q&A into an Obsidian vault using a fixed study-note format.
---

# Obsidian PaperTool Notes

Use this skill when the user asks to save study/session notes or quiz/review output to Obsidian.

## Path Discovery (Required First)

Resolve these values before writing:

- `<VAULT_PATH>`
- `<PAPERS_DIR>`
- `<SESSIONS_DIR>`

Priority:
1. Use explicit path(s) from user prompt when provided.
2. Else discover vault candidates by searching for `.obsidian` directories.
3. If multiple vaults exist, ask user to choose before writing.

Suggested discovery command:

```bash
find ~ -type d -name .obsidian 2>/dev/null | head -n 20
```

## Update Local Skill Paths

After discovery, update your local/private skill copy with resolved paths if needed.
Keep this Git-tracked skill generic.

## Target File Rules

1. If user gives an exact file path, write there.
2. Else if a paper title is known, write to:
   - `<PAPERS_DIR>/<paper-title>.md`
3. Else write to a session note:
   - `<SESSIONS_DIR>/<YYYY-MM-DD>.md`

Create parent folders if missing.
Never overwrite the full note unless explicitly requested.

## Required Note Format

```markdown
## Summary
<concise summary paragraph>

## Notes
<normal study notes from asks/discussion>

## Q&A
### Review Session (YYYY-MM-DD)
**Q: <quiz/review question>**
A: <answer>
```

## Mapping Rules

- `## Notes`:
  - Use for normal discussion and `papertool ask` style notes.
  - Do not paste retrieval chunks or raw tool logs.
- `## Q&A`:
  - Use only for quiz/review prompts and answers.
  - Group entries under `### Review Session (YYYY-MM-DD)`.

## Style Rules

- Keep notes clean and study-oriented.
- Do not add noisy timestamps to headings (except review-session date).
- Prefer compact, high-signal phrasing over transcript dumps.
- Preserve existing sections and append incrementally.
