"""
Playwright 기반 코드 우선 업로드 공통 인프라.

설계 원칙:
  - 업로드는 코드(Playwright + 로그인된 `data/profiles/<platform>` Chrome 프로필)가 기본.
  - 실패한 단계는 스크린샷/HTML 아티팩트를 남기고 StepFailure 로 승격해서,
    에이전트(browser-use/computer-use)가 정확히 그 지점부터 이어받게 한다.
  - 셀렉터는 한국어/영어 UI, 리뉴얼 드리프트에 대비해 항상 다중 후보 리스트로 둔다.
  - 키 이벤트 기반 자동완성(멘션/해시태그 팝업)을 피하려고 텍스트는 insert_text 우선,
    입력 후 화면 텍스트를 기대값과 재대조한다(post_text 정확성 하드 룰).
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

DEFAULT_TIMEOUT_MS = 20_000
POLL_MS = 200

# 로그인 세션 판별용 쿠키. doctor/login 이 여기에 의존한다.
SESSION_COOKIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "instagram": ("sessionid", ("instagram.com",)),
    "threads": ("sessionid", ("threads.com", "threads.net")),
    "tiktok": ("sessionid", ("tiktok.com",)),
    "linkedin": ("li_at", ("linkedin.com",)),
    "facebook": ("c_user", ("facebook.com",)),
    "naver": ("NID_AUT", ("naver.com",)),
}


class StepFailure(Exception):
    """업로드/검증 단계 실패. 에이전트 인계에 필요한 컨텍스트를 담는다."""

    def __init__(
        self,
        step: str,
        message: str,
        *,
        hint: str = "",
        artifacts: Sequence[str] | None = None,
        page_url: str | None = None,
        blocker: bool = False,
    ) -> None:
        super().__init__(f"[{step}] {message}")
        self.step = step
        self.message = message
        self.hint = hint
        self.artifacts = list(artifacts or [])
        self.page_url = page_url
        # blocker=True 는 로그인/2FA/보안점검처럼 같은 플랫폼의 나머지 셀도
        # 똑같이 막히는 상태. 러너가 해당 레인을 통째로 중단한다.
        self.blocker = blocker


@dataclass
class Job:
    """업로드 셀 하나의 입력값. runner 가 prepare 결과에서 만든다."""

    platform: str
    youtube_id: str
    file_path: Path
    post_text: str
    title_text: str
    upload_date: str | None = None
    timestamp: int | None = None
    disclosures: tuple[str, ...] = ()


@dataclass
class Outcome:
    """업로드 시도 결과."""

    status: str  # published | already | failed | blocked
    post_url: str | None = None
    posted_text: str | None = None  # 실제 게시한 텍스트(플랫폼 글자수 제한 축약 반영)
    evidence: str = ""
    step: str | None = None
    error: str | None = None
    hint: str = ""
    artifacts: list[str] = field(default_factory=list)


@dataclass
class VerifyOutcome:
    """게시 후 코드 검증 결과. inconclusive 는 에이전트 확인 지점."""

    status: str  # verified | missing | inconclusive
    url: str | None = None
    evidence: str = ""
    artifacts: list[str] = field(default_factory=list)


def norm_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _exc_brief(exc: BaseException, limit: int = 300) -> str:
    text = str(exc).strip().splitlines()
    return (text[0] if text else exc.__class__.__name__)[:limit]


class Steps:
    """단계 실행기. 실패 시 스크린샷/HTML 을 남기고 StepFailure 로 승격."""

    def __init__(self, page: Page, artifacts_dir: Path, tag: str) -> None:
        self.page = page
        self.artifacts_dir = artifacts_dir
        self.tag = re.sub(r"[^A-Za-z0-9_-]", "_", tag)
        self.current: str | None = None

    def capture(self, name: str) -> list[str]:
        artifacts: list[str] = []
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        base = self.artifacts_dir / f"{self.tag}-{name}"
        try:
            path = base.with_suffix(".png")
            self.page.screenshot(path=str(path))
            artifacts.append(str(path))
        except Exception:
            pass
        try:
            path = base.with_suffix(".html")
            # 잘라 저장하면 정작 문제 영역(대개 문서 후반의 다이얼로그)이 유실된다.
            path.write_text(self.page.content(), encoding="utf-8")
            artifacts.append(str(path))
        except Exception:
            pass
        return artifacts

    @contextmanager
    def step(self, name: str, hint: str = "") -> Iterator[None]:
        self.current = name
        try:
            yield
        except StepFailure as failure:
            if not failure.artifacts:
                failure.artifacts = self.capture(name)
            if not failure.page_url:
                failure.page_url = self._safe_url()
            raise
        except Exception as exc:
            raise StepFailure(
                name,
                _exc_brief(exc),
                hint=hint,
                artifacts=self.capture(name),
                page_url=self._safe_url(),
            ) from exc

    def _safe_url(self) -> str | None:
        try:
            return self.page.url
        except Exception:
            return None


def profile_dir_for(cfg, platform: str) -> Path:
    """PROFILE_STRATEGY 에 따른 프로필 경로. shared 면 모든 플랫폼이 한 프로필."""
    if cfg.profile_strategy == "shared":
        return cfg.shared_profile_dir
    return cfg.profiles_dir / platform


@contextmanager
def platform_context(
    cfg,
    platform: str,
    *,
    headless: bool | None = None,
    require_profile: bool = True,
) -> Iterator[BrowserContext]:
    """플랫폼(전략에 따라 공유) 로그인 Chrome 프로필로 persistent context 를 연다.

    require_profile=False 는 `shorts-dist login` 처럼 새 프로필 생성이 목적일 때만.
    """
    profile_dir = profile_dir_for(cfg, platform)
    if require_profile and not (profile_dir / "Local State").exists():
        raise StepFailure(
            "profile",
            f"로그인된 Chrome 프로필이 없습니다: {profile_dir}",
            hint=f"`uv run shorts-dist login {platform}` 으로 프로필을 만들고 로그인하세요.",
            blocker=True,
        )
    profile_dir.mkdir(parents=True, exist_ok=True)
    effective_headless = cfg.uploader_headless if headless is None else headless
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                str(profile_dir),
                channel=cfg.chrome_channel,
                headless=effective_headless,
                viewport={"width": 1400, "height": 950},
                slow_mo=cfg.uploader_slowmo_ms,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-session-crashed-bubble",
                    "--hide-crash-restore-bubble",
                ],
                # --use-mock-keychain 이 기본 인자로 들어가면 실제 Keychain 키로
                # 암호화된 기존 쿠키를 복호화하지 못해 Chrome 이 세션 쿠키를
                # 전부 삭제한다(실측: 175개 → 0개). 반드시 제외할 것.
                ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            )
        except Exception as exc:
            message = str(exc)
            if "ProcessSingleton" in message or "SingletonLock" in message or "already in use" in message:
                raise StepFailure(
                    "launch",
                    f"프로필이 이미 다른 Chrome 창에서 사용 중입니다: {profile_dir}",
                    hint="해당 프로필로 열린 Chrome 창을 닫은 뒤 다시 실행하세요.",
                    blocker=True,
                ) from exc
            raise StepFailure(
                "launch",
                _exc_brief(exc),
                hint=f"Google Chrome 설치 상태와 CHROME_CHANNEL={cfg.chrome_channel} 값을 확인하세요.",
                blocker=True,
            ) from exc
        ctx.set_default_timeout(DEFAULT_TIMEOUT_MS)
        ctx.set_default_navigation_timeout(60_000)  # LinkedIn 등 무거운 첫 로드 대비
        try:
            yield ctx
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def login_cookie_present(cookies: list[dict], platform: str) -> bool | None:
    """쿠키 목록에서 세션 쿠키 존재 여부. None 은 판별 규칙이 없는 커스텀 플랫폼."""
    rule = SESSION_COOKIES.get(platform)
    if rule is None:
        return None
    name, domains = rule
    for cookie in cookies:
        if (
            cookie.get("name") == name
            and cookie.get("value")
            and any(domain in cookie.get("domain", "") for domain in domains)
        ):
            return True
    return False


def has_login_cookie(ctx: BrowserContext, platform: str) -> bool | None:
    return login_cookie_present(ctx.cookies(), platform)


def first_match(
    page: Page,
    selectors: Sequence[str],
    *,
    scope: Locator | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    state: str = "visible",
) -> Locator:
    """여러 후보 셀렉터 중 먼저 나타나는 것을 반환. ko/en·리뉴얼 대비 다중 후보용.

    주의: `.first`만 검사하면 반응형 레이아웃용 숨은 중복 노드에 갇혀 화면에
    보이는 요소를 영영 못 찾는다(실측: IG 공유하기 버튼) — 매치를 순회하며
    가시적인 후보를 고른다.
    """
    root = scope if scope is not None else page
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for selector in selectors:
            locator = root.locator(selector)
            try:
                count = min(locator.count(), 10)
            except Exception:
                continue
            if count == 0:
                continue
            if state == "attached":
                return locator.first
            for i in range(count):
                candidate = locator.nth(i)
                try:
                    if candidate.is_visible():
                        return candidate
                except Exception:
                    continue
        if time.monotonic() >= deadline:
            raise PlaywrightTimeout(f"셀렉터 후보가 {timeout_ms}ms 안에 나타나지 않음: {list(selectors)}")
        page.wait_for_timeout(POLL_MS)


def click_first(
    page: Page,
    selectors: Sequence[str],
    *,
    scope: Locator | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> Locator:
    locator = first_match(page, selectors, scope=scope, timeout_ms=timeout_ms)
    locator.click()
    return locator


def click_optional(
    page: Page,
    selectors: Sequence[str],
    *,
    scope: Locator | None = None,
    timeout_ms: int = 3_000,
) -> bool:
    """있을 때만 누르는 보조 다이얼로그(확인/초안 정리 등) 처리."""
    try:
        first_match(page, selectors, scope=scope, timeout_ms=timeout_ms).click()
        return True
    except PlaywrightTimeout:
        return False
    except Exception:
        return False


def click_following_new_page(
    page: Page,
    selectors: Sequence[str],
    *,
    scope: Locator | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    settle_ms: int = 2_500,
) -> Page:
    """클릭이 새 탭/창을 열면 그 페이지를, 아니면 기존 페이지를 반환.

    Naver Creator Studio `만들기`처럼 업로드 화면을 새 탭으로 여는 UI 대응.
    """
    ctx = page.context
    before = list(ctx.pages)
    click_first(page, selectors, scope=scope, timeout_ms=timeout_ms)
    page.wait_for_timeout(settle_ms)
    new_pages = [p for p in ctx.pages if p not in before]
    if new_pages:
        target = new_pages[-1]
        try:
            target.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        target.bring_to_front()
        return target
    return page


def wait_enabled(page: Page, locator: Locator, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            if locator.is_enabled() and locator.get_attribute("aria-disabled") not in ("true",):
                return
        except Exception:
            pass
        page.wait_for_timeout(POLL_MS)
    raise PlaywrightTimeout(f"버튼이 {timeout_ms}ms 안에 활성화되지 않음")


def file_input_anywhere(page: Page, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> Locator:
    """페이지(및 iframe)에서 첨부용 input[type=file] 을 찾는다."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        locator = page.locator('input[type="file"]').first
        try:
            if locator.count() > 0:
                return locator
        except Exception:
            pass
        for frame in page.frames[1:]:
            locator = frame.locator('input[type="file"]').first
            try:
                if locator.count() > 0:
                    return locator
            except Exception:
                continue
        if time.monotonic() >= deadline:
            raise PlaywrightTimeout(f"input[type=file] 을 {timeout_ms}ms 안에 찾지 못함")
        page.wait_for_timeout(POLL_MS)


def fill_editor_exact(page: Page, editor: Locator, text: str) -> None:
    """리치 에디터에 text 를 넣고 화면에 보이는 값이 기대값과 같은지 재검증한다.

    insert_text(CDP Input.insertText 상당)는 키 이벤트 자동완성 팝업을 유발하지
    않아 TikTok Draft.js / LinkedIn Quill(shadow DOM) 계열에서 가장 안전하다.
    실패 시 저속 실제 타이핑으로 한 번 더 시도한다.
    """
    editor.click()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.press("Backspace")
    page.keyboard.insert_text(text)
    page.wait_for_timeout(400)
    if norm_text(_editor_text(editor)) != norm_text(text):
        editor.click()
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.press("Backspace")
        editor.press_sequentially(text, delay=12)
        page.wait_for_timeout(400)
    visible = norm_text(_editor_text(editor))
    if visible != norm_text(text):
        raise StepFailure(
            "fill-text",
            f"입력 후 화면 텍스트가 기대값과 다릅니다. expected={text[:80]!r} got={visible[:80]!r}",
            hint="에디터가 프로그램 입력을 무시함 — 에이전트가 직접 입력·확인해야 하는 지점.",
        )


def _editor_text(editor: Locator) -> str:
    try:
        value = editor.input_value()
        if value:
            return value
    except Exception:
        pass
    try:
        return editor.inner_text()
    except Exception:
        return ""


def require_no_login_redirect(page: Page, patterns: Sequence[str], platform: str) -> None:
    url = page.url
    if any(pattern in url for pattern in patterns):
        raise StepFailure(
            "login",
            f"로그인이 풀렸습니다: {url}",
            hint=f"`uv run shorts-dist login {platform}` 으로 다시 로그인한 뒤 재시도하세요.",
            blocker=True,
        )


AI_LABEL_STEP = "ai-label"
_SWITCH_MARK = "data-shorts-ai-switch"
# aria-pressed 는 제외: 실측(Naver)에서 라벨 옆 툴팁(ⓘ) 버튼이 aria-pressed 를 가져
# 진짜 토글 대신 툴팁을 "켜짐"으로 오인할 수 있었다.
_SWITCH_CONTROLS = '[role="switch"], input[type="checkbox"], [aria-checked]'


def _ai_label_failure(platform: str, message: str) -> StepFailure:
    # blocker=True: 같은 플랫폼의 다음 영상도 같은 화면에서 똑같이 막히므로 레인 전체를
    # 멈추고, 다른 플랫폼 레인은 계속 진행한다.
    return StepFailure(
        AI_LABEL_STEP,
        message,
        hint=(
            f"게시하지 않음(fail closed). {platform} 작성 화면에서 AI 생성 콘텐츠 표시 토글을 "
            "사람이 확인해야 합니다. 토글 없이 수동 게시하지 말고 사용자에게 알리세요."
        ),
        blocker=True,
    )


def _switch_state(locator: Locator) -> str:
    return locator.evaluate(
        # 실측(Instagram): input[role=switch]가 checked 대신 aria-checked 로 상태를
        # 표시한다 — aria 속성이 있으면 그것을 우선한다.
        "el => {"
        " const v = el.getAttribute('aria-checked') ?? el.getAttribute('aria-pressed');"
        " if (v === 'true') return 'on'; if (v === 'false') return 'off';"
        " if (el instanceof HTMLInputElement) return el.checked ? 'on' : 'off';"
        " return 'unknown'; }"
    )


def ensure_ai_label(
    page: Page,
    platform: str,
    labels: Sequence[str],
    *,
    scope: Locator | None = None,
    reveal: Sequence[str] = (),
    confirm: Sequence[str] = (),
    timeout_ms: int = 10_000,
) -> str:
    """AI 생성 콘텐츠 표시 토글을 켜고 켜진 상태를 확인한다. 확인 못 하면 게시 금지.

    라벨 텍스트에서 가장 가까운 단일 스위치를 찾는다. 같은 단계에서 스위치가 둘 이상
    보이면(예: 옆의 광고 토글) 잘못 켤 수 있으므로 실패로 처리한다.
    """
    label_selectors = [f"text={label}" for label in labels]
    try:
        label_el = first_match(page, label_selectors, scope=scope, timeout_ms=3_000 if reveal else timeout_ms)
    except PlaywrightTimeout:
        label_el = None
    if label_el is None and reveal:
        click_optional(page, reveal, scope=scope, timeout_ms=4_000)
        page.wait_for_timeout(600)
        try:
            label_el = first_match(page, label_selectors, scope=scope, timeout_ms=timeout_ms)
        except PlaywrightTimeout:
            label_el = None
    if label_el is None:
        raise _ai_label_failure(platform, f"AI 표시 토글 라벨을 찾지 못함: {list(labels)}")

    label_el.scroll_into_view_if_needed()
    root = scope if scope is not None else page
    root.locator(f"[{_SWITCH_MARK}]").evaluate_all(
        f"els => els.forEach(e => e.removeAttribute('{_SWITCH_MARK}'))"
    )
    # 1순위: 라벨 텍스트를 접근성 이름으로 가진 스위치(예: Naver aria-label="AI 활용 설정").
    named = root.locator(", ".join(f'[role="switch"][aria-label="{label}"]' for label in labels))
    named_count = named.count()
    if named_count > 1:
        raise _ai_label_failure(platform, "같은 이름의 AI 표시 토글이 여러 개라 특정 불가")
    found = "one" if named_count == 1 else None
    if found:
        named.first.evaluate(f"el => el.setAttribute('{_SWITCH_MARK}', '1')")
    else:
        found = label_el.evaluate(
            "(el, [sel, mark]) => {"
            " let node = el;"
            " for (let i = 0; i < 6 && node; i++) {"
            # 실측(TikTok): Switch__content[aria-checked] 안에 input[role=switch]가 중첩돼
            # 있다 — 다른 후보에 포함된 후보는 같은 스위치로 보고 바깥 요소만 센다.
            "  const all = [...node.querySelectorAll(sel)];"
            "  const hits = all.filter(h => !all.some(o => o !== h && o.contains(h)));"
            "  if (hits.length === 1) { hits[0].setAttribute(mark, '1'); return 'one'; }"
            "  if (hits.length > 1) return 'many';"
            "  node = node.parentElement;"
            " }"
            " return 'none'; }",
            [_SWITCH_CONTROLS, _SWITCH_MARK],
        )
    if found != "one":
        reason = "토글 후보가 여러 개라 특정 불가" if found == "many" else "라벨 근처에 토글이 없음"
        raise _ai_label_failure(platform, f"AI 표시 토글을 특정하지 못함({reason})")
    switch = root.locator(f"[{_SWITCH_MARK}]").first

    state = _switch_state(switch)
    if state == "unknown":
        raise _ai_label_failure(platform, "AI 표시 토글의 켜짐/꺼짐 상태를 읽을 수 없음")
    if state == "off":
        try:
            switch.click(timeout=3_000)
        except Exception:
            # 숨은 체크박스/움직이는 미리보기로 일반 클릭이 막히는 경우
            switch.evaluate("el => el.click()")
        page.wait_for_timeout(600)
        if confirm:
            click_optional(page, confirm, timeout_ms=3_000)
            page.wait_for_timeout(400)
        state = _switch_state(switch)
    if state != "on":
        raise _ai_label_failure(platform, "AI 표시 토글을 켰지만 켜진 상태가 확인되지 않음")
    return "AI 생성 콘텐츠 표시 켜짐 확인"
