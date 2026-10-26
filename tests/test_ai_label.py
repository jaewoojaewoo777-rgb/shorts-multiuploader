from unittest import SkipTest, TestCase

from playwright.sync_api import sync_playwright

from shorts_distributor.uploaders.base import AI_LABEL_STEP, StepFailure, ensure_ai_label

_TOGGLE = (
    "<script>function t(el){el.setAttribute('aria-checked',"
    " el.getAttribute('aria-checked')==='true'?'false':'true')}</script>"
)


class EnsureAiLabelTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._pw = sync_playwright().start()
        try:
            cls._browser = cls._pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # Chrome 미설치 환경
            cls._pw.stop()
            raise SkipTest(f"Chrome unavailable: {exc}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._browser.close()
        cls._pw.stop()

    def _page(self, body: str):
        page = self._browser.new_page()
        self.addCleanup(page.close)
        page.set_content(_TOGGLE + body)
        return page

    def _assert_fail_closed(self, page, **kwargs) -> None:
        with self.assertRaises(StepFailure) as ctx:
            ensure_ai_label(page, "naver", ("AI 활용 설정",), timeout_ms=800, **kwargs)
        self.assertEqual(ctx.exception.step, AI_LABEL_STEP)
        self.assertTrue(ctx.exception.blocker)

    def test_turns_off_switch_on(self) -> None:
        page = self._page(
            '<div><span>AI 활용 설정</span><div role="switch" aria-checked="false" onclick="t(this)">o</div></div>'
            '<div><span>광고 · 협찬 설정</span><div role="switch" aria-checked="false" onclick="t(this)">o</div></div>'
        )
        ensure_ai_label(page, "naver", ("AI 활용 설정",), timeout_ms=800)
        states = page.locator('[role="switch"]').evaluate_all("els => els.map(e => e.getAttribute('aria-checked'))")
        self.assertEqual(states, ["true", "false"])  # 광고 토글은 건드리지 않음

    def test_already_on_is_left_on(self) -> None:
        page = self._page('<div><span>AI 활용 설정</span><div role="switch" aria-checked="true" onclick="t(this)">o</div></div>')
        ensure_ai_label(page, "naver", ("AI 활용 설정",), timeout_ms=800)
        self.assertEqual(page.locator('[role="switch"]').get_attribute("aria-checked"), "true")

    def test_hidden_checkbox_is_toggled(self) -> None:
        page = self._page('<label><span>AI 활용 설정</span><input type="checkbox" style="display:none"></label>')
        ensure_ai_label(page, "naver", ("AI 활용 설정",), timeout_ms=800)
        self.assertTrue(page.locator("input").is_checked())

    def test_reveal_opens_hidden_section(self) -> None:
        page = self._page(
            '<div role="button" onclick="document.getElementById(\'adv\').style.display=\'block\'">고급 설정</div>'
            '<div id="adv" style="display:none"><div><span>AI 레이블 추가</span>'
            '<div role="switch" aria-checked="false" onclick="t(this)">o</div></div></div>'
        )
        ensure_ai_label(
            page, "instagram", ("AI 레이블 추가",), reveal=('div[role="button"]:has-text("고급 설정")',), timeout_ms=800
        )
        self.assertEqual(page.locator('[role="switch"]').get_attribute("aria-checked"), "true")

    def test_nested_switch_markup_counts_as_one(self) -> None:
        # TikTok Studio 실측 구조: aria-checked 컨테이너 안에 input[role=switch]
        page = self._page(
            '<div data-e2e="aigc_container"><span>AI 생성 콘텐츠</span><div class="switch">'
            '<div class="Switch__content" aria-checked="false" onclick="t(this)">'
            '<input role="switch" type="checkbox" aria-hidden="true" style="appearance:none"></div></div></div>'
        )
        ensure_ai_label(page, "tiktok", ("AI 생성 콘텐츠",), timeout_ms=800)
        self.assertEqual(page.locator(".Switch__content").get_attribute("aria-checked"), "true")

    def test_instagram_aria_checked_input_switch(self) -> None:
        # Instagram 실측 구조: input[role=switch] 의 상태가 aria-checked 로만 바뀐다
        page = self._page(
            '<div><div><span>AI 레이블 추가</span></div><div><div>'
            '<input role="switch" type="checkbox" aria-checked="false"'
            ' onclick="event.preventDefault(); t(this)"></div></div></div>'
        )
        ensure_ai_label(page, "instagram", ("AI 레이블 추가",), timeout_ms=800)
        self.assertEqual(page.locator("input").get_attribute("aria-checked"), "true")

    def test_missing_label_fails_closed(self) -> None:
        self._assert_fail_closed(self._page('<div><span>광고 · 협찬 설정</span><div role="switch" aria-checked="false">o</div></div>'))

    def test_ambiguous_switches_fail_closed(self) -> None:
        self._assert_fail_closed(self._page(
            '<div><span>AI 활용 설정</span><div role="switch" aria-checked="false"></div>'
            '<div role="switch" aria-checked="false"></div></div>'
        ))

    def test_switch_that_does_not_turn_on_fails_closed(self) -> None:
        self._assert_fail_closed(self._page('<div><span>AI 활용 설정</span><div role="switch" aria-checked="false">o</div></div>'))

    def test_unreadable_state_fails_closed(self) -> None:
        self._assert_fail_closed(self._page('<div><span>AI 활용 설정</span><div role="switch">o</div></div>'))
