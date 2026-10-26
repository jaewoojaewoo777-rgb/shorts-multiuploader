"""Instagram Reels 업로드/검증.

주의(레퍼런스 failure-modes 1~3):
  - h264 MP4 만 업로드한다(러너의 ensure_h264 가 보장). AV1 은 조용히 실패.
  - 자르기 화면에서 반드시 `원본` 비율을 고른다(기본값이 1:1 크롭).
  - 공유 클릭 후 스피너가 최대 ~5분 돈다. 성공 다이얼로그 전에 이동/닫기 금지.
    성공하면 릴스 탭에 수 초 내 노출된다(채널 운영자 실측) — 러너가 즉시 검증한다.
"""

from __future__ import annotations

import time
from urllib.parse import urljoin

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from ..caption import shorten
from .base import (
    Job,
    Outcome,
    StepFailure,
    Steps,
    VerifyOutcome,
    click_first,
    click_optional,
    ensure_ai_label,
    fill_editor_exact,
    file_input_anywhere,
    first_match,
    norm_text,
    require_no_login_redirect,
)

LOGIN_URL = "https://www.instagram.com/accounts/login/"
HOME_URL = "https://www.instagram.com/"
CAPTION_LIMIT = 2_200
_LOGIN_REDIRECTS = ("accounts/login", "/challenge")

_CREATE = (
    'a:has(svg[aria-label="새로운 게시물"])',
    'div[role="link"]:has(svg[aria-label="새로운 게시물"])',
    'a:has(svg[aria-label="New post"])',
    'div[role="link"]:has(svg[aria-label="New post"])',
    'svg[aria-label="새 게시물"]',
    'svg[aria-label="만들기"]',
    'svg[aria-label="새로운 게시물"]',
    'svg[aria-label="New post"]',
)
_POST_MENU = ('role=link[name="게시물"]', 'text="게시물"', 'role=link[name="Post"]')
_NOTIFICATION_LATER = (
    'button:text-is("나중에 하기")',
    'button:text-is("Not Now")',
)
_OK_INTERSTITIAL = ('role=button[name="확인"]', 'role=button[name="OK"]')
# 실측(2026-10): "이제 동영상 게시물이 릴스로 공유됩니다" 안내는 작성 다이얼로그와 별개의
# role=dialog 로 뜨므로 작성 다이얼로그로 스코핑하면 못 찾는다.
_REELS_NOTICE_OK = (
    'div[role="dialog"] button:text-is("확인")',
    'div[role="dialog"] button:text-is("OK")',
)
_CROP_TOGGLE = (
    'button:has(svg[aria-label="자르기 선택"])',
    'div[role="button"]:has(svg[aria-label="자르기 선택"])',
    'svg[aria-label="자르기 선택"]',
    'button:has(svg[aria-label="Select crop"])',
    'svg[aria-label="Select crop"]',
)
_ORIGINAL = (
    'div[role="button"]:has-text("원본")',
    'role=button[name="원본"]',
    'text="원본"',
    'div[role="button"]:has-text("Original")',
    'text="Original"',
)
_NEXT = (
    'div[role="button"]:has-text("다음")',
    'role=button[name="다음"]',
    'role=button[name="Next"]',
)
_CAPTION = (
    'div[role="textbox"][aria-label*="문구"]',
    'div[role="textbox"][aria-label*="캡션"]',  # 실측(2026-10): aria-label="캡션 추가..."
    'div[role="textbox"][aria-label*="caption" i]',
    'div[role="dialog"] div[role="textbox"][contenteditable="true"]',
    'div[role="textbox"][data-lexical-editor="true"]',
)
_PARTNERSHIP_SETTINGS = (
    'div[role="button"]:has-text("파트너십 레이블 및 광고")',
    'div[role="button"]:has-text("Partnership label and ads")',
    'div[role="button"]:has-text("고급 설정")',
    'div[role="button"]:has-text("Advanced settings")',
)
_PAID_PARTNERSHIP_SWITCH = (
    'div[role="switch"][aria-label*="유료 파트너십"]',
    'div[role="switch"][aria-label*="paid partnership" i]',
    'input[type="checkbox"][aria-label*="유료 파트너십"]',
    'input[type="checkbox"][aria-label*="paid partnership" i]',
)
# AI 생성 콘텐츠 표시는 마지막 화면의 `고급 설정` 접이식 섹션 안에 있다.
_AI_LABELS = ("AI 레이블 추가", "AI 정보 추가", "Add AI label", "AI info")
_ADVANCED_SETTINGS = (
    'div[role="button"]:has-text("고급 설정")',
    'div[role="button"]:has-text("Advanced settings")',
)
# 주의 1: 배경 피드 게시물마다 svg(title=공유하기) 공유 아이콘이 있어서
# 다이얼로그 스코핑 없이 :has-text 로 찾으면 피드 아이콘을 잘못 집는다(실측).
# 주의 2: 최종 화면 우측 패널에 `공유` 접이식 섹션 헤더가 있어서
# name="공유" 같은 느슨한 후보를 넣으면 섹션 토글을 잘못 누른다.
_SHARE = (
    'div[role="button"]:text-is("공유하기")',
    'div[role="button"]:text-is("Share")',
    'role=button[name="공유하기"]',
    'role=button[name="Share"]',
)
_DIALOG = 'div[role="dialog"]'
_SUCCESS = (
    'text="릴스가 공유되었습니다"',
    'text="회원님의 릴스가 공유되었습니다"',
    'text="게시물이 공유되었습니다"',
    'text=/reel has been shared/i',
    'text=/post has been shared/i',
    'img[alt*="체크 표시"]',
    'text="공유됨"',
)
# 공유 클릭 후 로딩 상태(약 3분, 대용량은 더 길 수 있음). 이 표시가 보이는 동안은
# 절대 창을 닫거나 이동하지 않는다 — 업로드가 중단된다(사용자 실측 피드백).
_PROGRESS = (
    'div[role="dialog"] [role="progressbar"]',
    'text=/공유 중입니다|공유 중|Sharing/',
)
_SHARE_HARD_CAP_S = 900  # 완료 대기 하드캡 15분
_NO_PROGRESS_GIVEUP_S = 300  # 진행 흔적이 전혀 없을 때 포기 시점
_PROGRESS_GONE_GRACE_S = 120  # 진행 표시 소실 후 완료 다이얼로그 유예


def _any_visible(page: Page, selectors) -> str | None:
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = min(locator.count(), 5)
        except Exception:
            continue
        for i in range(count):
            try:
                if locator.nth(i).is_visible():
                    try:
                        return norm_text(locator.nth(i).inner_text())[:80] or selector
                    except Exception:
                        return selector
            except Exception:
                continue
    return None


def _enable_paid_partnership(page: Page, dialog) -> None:
    click_optional(page, _PARTNERSHIP_SETTINGS, scope=dialog, timeout_ms=4_000)
    try:
        switch = first_match(page, _PAID_PARTNERSHIP_SWITCH, scope=dialog, timeout_ms=8_000)
    except PlaywrightTimeout:
        raise StepFailure(
            "branded-content",
            "Paid partnership control was not found",
            hint="Do not publish. Update the Instagram disclosure selectors or policy.",
        )
    checked = switch.get_attribute("aria-checked") == "true"
    if switch.evaluate("el => el instanceof HTMLInputElement"):
        checked = switch.is_checked()
    if not checked:
        switch.click()
    page.wait_for_timeout(500)
    checked = switch.get_attribute("aria-checked") == "true"
    if switch.evaluate("el => el instanceof HTMLInputElement"):
        checked = switch.is_checked()
    if not checked:
        raise StepFailure(
            "branded-content",
            "유료 파트너십 레이블을 활성화하지 못함",
            hint="게시하지 말고 Instagram 작성 화면에서 Paid partnership label을 확인하세요.",
        )


def _wait_crop_toggle(page: Page, dialog, timeout_s: int = 40) -> None:
    """자르기 버튼이 보일 때까지 기다리며, 그 위를 덮는 릴스 안내창은 닫는다."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        click_optional(page, _REELS_NOTICE_OK, timeout_ms=500)
        if _any_visible(dialog, _CROP_TOGGLE):
            return
        page.wait_for_timeout(1_000)


def _wait_share_complete(page: Page) -> tuple[str, str]:
    """공유 클릭 후 완료 다이얼로그까지 대기.

    반환 (status, evidence): status 는
      success     — 완료 다이얼로그 확인
      ambiguous   — 공유 진행은 확인했지만 완료 다이얼로그를 못 봄(게시됐을 수 있음)
      no-progress — 공유가 시작된 흔적 자체가 없음
    """
    start = time.monotonic()
    saw_progress = False
    last_progress = start
    while time.monotonic() - start < _SHARE_HARD_CAP_S:
        evidence = _any_visible(page, _SUCCESS)
        if evidence:
            return "success", evidence
        if _any_visible(page, _PROGRESS):
            saw_progress = True
            last_progress = time.monotonic()
        elif saw_progress and time.monotonic() - last_progress > _PROGRESS_GONE_GRACE_S:
            break
        elif not saw_progress and time.monotonic() - start > _NO_PROGRESS_GIVEUP_S:
            break
        page.wait_for_timeout(2_000)
    return ("ambiguous", "공유 진행 확인됨 — 완료 다이얼로그 미확인") if saw_progress else (
        "no-progress", "공유 진행 흔적 없음"
    )


def upload(page: Page, job: Job, steps: Steps, cfg) -> Outcome:
    posted_text = shorten(job.post_text, CAPTION_LIMIT)

    with steps.step("open", hint="instagram.com 접속 및 로그인 상태 확인"):
        page.goto(HOME_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2_500)
        require_no_login_redirect(page, _LOGIN_REDIRECTS, "instagram")
        # 로그인 프로필에 간헐적으로 뜨는 알림 설정 모달이 만들기 버튼을 가린다.
        click_optional(page, _NOTIFICATION_LATER, timeout_ms=5_000)

    with steps.step("composer", hint="좌측 만들기(+) → 게시물 → 파일 선택 창"):
        # 실측(2026-10): 만들기 클릭 뒤 작성 창 대신 프로필 화면이 남는 경우가 있었다.
        # 다이얼로그가 안 뜨면 홈으로 돌아가 한 번 더 시도한다(게시 전 단계라 안전).
        for attempt in range(2):
            if attempt:
                page.goto(HOME_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(2_500)
                require_no_login_redirect(page, _LOGIN_REDIRECTS, "instagram")
                click_optional(page, _NOTIFICATION_LATER, timeout_ms=3_000)
            click_first(page, _CREATE)
            # 최신 UI 는 만들기 클릭 시 서브메뉴(게시물/AI)가 뜬다. 없으면 그대로 진행.
            click_optional(page, _POST_MENU, timeout_ms=2_500)
            # 이후 모든 조작은 작성 다이얼로그 안으로 스코핑(배경 피드 요소 오클릭 방지).
            try:
                dialog = first_match(page, (_DIALOG,), timeout_ms=15_000)
                break
            except PlaywrightTimeout:
                if attempt:
                    raise
        file_input_anywhere(page).set_input_files(str(job.file_path))

    with steps.step("crop-original", hint="자르기 메뉴에서 `원본` 선택(기본 1:1 크롭 방지)"):
        click_optional(page, _OK_INTERSTITIAL, scope=dialog, timeout_ms=4_000)  # "동영상은 릴스로 공유됩니다" 안내
        _wait_crop_toggle(page, dialog)
        click_first(page, _CROP_TOGGLE, scope=dialog, timeout_ms=10_000)
        click_first(page, _ORIGINAL, scope=dialog)
        click_first(page, _NEXT, scope=dialog)

    with steps.step("edit-next", hint="편집(커버/트리밍) 화면에서 다음"):
        click_first(page, _NEXT, scope=dialog)

    with steps.step("caption", hint="문구 입력 — post_text 외 다른 텍스트 금지"):
        editor = first_match(page, _CAPTION, scope=dialog)
        fill_editor_exact(page, editor, posted_text)

    if "paid-partnership" in job.disclosures:
        with steps.step(
            "branded-content",
            hint="Content policy requires Instagram's paid partnership label",
        ):
            _enable_paid_partnership(page, dialog)

    if "ai-generated" in job.disclosures:
        with steps.step("ai-label", hint="고급 설정 → AI 레이블 추가 토글. 확인 못 하면 공유하지 않는다"):
            ensure_ai_label(page, "instagram", _AI_LABELS, scope=dialog, reveal=_ADVANCED_SETTINGS)

    with steps.step("share", hint="다이얼로그 우상단 공유하기(배경 피드의 공유 아이콘 아님)"):
        click_first(page, _SHARE, scope=dialog)

    with steps.step("confirm", hint="공유 중 로딩(약 3분, 최대 15분)을 완료 다이얼로그까지 대기 — 중간에 창 닫기/이동 금지"):
        status, evidence = _wait_share_complete(page)
        if status == "no-progress":
            raise StepFailure(
                "confirm",
                "공유 클릭 후 진행/완료 흔적이 없음",
                hint="공유하기 클릭이 실제로 동작했는지 에이전트가 화면 확인 필요.",
            )

    if status == "ambiguous":
        # 게시됐을 가능성이 있는 모호 상태 — 기록해서 중복 업로드를 막고,
        # 곧바로 이어지는 검증/pending 재확인이 진실을 판정한다.
        return Outcome(
            "published",
            posted_text=posted_text,
            evidence=f"{evidence} — verify --pending 재확인 필수",
        )
    return Outcome("published", posted_text=posted_text, evidence=f"공유 완료 다이얼로그: {evidence}")


def verify(page: Page, job: Job, steps: Steps, cfg) -> VerifyOutcome:
    """릴스 탭 상위 항목들의 og 메타 캡션을 직접 대조한다(shortcode 추정 금지 원칙)."""
    handle = cfg.instagram_handle
    if not handle:
        return VerifyOutcome("inconclusive", evidence="INSTAGRAM_HANDLE 미설정")

    needle = norm_text(job.post_text)[:40] or norm_text(job.title_text)[:40]
    with steps.step("verify-open"):
        page.goto(f"https://www.instagram.com/{handle}/reels/?__cb={int(time.time())}", wait_until="domcontentloaded")
        page.wait_for_timeout(3_000)
        require_no_login_redirect(page, _LOGIN_REDIRECTS, "instagram")

    with steps.step("verify-scan", hint="릴스 상위 항목 og:description 캡션 대조"):
        try:
            first_match(page, ('a[href*="/reel/"]',), state="attached", timeout_ms=15_000)
        except Exception:
            pass  # 아래 count==0 분기가 inconclusive 로 처리
        anchors = page.locator('a[href*="/reel/"]')
        hrefs: list[str] = []
        for i in range(min(anchors.count(), 8)):
            href = anchors.nth(i).get_attribute("href")
            if href and href not in hrefs:
                hrefs.append(href)
        if not hrefs:
            return VerifyOutcome(
                "inconclusive",
                evidence="릴스 탭에서 항목을 읽지 못함(피드 stale/로딩 실패 가능) — 에이전트 확인 필요",
                artifacts=steps.capture("verify-scan"),
            )
        for href in hrefs:
            page.goto(urljoin(HOME_URL, href), wait_until="domcontentloaded")
            page.wait_for_timeout(1_200)
            meta = ""
            for prop in ("og:description", "og:title"):
                node = page.locator(f'meta[property="{prop}"]').first
                if node.count():
                    meta += " " + (node.get_attribute("content") or "")
            if needle and needle in norm_text(meta):
                return VerifyOutcome("verified", url=urljoin(HOME_URL, href), evidence="og 캡션 일치")
        return VerifyOutcome(
            "missing",
            evidence=f"릴스 상위 {len(hrefs)}개에서 캡션 미발견(피드 capped 가능성 — 재업로드 전 에이전트 교차확인)",
            artifacts=steps.capture("verify-miss"),
        )
