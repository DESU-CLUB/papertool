---
name: obsidian-papertool
description: Save PaperTool session notes and quiz Q&A into Obsidian vault /Users/warrenlow/Documents/llm-notes using a fixed study-note format. Use when the user asks to save study notes, session notes, ask outputs, or quiz/review content to Obsidian.
---

# Obsidian PaperTool Notes

Use this skill when the user wants PaperTool interactions saved to Obsidian.

## Vault

- Default vault path: `/Users/warrenlow/Documents/llm-notes`
- Preferred paper note folder: `/Users/warrenlow/Documents/llm-notes/Paper Reading/Papers`

## Target File Rules

1. If user provides an exact file path, write there.
2. Else if a paper title is known, write to:
   - `/Users/warrenlow/Documents/llm-notes/Paper Reading/Papers/<paper-title>.md`
3. Else write to a session note:
   - `/Users/warrenlow/Documents/llm-notes/Paper Reading/Sessions/<YYYY-MM-DD>.md`

Create parent folders if missing.
Never overwrite the full note unless explicitly requested.

## Required Note Format

```markdown
## Summary
<concise summary paragraph>

## Notes
<normal study notes from user asks and discussion>

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
  - Use only for quiz/review prompts and answers (PaperTool quiz/review flow).
  - Group entries under `### Review Session (YYYY-MM-DD)`.

## Style Rules

- Keep notes clean and study-oriented.
- Do not include noisy timestamps in headings (except review-session date).
- Prefer compact, high-signal phrasing over transcript-style dumps.
- Preserve existing sections and append incrementally.
