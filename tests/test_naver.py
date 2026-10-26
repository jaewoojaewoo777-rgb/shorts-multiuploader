from unittest import TestCase

from shorts_distributor.uploaders.naver import _shorten_title, _utf16_units


class NaverTitleTest(TestCase):
    def test_shorten_title_uses_utf16_counter_for_astral_emoji(self) -> None:
        title = "🚨" + ("가" * 23)

        shortened = _shorten_title(title)

        self.assertEqual(shortened, "🚨" + ("가" * 21) + "…")
        self.assertEqual(_utf16_units(shortened), 24)

    def test_short_title_is_unchanged(self) -> None:
        title = "AI 업데이트 소식"

        self.assertEqual(_shorten_title(title), title)

    def test_pipe_title_uses_last_segment_that_fits(self) -> None:
        title = "출항 시간 딱 맞춰 왔는데… 배가 떠났습니다 | 선상낚시 처음 가면 100% 하는 실수"

        self.assertEqual(_shorten_title(title), "선상낚시 처음 가면 100% 하는 실수")

    def test_pipe_title_falls_back_to_truncation(self) -> None:
        title = ("가" * 30) + " | " + ("나" * 30)

        self.assertEqual(_utf16_units(_shorten_title(title)), 24)
