"""TikTok Studio 업로드/검증.

주의(레퍼런스 failure-modes 4~7):
  - 캡션은 Draft.js 에디터라 프로그램 삽입이 무시될 수 있다 — insert_text 후
    화면 텍스트 재검증(fill_editor_exact)이 필수.
  - 남은 초안 모달은 `취소 → 삭제` 경로로 정리한다(`나중에`는 반쯤 깨진 상태를 남김).
  - 검증은 캡션 문자열이 아니라 콘텐츠 목록의 날짜/최신 위치로 판단한다.
    (`설명 없음` 표시는 게시 실패가 아니다.)
"""

from __future__ import annotations

import re

from playwright.sync_api import Page

from ..caption import shorten
from .base import (
    Job,
    Outcome,
    Steps,
    VerifyOutcome,
    click_optional,
    ensure_ai_label,
    fill_editor_exact,
    file_input_anywhere,
    first_match,
    norm_text,
    require_no_login_redirect,
    wait_enabled,
)

LOGIN_URL = "https://www.tiktok.com/login"
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=creator_center"
CONTENT_URL = "https://www.tiktok.com/tiktokstudio/content"
CAPTION_LIMIT = 2_000
_LOGIN_REDIRECTS = ("/login",)

_EDITOR = (
    'div.public-DraftEditor-content[contenteditable="true"]',
    'div[contenteditable="true"]',
)
_SHOW_MORE = (
    'button:has-text("더 보기")',
    'button:has-text("Show more")',
    'div[role="button"]:has-text("더 보기")',
    'div[role="button"]:has-text("Show more")',
)
_DISCLOSURE_SWITCH = (
    '[data-e2e="disclose_content_container"] .Switch__content[aria-checked]',
    '.Switch__content[aria-checked]',
    '[data-e2e="disclose_content_container"] input[role="switch"]',
    '[data-e2e="disclose_content_container"] [role="switch"]',
    '[role="switch"][aria-label*="게시물 콘텐츠 공개"]',
    '[role="switch"][aria-label*="콘텐츠 공개"]',
    '[role="switch"][aria-label*="Disclose post content" i]',
    'input[type="checkbox"][aria-label*="콘텐츠 공개"]',
    'input[type="checkbox"][aria-label*="Disclose post content" i]',
)
_DISCLOSURE_CONTAINER = ('[data-e2e="disclose_content_container"]',)
_BRANDED_CONTENT = (
    '.title-line:has(span:text-is("브랜디드 콘텐츠")) label',
    '.title-line:has(span:text-is("Branded content")) label',
    'label:has-text("브랜드 콘텐츠")',
    '[role="radio"]:has-text("브랜드 콘텐츠")',
    'label:has-text("Branded content")',
    '[role="radio"]:has-text("Branded content")',
)
_DISCLOSURE_SAVE = (
    'div[role="dialog"] button:has-text("저장")',
    'div[role="dialog"] button:has-text("Save")',
    'div[role="dialog"] button:has-text("계속")',
    'div[role="dialog"] button:has-text("Continue")',
)
# `더 보기` 아래 `AI 생성 콘텐츠` 토글. 켤 때 확인 모달이 뜰 수 있다.
_AI_LABELS = ("AI 생성 콘텐츠", "AI-generated content")
# 실측(2026-10): 콘텐츠 공개/AI 생성 콘텐츠 토글은 설정 카드의 `펼치기` 아래 접혀 있다.
_AI_REVEAL = (
    '[data-e2e="advanced_settings_container"] .more-btn',
    'div:text-is("펼치기")',
    'span:text-is("펼치기")',
    'span:text-is("Show more")',
    *_SHOW_MORE,
)
_AI_CONFIRM = (
    'div[role="dialog"] button:has-text("켜기")',
    'div[role="dialog"] button:has-text("Turn on")',
    'div[role="dialog"] button:has-text("확인")',
)
_POST = (
    '[data-e2e="post_video_button"]',
    'button:has-text("게시")',
    'button:has-text("Post")',
)
_CONFIRM_NOW = (
    'button:has-text("지금 게시")',
    'div[role="dialog"] button:has-text("게시")',
    'button:has-text("Post now")',
)
_SUCCESS = (
    'text=/게시되었습니다|게시됨/',
    'text=/동영상 관리|Manage your posts/i',
    'text=/has been (uploaded|posted)/i',
)
_RECENT_ROW_TIME = re.compile(r"방금|분 전|시간 전|오늘|Just now|minutes? ago|hours? ago|Today", re.I)


def _clear_draft_modals(page: Page) -> None:
    # 초안 경고: 첫 모달은 `취소`(편집 취소), 이어지는 확인 모달은 `삭제`.
    if click_optional(page, ('div[role="dialog"] button:has-text("취소")', 'button:has-text("Discard")'), timeout_ms=4_000):
        click_optional(page, ('div[role="dialog"] button:has-text("삭제")', 'button:has-text("Delete")'), timeout_ms=4_000)


_ANNOUNCEMENTS = (
    'div[role="dialog"] button:has-text("확인")',
    'div[class*="modal" i] button:has-text("확인")',
    'button:has-text("알겠습니다")',
    'button:has-text("Got it")',
    'button:has-text("확인")',
)


def _dismiss_announcements(page: Page, rounds: int = 3) -> None:
    """기능 공지/온보딩 팝업 정리. 캡션 입력을 가로막는 실측 사례가 있다."""
    for _ in range(rounds):
        if not click_optional(page, _ANNOUNCEMENTS, timeout_ms=2_500):
            return
        page.wait_for_timeout(400)


def _enable_branded_content_disclosure(page: Page) -> None:
    click_optional(page, _SHOW_MORE, timeout_ms=4_000)
    container = first_match(page, _DISCLOSURE_CONTAINER, timeout_ms=10_000, state="attached")
    # TikTok의 움직이는 미리보기 때문에 Playwright의 stable 대기가 타임아웃될 수 있다.
    container.evaluate("el => el.scrollIntoView({block: 'center'})")
    page.wait_for_timeout(500)
    switch = first_match(
        page,
        _DISCLOSURE_SWITCH,
        scope=container,
        timeout_ms=10_000,
        state="attached",
    )
    checked = switch.get_attribute("aria-checked") == "true"
    if switch.evaluate("el => el instanceof HTMLInputElement"):
        checked = switch.is_checked()
    if not checked:
        switch.evaluate("el => el.click()")
    page.wait_for_timeout(500)
    checked = switch.get_attribute("aria-checked") == "true"
    if switch.evaluate("el => el instanceof HTMLInputElement"):
        checked = switch.is_checked()
    if not checked:
        raise RuntimeError("TikTok 콘텐츠 공개 설정을 활성화하지 못함")
    branded = first_match(page, _BRANDED_CONTENT, timeout_ms=8_000, state="attached")
    branded_checked = branded.get_attribute("aria-checked") == "true"
    checkbox = branded.locator('input[type="checkbox"]')
    if checkbox.count():
        branded_checked = checkbox.first.is_checked()
    if not branded_checked:
        branded.evaluate("el => el.click()")
    page.wait_for_timeout(500)
    branded_checked = branded.get_attribute("aria-checked") == "true"
    if checkbox.count():
        branded_checked = checkbox.first.is_checked()
    if not branded_checked:
        raise RuntimeError("TikTok 브랜디드 콘텐츠 항목을 선택하지 못함")
    click_optional(page, _DISCLOSURE_SAVE, timeout_ms=5_000)


def upload(page: Page, job: Job, steps: Steps, cfg) -> Outcome:
    posted_text = shorten(job.post_text, CAPTION_LIMIT)

    with steps.step("open", hint="TikTok Studio 업로드 페이지 접속 및 로그인 확인"):
        page.goto(UPLOAD_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3_000)
        require_no_login_redirect(page, _LOGIN_REDIRECTS, "tiktok")
        _clear_draft_modals(page)
        _dismiss_announcements(page)

    with steps.step("attach", hint="숨겨진 input[type=file] 에 직접 파일 지정(iframe 폴백 포함)"):
        file_input_anywhere(page, timeout_ms=30_000).set_input_files(str(job.file_path))

    with steps.step("caption", hint="Draft.js 에디터 — 공지 팝업 정리 후 전체선택 삭제·정확 입력, 파일명 잔재 금지"):
        editor = first_match(page, _EDITOR, timeout_ms=180_000)
        _dismiss_announcements(page)
        fill_editor_exact(page, editor, posted_text)

    if "branded-content" in job.disclosures:
        with steps.step("branded-content", hint="Content policy requires TikTok branded-content disclosure"):
            _enable_branded_content_disclosure(page)

    if "ai-generated" in job.disclosures:
        with steps.step("ai-label", hint="펼치기 → AI 생성 콘텐츠 토글. 확인 못 하면 게시하지 않는다"):
            ensure_ai_label(page, "tiktok", _AI_LABELS, reveal=_AI_REVEAL, confirm=_AI_CONFIRM)

    with steps.step("post", hint="업로드 처리 완료 후 게시 버튼 활성화 대기"):
        post_button = first_match(page, _POST, timeout_ms=60_000)
        wait_enabled(page, post_button, timeout_ms=300_000)
        _dismiss_announcements(page)
        post_button.click()
        # 저작권/콘텐츠 확인이 아닌 일반 `지금 게시` 확인 모달만 통과시킨다.
        click_optional(page, _CONFIRM_NOW, timeout_ms=6_000)

    with steps.step("confirm", hint="게시 완료 모달 또는 콘텐츠 목록 전환 대기"):
        deadline_ms = 180_000
        try:
            first_match(page, _SUCCESS, timeout_ms=deadline_ms)
            evidence = "게시 완료 안내 확인"
        except Exception:
            if "/content" in page.url:
                evidence = "콘텐츠 목록으로 전환됨"
            else:
                raise

    return Outcome("published", posted_text=posted_text, evidence=evidence)


def verify(page: Page, job: Job, steps: Steps, cfg) -> VerifyOutcome:
    """Studio 콘텐츠 목록의 최신 행 날짜/위치로 판정. 캡션 부재는 실패 근거가 아니다."""
    needle = norm_text(job.title_text)[:24]
    with steps.step("verify-open"):
        page.goto(CONTENT_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(5_000)
        require_no_login_redirect(page, _LOGIN_REDIRECTS, "tiktok")

    with steps.step("verify-scan", hint="최신 행 날짜/제목 대조(캡션 문자열 단독 판정 금지)"):
        body_text = norm_text(page.locator("body").inner_text()[:20_000])
        if not body_text:
            return VerifyOutcome("inconclusive", evidence="콘텐츠 목록을 읽지 못함", artifacts=steps.capture("verify"))
        if needle and needle in body_text:
            return VerifyOutcome("verified", evidence="콘텐츠 목록에서 제목 일치")
        if _RECENT_ROW_TIME.search(body_text):
            return VerifyOutcome(
                "verified",
                evidence="콘텐츠 목록 최신 행이 방금 게시됨(날짜 기준 — 캡션 미표시는 정상)",
            )
        return VerifyOutcome(
            "inconclusive",
            evidence="최신 행 날짜/제목 모두 불일치 — 에이전트가 썸네일로 확인 필요",
            artifacts=steps.capture("verify-miss"),
        )
