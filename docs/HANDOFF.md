# HANDOFF: live state of the build

Update this file before you stop working. Newest entry on top. Keep it short and factual.

## Current state

- Phase: **planning done, build not started.**
- Done: T00 (handoff kit), plan in `docs/PLAN.md`, issues T01–T29 on GitHub (T29 = demo video, reserve capacity for it).
- In progress: none.
- Next: T01 (contracts) → T04 → T05. Human: T02 (footage, sponsor list, hand count, permission)
  and T03 (create Aiven services, check Runtime access).
- Blocked: T06/T07/T08 need footage from T02; T05 needs Aiven credentials from T03.

## Log

### 2026-10-22: planning (orchestrator: Claude Code, desktop session)
- Wrote PLAN.md, AGENTS.md, CLAUDE.md, this file. Created labels, milestones M1–M4 and issues.
- Decisions: rewrite from scratch (earlier prototype is reference only); recognition by
  appearance (surface detector + embeddings + Claude vision), not OCR; per-surface metric v1 =
  type + side + LED rotation, v2 = board IDs via homography.
- Usage ladder for development: Claude subscription → $90 extra Claude usage in the app →
  Codex (OpenAI key) → Lovable for `web/`. Anthropic API $100 is reserved for the product.

## Template for a new entry

```
### <date time>: Txx <title> (<agent/model>)
- Done:
- Checks run (commands + result):
- Branch / PR:
- Next step:
- Blockers / notes:
```
