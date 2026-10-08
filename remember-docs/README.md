# remember-docs

Handoff notes for picking InfraReplay up in a **new Claude Code session**
(or on another machine). Written 2026-10-08, covering the session that ran
2026-09-21 → 2026-10-08, and updated the same day after v8, v9 and the TUI.

Read them in this order; each is standalone.

| File | What it answers |
|---|---|
| [01-project-overview.md](01-project-overview.md) | What InfraReplay is, the stack, the v1→v12 roadmap, where the build stands |
| [02-architecture.md](02-architecture.md) | How the pieces fit, and the design decisions that are not obvious from the code |
| [03-session-history.md](03-session-history.md) | What this session actually did, commit by commit |
| [04-run-and-demo.md](04-run-and-demo.md) | Every command: local, Docker, Compose, CLI, tests, and the demo script |
| [05-gotchas.md](05-gotchas.md) | Traps already hit and fixed. **Read this before debugging anything.** |
| [06-conventions.md](06-conventions.md) | Commit rules, code style, how to author commits as the user |
| [07-next-steps.md](07-next-steps.md) | v8–v12 scope, known limits, and what not to start unasked |
| [08-environment.md](08-environment.md) | Paths, ports, env vars, venv, Docker state, links |

## The 60-second version

InfraReplay records a real HTTP + PostgreSQL workflow, replays it somewhere
else, and diffs the two runs — so reproducing an incident stops being a
guessing game. Core loop: **capture → correlate → store → replay → compare**.

The build spec (`idea.md`) breaks it into twelve versions. **v1–v9 are
done** (75%): the whole pipeline on real traffic with a real concurrency
bug caught end to end, plus security hardening (v8) and observability (v9).
There is also a terminal UI, `infractl tui`. v10–v12 have not been
started — and should not be started without being asked.

Everything is committed on `main`. Pushed through `01fc4ec`; the v8, v9
and TUI commits (through `7bd18c0`) were **not pushed** as of 2026-10-08.

## If you are a fresh Claude session

1. Read `01`, `02`, `05`, `06` — that is the minimum to work safely here.
2. Do not re-derive the roadmap from `idea.md`; `01` and `07` already state
   what is done and what is not.
3. Check `05-gotchas.md` before touching the plugin registry, the Postgres
   schema, Docker Compose, or anything timing-sensitive.
4. The repo's own docs are still the source of truth for design:
   `architecture.md`, `docs/v1.md` … `docs/v7.md`, `docs/roadmap.md`.
   These notes point at them rather than duplicating them.
