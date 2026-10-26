# Customization guide

## Configure a new creator

1. Copy `.env.example` to `.env`.
2. Set `YOUTUBE_HANDLE`, `TARGET_PLATFORMS`, and every verification URL used by
   those platforms.
3. Choose `PROFILE_STRATEGY=shared` for the easiest login flow or
   `per-platform` for isolation.
4. Log in once with `shorts-dist login <platform>` and run `doctor`.
5. Run a one-video dry run, inspect final text and policy decisions, then perform
   a supervised upload.

Never put real account values into defaults, tests, screenshots, or docs.

## Add channel or campaign rules

Copy `config/content-policies.example.json` to the ignored
`config/content-policies.json`. Each rule needs a unique name and at least one
case-insensitive marker under `match.any` or `match.all`.

```json
{
  "version": 1,
  "rules": [
    {
      "name": "campaign-example",
      "match": {"any": ["PRIVATE_CAMPAIGN_MARKER"]},
      "platforms": ["instagram", "tiktok"],
      "post_text": {"mode": "description", "prepend": "#ad\n\n"},
      "disclosures": {
        "instagram": ["paid-partnership"],
        "tiktok": ["branded-content"]
      }
    }
  ]
}
```

Supported text modes are `default`, `caption`, `title`, and `description`.
Commit only sanitized examples. The live file may reveal commercial agreements
or review codes and is intentionally ignored.

## Add a disclosure action

1. Choose a stable lowercase action name such as `paid-partnership`.
2. Read it from `job.disclosures` in the relevant uploader.
3. Enable the platform control before the final publish click.
4. Verify the control's checked state, not only the click.
5. Fail closed if the required control is absent or cannot be confirmed.
6. Add policy and uploader tests plus a sanitized example.

Implemented actions: Instagram `paid-partnership`, `ai-generated`; TikTok
`branded-content`, `ai-generated`; Naver `ai-generated`. `ai-generated` uses the
shared fail-closed helper `ensure_ai_label` in `uploaders/base.py`. A rule with
`"match": {"always": true}` applies to every video (for channel-wide
disclosures such as AI-generated content).

Do not infer legal requirements from hashtags. The policy file explicitly
connects creator metadata to a platform action.

## Add a platform

Add a `PlatformSpec` in `platforms.py`, configuration fields in `config.py`, and
an uploader module under `uploaders/`. Register the uploader in
`uploaders/__init__.py`.

The module contract is:

```python
LOGIN_URL = "https://example.invalid/login"

def upload(page, job, steps, cfg) -> Outcome:
    ...

def verify(page, job, steps, cfg) -> VerifyOutcome:
    ...
```

Use `steps.step(...)` around open, account check, attachment, text entry,
platform options, publish, and confirmation. A `StepFailure` should name the
failed step, preserve artifacts, say whether it blocks the lane, and include a
useful recovery hint.

Before considering the uploader complete, define:

- exact upload and creator verification URLs
- logged-in and correct-account evidence
- media constraints and input behavior
- title/body limits and deterministic shortening
- privacy, audience, category, and disclosure defaults
- strongest acceptable publish and verification evidence
- blockers that require a human decision
- selector candidates for at least the observed UI languages
- a test proving this platform's blocker does not stop another lane

If an ID has no registered uploader, the runner emits `agent-required`. That is a
useful prototyping state, but document the UI and verification procedure before
using it live.

## Selector maintenance

Add candidates rather than replacing known working selectors. Scope within the
active composer. Broad `:has-text()` ancestors, background carousel controls,
hidden file inputs, virtualized options, and animated preview panels are common
sources of false clicks. Always assert the resulting UI state and preserve a
sanitized failure-mode note for future maintainers.

## Test strategy

- Unit-test policy matching and validation with fictitious markers.
- Test text shortening with Unicode and platform-specific counters.
- Mock platform contexts to verify lane isolation and state transitions.
- Use a private test account for live smoke tests; never automate real publishing
  in public CI.
- Run the secret scan described in `SECURITY.md` before publishing a fork.
