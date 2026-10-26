"""Naver Clip(Creator Studio) 업로드/검증.

주의(레퍼런스 failure-modes 12~14, 20):
  - 제목은 24자 제한 — 의미를 보존해 축약하고 실제 게시 제목을 기록한다.
  - 카테고리 2개(.env NAVER_CATEGORY_1/2)는 필수이고 드롭다운이 DOM 클릭을
    무시할 수 있다 — 선택 후 표시값을 재확인하고, 실패 시 에이전트 인계.
  - 파일 선택 후 모달이 조용히 닫히고 목록으로 돌아오는 실패 모드가 있다.
    업로드 진행 증거가 없으면 완료로 간주하지 않는다.
  - 목록은 30~60초 lazy-load — 검증은 한 번 재시도한다.
"""

from __future__ import annotations

from playwright.sync_api import Page

from .base import (
    Job,
    Outcome,
    StepFailure,
    Steps,
    VerifyOutcome,
    click_following_new_page,
    ensure_ai_label,
    first_match,
    file_input_anywhere,
    norm_text,
    require_no_login_redirect,
    wait_enabled,
)

LOGIN_URL = "https://nid.naver.com/nidlogin.login"
TITLE_LIMIT = 24
_LOGIN_REDIRECTS = ("nid.naver.com",)

_CREATE = (
    'button:has-text("클립 업로드")',
    'a:has-text("클립 업로드")',
    'button:has-text("만들기")',
    'a:has-text("만들기")',
    'text="+ 만들기"',
    # Clip Creators(clipcreators.naver.com) 신 UI: 우상단 `+ 업로드`
    'button:has-text("+ 업로드")',
    'a:has-text("+ 업로드")',
    'button:has-text("업로드")',
)
_UPLOAD_MENU = (
    'button:has-text("클립 업로드")',
    'a:has-text("클립 업로드")',
    '[role="menuitem"]:has-text("클립")',
    'text="클립 업로드"',
    'a:has-text("클립")',
)
_UPLOAD_PROGRESS = (
    'text=/업로드 중|인코딩|% /',
    'input[placeholder*="제목"]',
    'textarea[placeholder*="제목"]',
    'input[maxlength="24"]',
    'text="제목"',
)
_TITLE_FIELD = (
    'input[maxlength="24"]',
    'textarea[maxlength="24"]',
    'input[placeholder*="제목"]',
    'textarea[placeholder*="제목"]',
)
# 클립 상세 정보 모달의 1차/2차 카테고리 드롭다운(실측 라벨).
_CATEGORY_TRIGGERS_BY_ORDINAL = (
    ('button:has-text("1차 카테고리")', 'div[role="button"]:has-text("1차 카테고리")', 'text="1차 카테고리"'),
    ('button:has-text("2차 카테고리")', 'div[role="button"]:has-text("2차 카테고리")', 'text="2차 카테고리"'),
)
# Clip Creators 상세 정보의 `AI 활용 설정` 토글(바로 아래 `광고·협찬 설정` 토글과 혼동 금지).
_AI_LABELS = ("AI 활용 설정",)
_AI_CONFIRM = ('div[role="dialog"] button:has-text("확인")',)
_SUBMIT = (
    'button:has-text("저장")',  # 실측: 클립 상세 정보 모달의 확정 버튼
    'button:has-text("등록")',
    'button:has-text("업로드")',
    'button:has-text("올리기")',
    'button:has-text("게시")',
)


def _utf16_units(text: str) -> int:
    """Naver's browser counter follows JavaScript UTF-16 string length."""
    return len(text.encode("utf-16-le")) // 2


def _shorten_title(text: str, limit: int = TITLE_LIMIT) -> str:
    if _utf16_units(text) <= limit:
        return text

    suffix = "…"
    budget = limit - _utf16_units(suffix)
    kept: list[str] = []
    used = 0
    for char in text:
        units = _utf16_units(char)
        if used + units > budget:
            break
        kept.append(char)
        used += units
    return "".join(kept).rstrip() + suffix


def _studio_url(cfg) -> str:
    if cfg.naver_clip_url:
        return cfg.naver_clip_url
    if cfg.naver_channel_slug:
        return f"https://creator.tv.naver.com/channel/{cfg.naver_channel_slug}/content/clip"
    return "https://clipcreators.naver.com/web/contents/clips"


def _select_category(page: Page, steps: Steps, label: str, ordinal: int) -> None:
    """ordinal 번째(1차/2차) 카테고리 드롭다운에서 label 선택 후 표시값 재확인.

    주의: 모달 뒤 콘텐츠 목록 테이블에도 같은 카테고리 텍스트(예: 테크)가 깔려
    있어 page 전역 get_by_text 로 옵션을 집으면 배경 셀을 잘못 누른다(실측).
    옵션은 드롭다운 리스트 요소(li/[role=option])로 한정한다.
    """
    trigger_selectors = _CATEGORY_TRIGGERS_BY_ORDINAL[min(ordinal, len(_CATEGORY_TRIGGERS_BY_ORDINAL) - 1)]
    # 모달 드롭다운은 가상화 리스트(button[role=option], absolute 배치)라
    # 스크롤 전에는 뒷순서 옵션이 DOM 에 없다(실측: 테크 미렌더). 휠로 렌더 유도.
    option_selectors = (
        f'button[role="option"]:has-text("{label}")',
        f'[role="option"]:text-is("{label}")',
        f'[role="option"]:has-text("{label}")',
        f'button:text-is("{label}")',
    )
    for attempt in range(2):
        try:
            trigger = first_match(page, trigger_selectors, timeout_ms=8_000)
        except Exception:
            # 이미 선택돼 트리거 라벨이 값으로 바뀐 경우 — 선택 확인으로 넘어감
            break
        trigger.click()
        page.wait_for_timeout(600)
        clicked = False
        bottom_hits = 0
        for _ in range(20):  # 가상화 리스트 스크롤하며 옵션 렌더 대기
            try:
                option = first_match(page, option_selectors, timeout_ms=1_200)
                option.click()
                clicked = True
                break
            except Exception:
                scrolled = False
                # 주의: 닫힌 드롭다운의 숨은 option 들이 DOM 에 남아 있어 `.first`
                # 앵커는 엉뚱한(숨은) 컨테이너를 스크롤한다(실측) — 반드시 :visible.
                anchor = page.locator('[role="option"]:visible').first
                try:
                    if anchor.count():
                        scrolled = bool(anchor.evaluate(
                            "el => { let p = el.parentElement;"
                            " while (p && p.scrollHeight <= p.clientHeight + 4) p = p.parentElement;"
                            " if (!p) return false; const before = p.scrollTop;"
                            " p.scrollTop += Math.max(200, p.clientHeight * 0.8);"
                            " return p.scrollTop !== before; }"
                        ))
                except Exception:
                    pass
                if not scrolled:
                    bottom_hits += 1
                    if bottom_hits >= 2:
                        break  # 리스트 끝까지 봤는데 없음 — 라벨 불일치 가능성
                    page.mouse.wheel(0, 240)
                page.wait_for_timeout(300)
        if clicked:
            page.wait_for_timeout(600)
            break
        page.keyboard.press("Escape")
    # 확인: 드롭다운 라벨이 값(label)으로 바뀌었는지 — 남아 있으면 미선택.
    still_placeholder = False
    try:
        still_placeholder = first_match(page, trigger_selectors, timeout_ms=1_500).is_visible()
    except Exception:
        still_placeholder = False
    if still_placeholder:
        try:
            rendered = ", ".join(norm_text(t) for t in page.locator('[role="option"]').all_inner_texts()[:20])
        except Exception:
            rendered = ""
        raise StepFailure(
            "category",
            f"카테고리 {ordinal + 1}({label}) 선택을 확정하지 못했습니다."
            + (f" 렌더된 옵션: {rendered[:200]}" if rendered else ""),
            hint="Naver 드롭다운이 DOM 클릭을 무시할 수 있음 — 에이전트(Claude in Chrome 스크린샷 클릭)로 선택 후 저장하세요.",
        )


def upload(page: Page, job: Job, steps: Steps, cfg) -> Outcome:
    posted_title = _shorten_title(job.title_text)

    with steps.step("open", hint="Creator Studio 접속, 로그인/사이드바 상태 확인"):
        page.goto(_studio_url(cfg), wait_until="domcontentloaded")
        page.wait_for_timeout(3_000)
        require_no_login_redirect(page, _LOGIN_REDIRECTS, "naver")

    with steps.step("composer", hint="만들기 → (메뉴) 클립 업로드. 새 탭으로 열리면 따라간다"):
        # 실측: 만들기/클립 업로드가 업로드 화면을 새 탭으로 열 수 있다(기존 탭은
        # 콘텐츠 목록 그대로 → attach 타임아웃으로 오진되던 원인).
        page = click_following_new_page(page, _CREATE, timeout_ms=30_000)
        steps.page = page
        try:
            page = click_following_new_page(page, _UPLOAD_MENU, timeout_ms=6_000)
            steps.page = page
        except Exception:
            pass  # 메뉴 없이 바로 업로드 화면일 수 있음
        page.wait_for_timeout(2_000)

    with steps.step("attach", hint="파일 지정 후 진행 증거 확인(모달 조용히 닫힘 = 실패)"):
        file_input_anywhere(page, timeout_ms=30_000).set_input_files(str(job.file_path))
        try:
            first_match(page, _UPLOAD_PROGRESS, timeout_ms=15_000)
        except Exception as exc:
            raise StepFailure(
                "attach",
                "파일 선택 후 업로드 진행 증거(진행률/제목 입력창)가 나타나지 않음",
                hint="failure-mode #20: 모달이 조용히 닫혔다면 업로드는 시작되지 않은 것. 완료로 간주 금지, 에이전트로 재시도.",
            ) from exc

    with steps.step("title", hint=f"제목 {TITLE_LIMIT}자 제한 — 축약본 입력 후 값 재확인"):
        field = first_match(page, _TITLE_FIELD, timeout_ms=30_000)
        field.click()
        field.fill(posted_title)
        if norm_text(field.input_value()) != norm_text(posted_title):
            raise StepFailure("title", "제목 입력값이 기대와 다릅니다.")

    categories = [c for c in (cfg.naver_category_1, cfg.naver_category_2) if c]
    for i, label in enumerate(categories):
        with steps.step(f"category-{i + 1}", hint="드롭다운 표시값까지 확인"):
            _select_category(page, steps, label, i)

    if "ai-generated" in job.disclosures:
        with steps.step("ai-label", hint="상세 정보 → AI 활용 설정 토글. 확인 못 하면 저장하지 않는다"):
            ensure_ai_label(page, "naver", _AI_LABELS, confirm=_AI_CONFIRM)

    with steps.step("submit", hint="저장 버튼 활성화 확인 후 등록"):
        button = first_match(page, _SUBMIT, timeout_ms=15_000)
        wait_enabled(page, button, timeout_ms=180_000)
        button.click()

    with steps.step("confirm", hint="목록 복귀 후 새 클립 행 확인(30~60초 lazy-load 재시도 포함)"):
        found = _find_in_list(page, cfg, posted_title)
        if not found:
            raise StepFailure(
                "confirm",
                "등록 후 콘텐츠 목록에서 새 클립을 찾지 못함",
                hint="목록 lazy-load 지연일 수 있음 — verify 재실행 또는 에이전트 확인.",
            )

    return Outcome(
        "published",
        posted_text=posted_title,
        evidence="콘텐츠 목록에서 새 클립 제목 확인",
    )


def _find_in_list(page: Page, cfg, needle: str) -> bool:
    for wait_ms in (8_000, 45_000):
        page.wait_for_timeout(wait_ms)
        page.goto(_studio_url(cfg), wait_until="domcontentloaded")
        page.wait_for_timeout(4_000)
        body = norm_text(page.locator("body").inner_text()[:30_000])
        if norm_text(needle) in body:
            return True
    return False


def verify(page: Page, job: Job, steps: Steps, cfg) -> VerifyOutcome:
    posted_title = _shorten_title(job.title_text)
    with steps.step("verify-open"):
        page.goto(_studio_url(cfg), wait_until="domcontentloaded")
        page.wait_for_timeout(4_000)
        require_no_login_redirect(page, _LOGIN_REDIRECTS, "naver")

    with steps.step("verify-scan", hint="목록 lazy-load 대비 1회 재시도"):
        for wait_ms in (0, 45_000):
            if wait_ms:
                page.wait_for_timeout(wait_ms)
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(4_000)
            body = norm_text(page.locator("body").inner_text()[:30_000])
            if norm_text(posted_title) in body:
                return VerifyOutcome("verified", evidence="클립 목록에서 제목 일치")
        return VerifyOutcome(
            "missing",
            evidence="클립 목록에서 제목 미발견(재시도 포함)",
            artifacts=steps.capture("verify-miss"),
        )
