# Shorts Multiuploader

A reusable, code-first pipeline for cross-posting your own YouTube Shorts to
Instagram, Threads, TikTok, LinkedIn, Facebook, and Naver Clip.

Playwright handles the normal upload path with your locally authenticated Chrome
profile. Claude in Chrome (Claude Code) is a recovery layer for individual failed cells,
inconclusive verification, and platform UI changes. No creator handles, campaign
names, cookies, or account URLs are built into this repository.

> Use this only for content you own or are authorized to distribute. You are
> responsible for each platform's terms, rate limits, advertising disclosures,
> and local law. UI automation is inherently brittle, so inspect every dry run
> before enabling unattended use.

## Why this skeleton exists

Cross-posting is not one six-step script. It is a matrix of independent
`video x platform` jobs whose upload, verification, and retry states must survive
partial failure. This repository already provides the less glamorous pieces:

- YouTube discovery and download through `yt-dlp`
- H.264 normalization with `ffmpeg`
- chronological posting, oldest first
- SQLite deduplication and post-verification state
- isolated platform lanes so one broken login does not stop the others
- exact-text checks before publish
- screenshots, HTML, and a cell-specific recovery brief on failure
- declarative per-video policies for campaign text, target platforms, and
  disclosure controls
- an agent skill that knows when to use code and when to take over the UI

## Architecture

```text
YouTube channel
    |
    v
plan -> prepare -> content policy -> H.264 -> platform lanes
                                              | upload
                                              | verify
                                              v
                                  SQLite state + run report
                                              |
                                      failed cell only
                                              v
                                  Claude in Chrome fallback
```

The unit of work is one matrix cell, not the entire batch. A login failure on
Facebook can block the Facebook lane while Instagram and TikTok continue. A cell
is never retried merely because an upload dialog disappeared; fresh platform
verification must first show that the post is missing.

See [architecture.md](.claude/skills/shorts-upload/references/architecture.md)
for status semantics, ownership boundaries, and extension constraints.

## Requirements

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- Google Chrome
- `ffmpeg`, `ffprobe`, and `yt-dlp`
- macOS is the primary tested environment; the Python and Playwright layers are
  portable, but profile and Chrome behavior should be verified on your OS
- Claude Code with the Claude in Chrome extension only if you want agent recovery

## Quick start

```bash
git clone https://github.com/<your-account>/shorts-multiuploader.git
cd shorts-multiuploader
uv sync
cp .env.example .env
```

Edit `.env` with your own channel, targets, handles, and verification URLs. Then:

```bash
# Create or refresh local login sessions. Repeat for enabled platforms.
uv run shorts-dist login instagram
uv run shorts-dist login tiktok

# Read-only checks, followed by a no-publish dry run.
uv run shorts-dist doctor
uv run shorts-dist run --dry-run

# Run the configured batch.
uv run shorts-dist run
```

Login profiles live under `data/profiles/` and never belong in Git. Close Chrome
windows using the same profile before starting a pipeline command.

## Configuration

The main batch controls are:

```dotenv
YOUTUBE_HANDLE=@your-youtube-handle
TARGET_PLATFORMS=instagram,threads,tiktok,linkedin,facebook,naver
POST_TEXT_MODE=caption
SHORTS_SELECTION_MODE=recent
SHORTS_UPLOAD_LIMIT=2
SHORTS_LOOKBACK_LIMIT=50
SHORTS_SKIP_ADS=true
PROFILE_STRATEGY=shared
```

Use `shared` for the simplest setup or `per-platform` to isolate Chrome profiles.
The remaining account and verification settings are documented in
[.env.example](.env.example).

### Creator-owned content policies

Campaign and channel-specific logic belongs outside the source code:

```bash
cp config/content-policies.example.json config/content-policies.json
```

Set `CONTENT_POLICIES_FILE=config/content-policies.json` in `.env`. A matching
rule can:

- use the full YouTube description instead of the default caption
- prepend or append required disclosure text
- restrict the Short to an approved subset of platforms
- request a supported UI disclosure such as Instagram `paid-partnership` or
  TikTok `branded-content`

Explicit `--video-id` selection does not bypass these policies. Unsupported or
missing disclosure controls fail closed before publishing.

## Commands

```bash
# Inspect and prepare
uv run shorts-dist platforms
uv run shorts-dist list-shorts --limit 10
uv run shorts-dist plan --json
uv run shorts-dist prepare --video-id <YOUTUBE_ID> --json

# Upload and verify
uv run shorts-dist run --json
uv run shorts-dist run --platforms instagram,tiktok --video-id <YOUTUBE_ID>
uv run shorts-dist upload <platform> <YOUTUBE_ID>
uv run shorts-dist verify --pending

# State and manual/agent completion
uv run shorts-dist status
uv run shorts-dist mark-uploaded <platform> <YOUTUBE_ID> \
  --url '<POST_URL>' --source agent
```

`run` and `upload` return exit code `3` when one or more cells need attention.
The run report under `data/runs/<run_id>/report.json` contains the complete
matrix, evidence, artifacts, and recovery briefs. Exit code `3` is a partial
attention signal, not permission to abandon healthy lanes.

## Built-in platforms

| Platform | Upload | Verification | Policy disclosure |
|---|---:|---:|---|
| Instagram Reels | Playwright | Reels caption/metadata | `paid-partnership` |
| Threads | Playwright | profile/search evidence | - |
| TikTok | Playwright | Studio content list | `branded-content` |
| LinkedIn | Playwright | recent activity | - |
| Facebook | Playwright | configured videos URL | - |
| Naver Clip | Playwright | Creator Studio list | - |

Platform UIs change without notice. Selector candidates are intentionally kept
as ordered lists, and failures capture both a screenshot and full HTML for
repair. See [customization.md](.claude/skills/shorts-upload/references/customization.md)
before adding a platform or policy action.

## Safety rules

1. Never duplicate-upload without fresh platform verification.
2. Publish only the prepared `post_text` or its documented platform truncation.
3. Keep the selected video IDs fixed while recovering a partial batch.
4. Finish healthy matrix cells before waiting on one blocked platform.
5. Treat login, 2FA, CAPTCHA, copyright, account-context, and paid-promotion
   prompts as human decisions.
6. When asked to stop, terminate the pipeline and its child browser processes,
   then confirm they are gone.
7. Never commit `.env`, `data/`, browser profiles, screenshots, HTML captures,
   downloaded media, cookies, or SQLite state.

## Agent skill

The Claude Code skill lives at
[`.claude/skills/shorts-upload/SKILL.md`](.claude/skills/shorts-upload/SKILL.md)
(the original Codex copy stays at `.agents/skills/shorts-upload/`).
It keeps Claude in Chrome usage focused on failed cells and documents the
self-healing selector workflow.

## Development

```bash
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing uploader contracts.
Security guidance is in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
