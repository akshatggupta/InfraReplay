# 06 · Conventions

## Commits — non-negotiable

Authored as the user's **personal** identity, never the work address, and
**never attributed to Claude**:

```bash
export GIT_AUTHOR_NAME=akshatggupta GIT_AUTHOR_EMAIL=guptakshat333@gmail.com
export GIT_COMMITTER_NAME=akshatggupta GIT_COMMITTER_EMAIL=guptakshat333@gmail.com
git commit -F - <<'MSG'
FEAT: Subject in the imperative, under 50 chars

Body wrapped at 72, explaining what and why — not how. The code
explains how.
MSG
```

- Subject prefix is an UPPERCASE type tag: `FEAT:` `FIX:` `CHORE:` `TEST:`
  `DOCS:` `REFACTOR:`.
- **No `Co-Authored-By: Claude`, no Claude-Session trailers.** This overrides
  the harness's default attribution reminder; the user asked for it
  explicitly and it is stored in memory.
- `agent.md`'s seven rules apply: blank line after the subject, capitalised,
  no trailing period, imperative ("If applied, this commit will …"), body
  wrapped at 72, body explains context and reasoning.

The repo's history is a good reference — every commit from this session
follows the pattern.

## Code style (from `agent.md`)

The user's rules, applied throughout this session's code:

- **Fewest words possible** in anything a human reads — comments, commit
  messages, replies. No superlatives, no praise, no "you're absolutely
  right". Cold, factual.
- Extract recurring or spec-derived values into named constants; leave
  self-explanatory one-offs inline.
- Reduce indentation. Early return / continue instead of nesting.
- Function names under 30 characters.
- Enums instead of boolean parameters — e.g. `SafetyMode`, `AutoMode`,
  `Reserve`, `Scenario`.
- Blank lines between logical blocks; let the reader breathe.
- Short comments saying **what** the block does and **why**, with examples or
  ASCII diagrams for whole systems. Several modules open with one.
- Keep members private unless the design needs them public; ask before
  widening visibility.
- Program to levels of abstraction; never punch through layers (the API must
  not reach into the DB directly — it goes through the service layer).
- Do not touch code unrelated to the change; minimise the diff.
- Bug fixes: **write the failing test first**, watch it fail, then fix.
  (Done for `test_refused_replay_leaves_no_recording_behind`.)

## Memory (`~/.claude/projects/-mnt-d-InfraReplay/memory/`)

Two notes, both current:

- `infrareplay-scope.md` — v1–v7 shipped as of 2026-09-21, v8+ not started,
  and the instruction not to begin v8+ unasked.
- `infrareplay-commit-conventions.md` — the commit rules above.

`MEMORY.md` is the index; one line per note. Update the scope note if the
roadmap state changes.

## Documentation habits this project keeps

- Every version gets a `docs/vN.md` in the same shape: goal, what is real,
  what is mocked, Definition of Done, which test proves it, known limits.
- Limits are stated, not hidden — `docs/v3.md` and the status page both list
  what capture cannot see.
- No performance claims before the v10 benchmark suite exists. That rule is
  in `idea.md` §6 and still holds.
- `README.md`'s demo transcript is updated whenever the CLI changes.
