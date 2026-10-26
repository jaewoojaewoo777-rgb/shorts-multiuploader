# Known failure modes

This file preserves the operational lessons from earlier uploads. The code
uploaders (`src/shorts_distributor/uploaders/*.py`) encode most of these as
step hints; use this file as the checklist when rescuing a failed cell with
agent (browser-use/computer-use) tools or when a platform UI looks surprising.

## 1. yt-dlp AV1 codec -> Instagram silent reject

**Symptom:** Instagram appears to accept the file, but the reel never appears on
the configured `INSTAGRAM_HANDLE` reels tab after 30+ minutes.

**Cause:** Instagram can silently reject AV1-in-MP4 even when other platforms
accept it.

**Fix:** The downloader prefers h264. Still check before Instagram upload:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name \
  -of default=noprint_wrappers=1:nokey=1 data/downloads/<ID>.mp4
```

Expect `h264`. If not, re-encode:

```bash
ffmpeg -y -i input.mp4 \
  -c:v libx264 -preset medium -crf 20 -profile:v high -pix_fmt yuv420p \
  -c:a aac -b:a 128k -movflags +faststart output.mp4
```

## 2. Instagram share spinner runs for minutes — the success dialog is the gate

**Symptom:** After clicking share, the loading bar/spinner can run for up to
~5 minutes. Closing the window or navigating away during this aborts the upload
(the share flow then "looks successful" but nothing is published).

**Corrected operational observation:** a successfully shared reel
appears on the reels tab within seconds. The old "wait 25 minutes before
declaring missing" rule was a misdiagnosis — the upload had actually failed,
the owner re-uploaded manually, and the next verification pass found that
manual post ~25 minutes later.

**Fix:** Wait through the spinner until the explicit success dialog
(`릴스가 공유되었습니다` 등) — the code waits while progress is visible
(hard cap 15 minutes). After success, verify immediately on the reels tab.
If the reel is missing shortly after a confirmed success dialog, recheck once
(~1-2 minutes), then treat it as a real failure and re-upload (cap 3 total
attempts, then report a video-level block).

## 3. Instagram defaults to a cropped square preview

**Symptom:** After selecting a vertical Shorts MP4, the crop screen shows a
square-looking preview and would publish as a cropped 1:1 post/reel if continued.

**Fix:** On the first Instagram crop screen, click the crop/aspect control near
the preview, then choose `Original` from the aspect choices before clicking
`Next`. The menu can expose `Original`, `1:1`, and `9:16`; use `Original` for the
downloaded YouTube Shorts files unless the user explicitly asks otherwise. Before
publishing, visually confirm the preview is the full vertical frame, not a
center-cropped square.

## 4. TikTok caption must be real typed text

**Symptom:** TikTok publishes, but Studio later shows `설명 없음` or the raw
filename instead of the intended caption. Another bad variant is a caption that
contains the intended title plus accidental workflow metadata, such as the
YouTube ID appended to the title.

**Cause:** TikTok's editor may ignore programmatic text insertion or stale draft
text. The safe native-tool behavior is to interact like a user.

**Fix with Claude in Chrome:** Focus the caption editor, select all existing
text, delete it, then type or paste the prepared caption into the active editor.
After entry, click away or press Escape to dismiss autocomplete. Before publish,
visually confirm the caption visible in the editor exactly matches the prepared
caption or the documented platform-shortened value. It must not contain the
video ID, filename, URL, batch number, or stale draft text.

## 5. TikTok leftover draft modal cascade

**Symptom:** Opening the TikTok upload page shows a leftover draft warning, and
the page behaves oddly after dismissing only one dialog.

**Correct path:** If the warning asks whether to discard the editing video,
choose the option equivalent to discard/cancel the draft. If the follow-up modal
asks whether to delete the draft, choose delete. Do not choose the later/keep
draft option; that preserves stale content and can corrupt the next publish.

## 6. TikTok "post now" confirmation

**Symptom:** After clicking post, TikTok shows an additional confirmation dialog
and the upload does not finalize.

**Fix:** If a copyright/content check dialog appears, read it carefully. If it is
the normal "continue/post now" confirmation for the intended video, click the
button equivalent to "post now". If it reports a rights, restriction, or account
problem, stop and report the exact text.

## 7. TikTok verification should not depend on caption substring

**Symptom:** The content list does not show the caption even though the post is
live.

**Fix:** Verify by today's date, newest list position, thumbnail, and title/file
identity. Caption is useful evidence, but not the sole truth signal.

## 8. Threads profile feed can hide posts

**Symptom:** After two or more same-session Threads uploads, the profile feed
shows only pinned posts and the newest item.

**Fix:** Do not re-upload based only on the visible profile feed. Use direct post
URLs, search, notifications, or the most recent publish evidence before deciding
a post is missing.

## 9. Facebook `/me/` can show only the newest post

**Symptom:** Facebook profile `/me/` appears to show just one recent reel.

**Fix:** Use `FACEBOOK_VIDEOS_URL`, configured for the target profile/page videos
tab. Treat `/me/` as weak evidence only.

## 10. Facebook Page uploads require the Page profile context

**Symptom:** Facebook reports that the reel was shared, but the configured
`FACEBOOK_VIDEOS_URL` page does not show the title. The target page may also
show a prompt like "switch to this Page to take more actions".

**Cause:** `https://www.facebook.com/reels/create/` posts under the currently
active Facebook profile. If the browser is active as the user's personal
profile, the upload can succeed outside the configured page.

**Fix:** Before uploading Facebook, open `FACEBOOK_VIDEOS_URL` and confirm the
page is actionable as the target page, not merely viewed by a personal profile.
If Facebook asks to switch profiles, switch to the configured Page first, then
start the reel upload. After posting, verify the newest Page reel tiles by
opening each Reel URL; the tile grid can hide captions.

If the new Reel opens with a generic title such as `내 릴스`, an empty
description, or any text that does not match the prepared title/caption, do not
record it as uploaded. Try the safe edit flow only if the UI clearly edits that
exact Reel. If there is no reliable edit flow, delete or move that Reel to trash
from the content-management UI, verify the bad Reel is removed or no longer
public, then re-upload with the correct text.

## 11. LinkedIn publish evidence can lag

**Symptom:** The composer closes or reports success, but the post is not
immediately obvious.

**Fix:** Verify through `LINKEDIN_RECENT_ACTIVITY_URL` in a logged-in self-view or
page admin view. The post body should normally appear within seconds; if it
doesn't, mark `check` and avoid blind retries.

## 12. Naver Clip list lazy-load timing

**Symptom:** Right after upload, the configured Naver Clip content list does not
show the new item.

**Fix:** Wait 30-60 seconds and refresh once. Match by title/caption/date and
category.

## 13. Naver Creator sidebar or login state can block upload

**Symptom:** The Naver Creator Studio Clip page loads, but the left sidebar menu
is empty, the Clip list says it cannot load, or refresh redirects to Naver login.
The expected PC `[+ 만들기]` button is not available.

**Fix:** Do not guess upload URLs or continue from an empty sidebar. Ask the user
to complete Naver login in the same Chrome profile, then reload
`NAVER_CLIP_URL`. Continue only when the left menu exposes `[+ 만들기]` and the
target channel context is visible. If the list itself lazy-loads after login,
retry once before deciding it is blocked.

## 14. Naver Clip title and category constraints

**Symptom:** Naver Clip upload refuses saving even though the video file is
uploaded, or a long YouTube title cannot fit in the title field.

**Fix:** Naver Clip requires category selection and currently shows a
24-character title counter. If the user requested title-only posting and the
original YouTube title is longer than the limit, shorten only enough to fit while
preserving the meaning, then record the exact posted title in local state. Select
both Naver categories from `.env` (`NAVER_CATEGORY_1` and `NAVER_CATEGORY_2`) and
confirm the visible values before saving.

Naver's counter uses JavaScript UTF-16 units, so an astral-plane emoji such as
`🚨` consumes two of the 24 units even though Python `len()` counts one code
point. Calculate and verify Naver title limits in UTF-16 units; otherwise the
browser silently truncates the filled value and the expected-title check fails.

**Tool note:** Naver's category dropdown can ignore Claude in Chrome
`form_input`/`ref` clicks or leave the popup open. When that happens, take a
fresh Claude in Chrome `computer` screenshot and click the visible category
option, then reopen the second category dropdown and repeat.
Do not rely on pixel clicks alone; verify the save button is enabled and the
visible file/title/category/public state is correct before saving.

## 15. Upload order reverses SNS feed

**Symptom:** A batch of multiple videos ends with the oldest YouTube Short on top
of SNS feeds.

**Cause:** YouTube Shorts listing is usually newest-first. If the agent uploads
directly in that discovery order, the newest selected video publishes first and
is pushed down by older selected videos.

**Fix:** First decide the exact `n` videos to upload, then create an ordered
batch by sorting those `n` videos by YouTube `upload_date` ascending and
`timestamp` ascending. Upload batch item `1`, then `2`, through `n`. The oldest
selected Short must be uploaded first, and the newest selected Short must be
uploaded last.

## 16. Login, 2FA, and security prompts

**Symptom:** Claude in Chrome hits login, 2FA, CAPTCHA, account recovery,
permission, or security review.

**Fix:** Stop and report the prompt. Do not enter unknown credentials or make
account security choices for the user. Continue only after the user confirms the
account is ready or completes the prompt.

## 17. Claude in Chrome file upload fails with `Not allowed`

**Symptom:** A platform file picker opens or the page exposes a valid
`input[type=file]`, but Claude in Chrome `file_upload` fails with `Not allowed`
or a similar permission error.

**Cause:** The Claude in Chrome extension is installed and connected, but Chrome
has not granted the extension file URL access for that profile.

**Fix:** Ask the user to open `chrome://extensions` in the Chrome profile that
holds the logged-in SNS session, open the Claude extension details, and enable
`Allow access to file URLs` (`파일 URL에 대한 액세스 허용`). Retry the same
`file_upload` after the extension reconnects. Claude cannot drive the native
macOS file picker; the user may select the file manually instead. Do not mark
the upload complete unless the post URL or platform success evidence is
verified.

## 18. Threads/TikTok accessibility tree can be empty

**Symptom:** Chrome is visibly on Threads or TikTok, but Claude in Chrome
`read_page` returns an empty or truncated tree, and tab control can become
unstable.

**Fix:** Prefer a fresh tab from `tabs_create_mcp` in the same connected Chrome
profile. Avoid full-page `read_page` calls on these heavy pages; use `find`,
`read_page` scoped with `ref_id`, `computer` screenshots, or `file_upload`. If
file upload is still blocked by the extension file URL permission, stop and ask
the user to enable that permission before retrying.

## 19. LinkedIn composer body ignores ordinary text entry

**Symptom:** LinkedIn's post composer opens with the correct video attached and
the correct account/audience visible, but the body placeholder remains even
after `form_input`, `computer` typing, clipboard paste, or page-level DOM
mutation.

**Cause:** LinkedIn can render the visible rich-text composer through an
isolated/iframe-backed editor surface. Page-level selectors may show a textbox
in snapshots while ordinary DOM evaluation cannot reach the live editor node.
The current redesign can place the Quill `.ql-editor[contenteditable=true]`
inside LinkedIn's shadow DOM; ordinary `form_input`, `computer` typing, and
clipboard paste may focus the placeholder without inserting text.

**Fix:** Do not click `Update` while the visible title/body is empty. First try
an iframe-scoped Playwright locator or Claude in Chrome `find` for the
`contenteditable` textbox. If that does not expose the live field, use Claude
in Chrome `computer` to click the visible editor and type, then verify the title is visible in the modal
before publishing. If the text still cannot be made visible, leave the draft
unpublished and treat LinkedIn as blocked rather than posting a video-only item.
When the editor is confirmed in shadow DOM and normal native typing still fails,
Chrome CDP input events (Playwright code path only) can rescue the flow: click the editor's visible text
start coordinate and send `Input.insertText` with the prepared title. This is
only acceptable if the text visibly appears in the composer before `Update` is
clicked.

## 20. Naver Clip file selection returns to list without upload

**Symptom:** Naver Creator Studio shows the correct target channel and the
`클립 업로드` modal opens, but after selecting a valid MP4 the modal closes and
the Clip content list reappears with no upload progress, no error toast, and no
new draft row. Retrying through macOS file selection, absolute-path selection, or
Claude in Chrome `file_upload` produces the same result.

**Additional diagnosis:** If the repository-local Naver Chrome profile was just
created, Chrome can show a first-run/default-browser dialog over Creator Studio
right after file selection. The dialog may make the upload look like it returned
to the content list. Dismiss the dialog without accepting default-browser or
usage-stat changes, then retry from `클립 업로드`.

Claude in Chrome only controls the Chrome profile where its extension is
installed. If the Naver login lives in the repo-local profile but the extension
lives in the default profile, Claude in Chrome operates a different session than
the pipeline used.

**Fix:** Treat this as a Naver upload UI blocker, not a completed or duplicate
upload. Do not record local state. Before retrying:

1. Use the exact Chrome profile that owns both the Naver login session and the
   Claude in Chrome extension.
2. Launch it with `--no-first-run --no-default-browser-check` so Chrome does not
   show the first-run/default-browser prompt during file selection.
3. Confirm with `tabs_context_mcp` that Claude in Chrome is attached to that
   profile and that the visible account matches `.env`.
4. Confirm the Claude extension has file URL access enabled in that profile.

Then confirm the upload modal still exposes `파일 선택` and retry once with
Claude in Chrome `file_upload` (or ask the user to pick the file manually). If it still returns to the list without
progress or error, stop and report the exact behavior so the user can verify
Naver Creator Studio's upload availability in that profile.

## 21. Right video but wrong or missing title after publish

**Symptom:** The video itself is correct, but the live post title/caption is
wrong. Examples include the prepared title plus an accidental YouTube ID suffix,
raw filename, copied URL, batch/debug text, stale draft caption, empty body, or a
generic platform label such as `내 릴스`.

**Cause:** The agent mixed operational metadata with user-visible text, pasted
into the wrong field, trusted a stale draft/editor state, or treated a platform
success screen as verification without checking the live title/caption.

**Prevention:** Before every publish click, compare the visible user-facing text
against `post_text` character-for-character. When `POST_TEXT_MODE=title` or the
user requested title-only posting, compare against `title_text`. The only allowed
difference is an intentional platform-limit shortening that is noted before
publishing and recorded afterward. Never copy a work note that includes both the
title and video ID into a platform composer.

**Fix:** Do not run `shorts-dist mark-uploaded` for a right-video/wrong-text
post. If the platform exposes a reliable edit flow for that exact post, correct
the title/caption and verify the live post again. If editing is unavailable or
ambiguous, delete or move the bad post to trash only after the UI clearly targets
that exact post, verify it is gone or non-public, then re-upload from the
prepared MP4 with the correct text and verify-record the new post.

## 22. Playwright 가 프로필의 로그인 쿠키를 전부 삭제 (mock keychain)

**Symptom:** 코드 업로드/doctor 를 한 번 실행한 뒤 해당 Chrome 프로필의 모든
로그인이 풀려 있다. 쿠키 DB(`Default/Cookies`)가 0 행이 된다.

**Cause:** Playwright 는 기본 인자로 `--use-mock-keychain` 을 넣는다. 실제
Keychain 키로 암호화된 기존 쿠키를 mock keychain 으로는 복호화할 수 없어 Chrome
이 로드 시점에 쿠키를 전부 삭제한다(실측: 175개 → 0개).

**Fix:** `uploaders/base.py` 의 `platform_context` 가
`ignore_default_args=["--use-mock-keychain"]` 을 항상 전달한다. 이 코드를 우회해
Playwright 로 실프로필을 직접 열지 말 것. 세션이 이미 날아간 프로필은
`uv run shorts-dist login <platform>` 으로 재로그인한다.

## 23. 프로필 이중 실행 (ProcessSingleton)

**Symptom:** run/upload/doctor 가 `프로필이 이미 다른 Chrome 창에서 사용 중`
(profile-in-use) blocker 로 즉시 실패한다.

**Cause:** 같은 `--user-data-dir` 프로필로 Chrome 창이 이미 떠 있다. macOS 는
프로필당 하나의 Chrome 프로세스만 허용한다(SingletonLock).

**Fix:** 그 프로필로 열린 Chrome 창을 닫고 재실행한다. 에이전트 폴백 작업과
코드 러너가 같은 프로필을 동시에 쓰지 않도록 한 쪽을 끝내고 시작할 것.

## 24. Threads 동영상 게시는 비동기 — 완료 증거 전에 닫으면 유실

**Symptom:** 게시 버튼 클릭까지 정상, 러너/에이전트는 성공으로 기록했지만 실제
프로필·미디어 탭·검색 어디에도 게시물이 없다.

**Cause:** Threads 동영상 게시는 클릭 후 백그라운드 업로드로 진행된다. 배경
피드의 `a[href*="/post/"]` 링크(남의 게시물)를 성공 토스트로 오인해 즉시 성공
처리하고 페이지를 닫으면 진행 중이던 게시가 중단된다(실측: 2건 유실). 이때
기록된 URL 은 타인 게시물 URL 이라 검증까지 오염된다.

**Fix:** 완료 증거는 (1) 클릭 전 기준선에 없던 **본인 핸들**의 새 `/post/` 링크,
또는 (2) 본인 프로필/미디어 탭에서의 캡션 직접 대조뿐이다. `게시 중` 표시가
보이는 동안은 페이지를 닫지 않는다(최대 5분 대기). 코드는
`uploaders/threads.py` confirm 단계에 반영돼 있다. Instagram 의 공유 로딩
(failure-mode #2)과 같은 부류 — **어떤 플랫폼이든 업로드/게시 진행 표시가 있는
동안 페이지·창을 닫으면 안 된다.**

## 25. Instagram 알림 설정 모달이 만들기 버튼을 가림

**Symptom:** 홈 진입 직후 `알림 설정` 다이얼로그가 뜨고, composer 단계의
`만들기` 클릭이 20초 뒤 타임아웃된다. 화면에는 `설정`과 `나중에 하기` 버튼이
보인다.

**Fix:** 업로드 흐름을 시작하기 전에 정확 텍스트 `나중에 하기`(영문 `Not Now`)
버튼을 선택적으로 눌러 모달을 닫는다. 알림 권한을 대신 결정하는 `설정`은 누르지
않는다. 코드는 `uploaders/instagram.py`의 open 단계에 반영돼 있다.

## 26. LinkedIn 미디어 편집기 `다음`과 배경 캐러셀 버튼 충돌

**Symptom:** 동영상 파일은 정상 첨부되고 `에디터` 모달 우하단에 파란 `다음`
버튼이 보이지만, 러너는 caption 단계에서 에디터를 찾지 못하고 타임아웃된다.

**Cause:** 배경 피드 캐러셀에도 `aria-label="다음"` 버튼이 있다. 전역 role
셀렉터가 가려진 배경 버튼을 먼저 반환하면 클릭이 오버레이에 막히고, 선택적 클릭이
이를 편집 화면 생략으로 오인한다.

**Fix:** 미디어 편집기의 정확 텍스트 `button:text-is("다음")`/`Next` 후보를
배경 role 후보보다 먼저 선택한다. 다음 버튼이 없을 때는 본문 에디터가 실제로
노출됐는지 확인하고, 둘 다 없으면 attach 단계에서 실패시킨다.

## 27. LinkedIn 전역 파일 입력·본문 선택기가 배경 요소를 잡음

**Symptom:** `시작할 파일 선택` 모달이 그대로 보이는데 러너는 attach 단계를
통과하고 caption 단계에서 클릭 타임아웃된다. 또는 파일을 지정했는데도 미디어
미리보기가 나타나지 않는다.

**Cause:** 신형 LinkedIn은 미디어 흐름을 shadow DOM 다이얼로그로 렌더링한다.
페이지 전역의 첫 `input[type=file]` 또는 `div[contenteditable=true]`를 쓰면
가려진 다른 입력이나 배경 메시지/피드 에디터를 선택할 수 있다.

**Fix:** 동영상 퀵 버튼 직후 보이는 `role=dialog`/`aria-modal=true` 컨테이너를
잡고, 그 다이얼로그 내부에서만 video/mp4 파일 입력, `다음`, 본문 에디터, `게시`
버튼을 찾는다. 본문이 준비된 값과 정확히 일치하기 전에는 게시하지 않는다.

## 28. LinkedIn 숨은 파일 입력 직접 설정이 첨부를 시작하지 않음

**Symptom:** `시작할 파일 선택` 에디터에서 파일 입력에 `set_input_files`를
실행해도 화면이 그대로이고, `컴퓨터에서 업로드` 버튼과 비활성 `다음`만 남는다.
이후 본문 에디터를 찾지 못해 attach 단계에서 실패한다.

**Cause:** 2026-07 신형 에디터는 shadow DOM의 숨은 파일 입력을 직접 설정하는
경로에서 첨부 상태를 갱신하지 않을 수 있다. 화면의 업로드 버튼이 여는 file
chooser 경로를 거쳐야 미디어 처리와 `다음` 활성화가 시작된다.

**Fix:** 다이얼로그 안의 정확 텍스트 `컴퓨터에서 업로드`/`Upload from computer`
버튼을 우선 찾고 `expect_file_chooser`로 파일을 지정한다. 버튼이 없는 구 UI에서만
기존 다이얼로그 내부 `input[type=file]` 직접 설정으로 폴백한다.

## 29. 한 플랫폼 장애가 나머지 배치를 불필요하게 막음

**Symptom:** 첫 플랫폼이 로그인 만료, profile-in-use, UI 변경 또는 검증 실패로
막힌 뒤, 아직 실행 가능한 다른 플랫폼과 영상 셀이 있는데도 전체 작업이 사용자
조치 대기 상태로 멈춘다.

**Cause:** 배치 exit code 3이나 첫 실패 브리프를 전체 배치 중단으로 잘못 해석했다.
실패 상태는 플랫폼×영상 셀 단위이며, 다른 플랫폼 레인은 독립적으로 처리할 수 있다.

**Fix:** 계획 단계에서 선택 영상 ID를 고정한다. 실패한 플랫폼 레인만
`hard-blocked` 또는 에이전트 후속 작업으로 남기고, 정상 플랫폼을 `--platforms`로
제한해 같은 영상 ID를 모두 먼저 처리한다. 실행 가능한 매트릭스 셀이 남아 있는 동안
로그인이나 수동 복구를 기다리지 않는다. 러너는 플랫폼 레인을 순회하며 한 레인의
blocker가 다음 레인으로 전파되지 않도록 유지한다.

## 30. TikTok 콘텐츠 공개 토글에서 `aria-label`이 사라짐

**Symptom:** 광고 본문과 영상 첨부는 정상인데 `branded-content` 단계에서
`게시물 콘텐츠 공개` 토글을 찾지 못해 중단된다. 화면 아래에는 해당 설정이 실제로
존재한다.

**Cause:** 신형 TikTok Studio는 라벨 없는 `input[role="switch"]`를
`data-e2e="disclose_content_container"` 안에 렌더링한다. input 자체는 투명해
가시성 검사에서 제외되고, 실제 클릭 가능한 바깥 `.Switch__content`에
`aria-checked`가 있다. 텍스트 기반 `aria-label` 셀렉터로는 찾을 수 없다.

**Fix:** 컨테이너를 먼저 스코핑한
`[data-e2e="disclose_content_container"] .Switch__content[aria-checked]`를
최우선 후보로 사용하고, 내부 input은 폴백으로 둔다. 토글 후
`aria-checked`/`is_checked()`가 참인지 확인한 다음에만 브랜드 콘텐츠 선택과 게시
단계로 진행한다. 설정 영역이 첫 화면 아래의 내부 스크롤에 있을 수 있으므로
컨테이너와 클릭 래퍼를 `attached` 상태로 찾는다. 고정 미리보기 때문에 일반
스크롤이나 stable 대기가 끝나지 않는 UI에서는 고유 컨테이너 안의 래퍼에 DOM
click을 보내되, 직후 `aria-checked=true`가 아니면 실패시킨다.
한국어 선택지는 `브랜드 콘텐츠`가 아니라 `브랜디드 콘텐츠`로 표시될 수 있고,
텍스트가 label 바깥의 형제 span이므로 `.title-line:has(span:text-is(...)) label`로
체크박스를 찾은 뒤 실제 `is_checked()`까지 확인한다.

## 31. A policy-required disclosure control is absent

**Symptom:** Media and text are ready, but the uploader cannot find or verify a
paid-partnership or branded-content control required by the matched content
policy.

**Fix:** Fail closed before publishing. Do not infer a partner account and do not
silently downgrade to text-only disclosure. Confirm the correct account type and
platform eligibility, inspect the current UI, update selector candidates, and
verify the enabled state. If the creator intentionally wants a different policy,
change the private content-policy file and run a new dry run rather than adding a
creator-specific bypass to source code.

## 22. AI-generated label toggle missing or unconfirmed

**Symptom:** A cell ends `blocked` at step `ai-label` with "AI 표시 토글 ..." in
the error. Nothing was published; the composer or draft was abandoned.

**Cause:** The content policy requires the `ai-generated` disclosure, but the
uploader could not find exactly one AI label switch next to the expected label
text, or could not read/confirm its checked state (`aria-checked`,
`aria-pressed`, or checkbox `checked`).

**Fix:** Stop and tell the user. Do not publish without the label. Inspect the
failure PNG/HTML for the current label text and switch markup, add the new label
text to that uploader's `_AI_LABELS` (keep old ones), and retest with a single
cell. Known labels (verified 2026-10-24): Instagram `AI 레이블 추가` shown directly in
the caption panel (`input[role=switch]` with `aria-checked`; the live reel then
shows `AI 콘텐츠` under the handle); TikTok `AI 생성 콘텐츠`
(`[data-e2e="aigc_container"]`) under the settings card's `펼치기`; Naver Clip Creators `AI 활용 설정` in 상세 정보
(right above the `광고 · 협찬 설정` toggle, which must stay untouched).
Claude in Chrome cannot open `clipcreators.naver.com`; Naver UI checks are the
user's.

## 23. Instagram reels notice and caption label (2026-10)

**Symptom:** The crop step times out with "이제 동영상 게시물이 릴스로 공유됩니다"
covering the composer, or the caption step cannot find the editor.

**Fix:** The reels notice is a separate `role=dialog`; dismiss its `확인` button
unscoped before clicking the crop toggle. The caption editor is now
`div[role=textbox][aria-label="캡션 추가..."]` (Lexical editor). A create click
can occasionally leave the profile page instead of opening the composer; the
uploader retries once from the home feed.

## 24. Naver Clip Creators upload form (2026-10)

**Symptom:** Upload stops at the menu, title, or AI step on
`clipcreators.naver.com`.

**Facts (verified 2026-10-25):** `+ 업로드` opens a menu whose
`[role=menuitem]` `동영상 업로드` leads to `/web/draft/<id>`. Attaching a file
creates a private 초안 immediately. The form has no title field: the required
`설명` textarea (`#content-description`, 300 chars) receives the 24-unit title
rule output (for `A | B` titles, the last segment that fits). Categories are
`1차 카테고리` / `2차 카테고리` buttons. `AI 활용 설정` is
`button[role=switch][aria-label="AI 활용 설정"]`; the ⓘ tooltip next to the label
has `aria-pressed` and must never be treated as the switch. Submit with
`button[type=submit]` `등록`; `임시저장` only saves a draft. The list then shows the
description as the clip title with status `공개`.

