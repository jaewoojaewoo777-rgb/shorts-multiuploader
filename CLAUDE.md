# Agent Guide (Claude Code)

This repository uses a code-first Playwright pipeline with agent recovery. Run
all commands from the repository root and use values from `.env`; never infer an
account handle, page URL, category, or campaign rule.

`AGENTS.md` is the original Codex guide and is kept unchanged. This file is the
Claude Code equivalent; the skill lives at `.claude/skills/shorts-upload/`.

## Default workflow

1. Run `uv run shorts-dist doctor` and `uv run shorts-dist plan --json`.
2. Upload with `uv run shorts-dist run --json`, or retry one cell with
   `uv run shorts-dist upload <platform> <ID>`.
3. If exit code is `3`, read `data/runs/<run_id>/report.json`. Continue every
   healthy matrix cell before working on a blocked platform.
4. Use Claude in Chrome (`mcp__claude-in-chrome__*`) only for the reported
   failed cell, inconclusive verification, or UI diagnosis.
5. After a manual completion and direct verification, record it with
   `uv run shorts-dist mark-uploaded <platform> <ID> --url '<POST_URL>' --source agent`.
6. Resolve pending posts with `uv run shorts-dist verify --pending`.

## Hard rules

- Never re-upload until fresh verification on that platform says the post is
  missing. A timeout or closed composer is not proof.
- Publish the prepared `post_text` exactly, except for uploader-documented length
  truncation. Do not append IDs, filenames, URLs, or debugging notes.
- Preserve oldest-first order inside the selected batch.
- Apply `config/content-policies.json` to explicit IDs as well as planned batches.
- A platform blocker affects only that platform lane. Keep the selected video
  IDs fixed and complete the remaining cells first.
- Stop on login, 2FA, CAPTCHA, copyright, permissions, wrong-account context, or
  an unimplemented disclosure control. Ask the user to decide.
- Never commit `.env` or anything under `data/` other than tracked `.gitkeep`
  files.
- A user request to stop includes terminating the command and all browser child
  processes started by this task. Verify process cleanup before replying.

## 오늘만선 AI disclosure (fail closed)

- Every 오늘만선 Short is a photorealistic AI-character video. The
  `ai-generated-channel` rule in `config/content-policies.json` requires the
  `ai-generated` disclosure on instagram, tiktok, and naver for every video, and
  restricts posting to those three platforms.
- Uploaders turn the platform's AI label toggle on and verify its checked state
  before the final publish click. If the toggle is missing, ambiguous, or its
  state cannot be confirmed, the cell ends `blocked` at step `ai-label` and
  nothing is published.
- An `ai-label` blocker is a stop-and-ask item: report it to the user. Never
  publish that cell manually without the AI label turned on and visibly
  confirmed.
- Claude in Chrome cannot open `clipcreators.naver.com` (blocked by safety
  restrictions). Naver recovery must be done by the user; Claude reads the
  failure PNG/HTML and repairs selectors instead.

## Claude in Chrome recovery

- Claude in Chrome drives the Chrome profile where its extension is installed,
  which is usually the user's everyday Chrome, not `data/profiles/shared`.
  Confirm the visible account matches `.env` before any action.
- Never drive `data/profiles/shared` from Claude in Chrome while `shorts-dist`
  is using it.
- Use `file_upload` for media; Claude cannot operate the native macOS file
  picker. See the skill's "Claude in Chrome setup and tool mapping" section.

## UI self-healing

Start with the failed cell's PNG and full HTML. Open a live browser only if those
artifacts are insufficient, and never run it concurrently with code using the
same Chrome profile. Add new selector candidates without deleting older ones,
scope controls to the active dialog, verify state after clicks, and retest with a
single-cell upload. Record new reusable findings in
`.claude/skills/shorts-upload/references/failure-modes.md`.

Follow [the full skill](.claude/skills/shorts-upload/SKILL.md) for detailed
recovery and verification behavior.
