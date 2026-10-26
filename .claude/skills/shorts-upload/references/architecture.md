# Architecture and invariants

## The matrix is the unit of work

A batch is `selected videos x active platforms`. Each cell owns its upload,
verification, evidence, and retry state. Platform lanes preserve chronological
order, while failures stay inside the lane that produced them. Never represent a
partial platform failure as a failed batch if other cells can still run.

## Pipeline boundaries

1. **Discovery** asks YouTube for a bounded lookback and removes locally complete
   videos. It may skip suspected ads according to batch configuration.
2. **Preparation** downloads once, builds metadata, applies creator-owned content
   policies, and normalizes media to H.264.
3. **Execution** creates one `Job` per allowed cell. Uploaders may shorten text
   only according to their documented platform limits.
4. **Verification** looks at a creator-owned platform surface and returns
   `verified`, `missing`, or `inconclusive` with evidence.
5. **State** records an upload before or during verification to prevent an
   ambiguous publish from being repeated.
6. **Recovery** receives one cell brief. It does not rediscover or reinterpret
   the batch.

Keep these boundaries explicit. Downloaders should not manipulate browser UI;
uploaders should not choose the batch; agent recovery should not silently change
prepared text or policy.

## Status model

| Status | Meaning | Retry behavior |
|---|---|---|
| `planned` | Ready but not attempted | May upload |
| `published` | Publish evidence exists, final verification incomplete | Verify; do not upload |
| `pending-verify` | Expected indexing delay or ambiguous completion | Recheck; do not upload |
| `verified` | Direct platform evidence matches | Closed |
| `already` | Local state says the cell was handled | Verify only when auditing |
| `skipped-ad` | Batch ad filter excluded the video | Closed for this run |
| `skipped-policy` | A matching rule excludes this platform | Closed while policy applies |
| `failed` | A cell step failed without common lane blocker | Recover or repair |
| `blocked` | Login, profile, permission, or shared lane blocker | User action or later retry |
| `agent-required` | No code uploader exists | Manual/agent path |

The dangerous transition is `failed -> upload again`. It is allowed only after
fresh verification returns `missing`. `inconclusive` never authorizes a retry.

## Deduplication and evidence

SQLite state is a guard, not the sole truth. Platform verification is the truth
for deciding whether a retry is safe. Record the specific post URL when possible;
never substitute a homepage or profile root. A successful click or vanished
composer is weaker evidence than a success dialog, content-list row, matching
post body, direct URL, or thumbnail.

## Content policy layer

The core source contains no sponsor or creator names. The untracked
`config/content-policies.json` transforms prepared metadata into:

- final `post_text`
- an optional allowed-platform set
- per-platform disclosure actions
- matched rule names for reports

Rules are evaluated in file order. Allowed-platform sets are intersected. Later
matching text modes replace the base text, while prepend/append transformations
apply in order. Disclosure values are combined. Avoid overlapping rules unless
that composition is intentional.

Policies are applied after explicit ID selection, so a direct retry cannot evade
campaign constraints. Unsupported disclosure values fail policy validation
before upload. Add and test uploader behavior before introducing a new action.

## Browser profile lifecycle

Persistent profiles contain credentials. They live under `data/profiles/`, are
never copied into an agent artifact, and must not be opened by two Chrome
processes simultaneously. Use `shared` for setup simplicity or `per-platform`
for stronger failure isolation. A stop request owns cleanup of the pipeline and
its browser descendants, not unrelated user browser processes.

## UI automation invariants

- Scope locators to the active dialog or panel.
- Keep multiple selector candidates because old and new UIs coexist.
- Prefer exact text, roles, stable attributes, and state verification.
- Treat virtualized dropdowns and shadow DOM as explicit cases.
- Re-read text after programmatic insertion.
- Wait while upload/progress indicators remain visible.
- Capture a screenshot and full HTML before escalating a failure.
- Stop on account, security, legal, copyright, or payment decisions.

## Runtime artifacts

`data/runs/<run_id>/report.json` is the handoff contract. It includes selected
media, final text, matrix cells, evidence, artifact paths, and agent briefs.
Everything under `data/` may contain private account data and must remain local.
