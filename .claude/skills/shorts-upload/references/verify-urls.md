# Per-platform verification URLs

Use these URLs with Claude in Chrome after upload. The logged-in account
must be the user's account configured in `.env`; do not assume maintainer
handles or profile URLs.

## Environment-driven URLs

Set `TARGET_PLATFORMS` in `.env` to choose the active upload targets, then set
the matching verification values before relying on verification:

| Variable | Purpose |
|---|---|
| `INSTAGRAM_HANDLE` | Instagram reels verification |
| `THREADS_HANDLE` | Threads profile verification; defaults to `INSTAGRAM_HANDLE` |
| `TIKTOK_HANDLE` | Optional TikTok profile fallback after Studio verification |
| `LINKEDIN_RECENT_ACTIVITY_URL` | Logged-in recent activity URL for the profile/page |
| `FACEBOOK_VIDEOS_URL` | Videos tab URL for the profile/page |
| `NAVER_CHANNEL_SLUG` | Builds Naver dashboard/content URLs |
| `NAVER_CLIP_URL` | Optional explicit Naver Clip content-list URL |

For custom platform IDs, add a custom verification URL variable and document it
in `platform-guides.md` before live upload.

## Naver Clip Studio

- URL: `NAVER_CLIP_URL`, or
  `https://creator.tv.naver.com/channel/<NAVER_CHANNEL_SLUG>/content/clip`
- Look for the clip list table with registered date, view count, and category.
- A new clip may appear after a 30-60 second lazy-load delay.
- If Creator Studio redirects to Naver login or the left sidebar is empty, treat
  upload as blocked until the user logs in again in the same Chrome profile.

## TikTok Studio

- URL: `https://www.tiktok.com/tiktokstudio/content`
- Verify by upload date/list position and thumbnail.
- Do not rely on caption substring alone; Studio can display `설명 없음` or a
  filename for a live post.
- If Studio is inconclusive and `TIKTOK_HANDLE` is set, use
  `https://www.tiktok.com/@<TIKTOK_HANDLE>` only as a secondary public-profile
  check.

## LinkedIn

- URL: `LINKEDIN_RECENT_ACTIVITY_URL`
- Use a logged-in self-view or page admin view that shows full recent post
  bodies. Authwall/non-logged-in public views are unreliable.

## Threads

- URL: `https://www.threads.com/@<THREADS_HANDLE>`
- Profile feeds can show pinned posts and only the newest non-pinned item. Use
  Threads search or direct post URLs before deciding a post is missing.

## Facebook

- URL: `FACEBOOK_VIDEOS_URL`
- Prefer a stable videos tab for the target profile/page. Do not use `/me/` as
  proof for older posts; it can show only the newest item.
- For Pages, switch into the Page profile before upload. The Page reels grid can
  hide captions, so open the newest Reel URLs and match the title inside each
  Reel page before marking verified.

## Instagram

- URL: `https://www.instagram.com/<INSTAGRAM_HANDLE>/reels/`
- A successfully shared reel appears on the reels tab within seconds. If it is
  missing right after a confirmed success dialog, recheck once (~1-2 minutes),
  then treat the upload as failed (owner-verified; the old 25-minute figure was
  a misdiagnosis of a failed upload that was manually re-uploaded).
- Open candidate reels and match the visible caption/title or page metadata
  against that video's unique caption/title.
- If the profile feed's newest reel is older than the upload time, treat the feed
  as stale/capped and verify via direct reel URLs or search before re-uploading.
