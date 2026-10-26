import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from shorts_distributor.content_policies import evaluate_content_policies, load_policy_rules
from shorts_distributor.youtube import VideoMeta


def _meta(*, title: str = "A useful Short", description: str = "Full description") -> VideoMeta:
    return VideoMeta(
        id="video-id",
        title=title,
        description=description,
        tags=["example"],
        duration=30,
        upload_date="20260901",
        timestamp=1,
        webpage_url="https://example.invalid/video-id",
        file_path=Path("video.mp4"),
    )


class ContentPolicyTest(TestCase):
    def _policy_file(self, document: dict, directory: str) -> Path:
        path = Path(directory) / "content-policies.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_matching_rule_controls_text_platforms_and_disclosures(self) -> None:
        document = {
            "version": 1,
            "rules": [{
                "name": "campaign-example",
                "match": {"all": ["campaign-marker", "review-code"]},
                "platforms": ["instagram", "tiktok"],
                "post_text": {"mode": "description", "prepend": "#ad\n\n"},
                "disclosures": {
                    "instagram": ["paid-partnership"],
                    "tiktok": ["branded-content"],
                },
            }],
        }
        with TemporaryDirectory() as directory:
            path = self._policy_file(document, directory)
            decision = evaluate_content_policies(
                _meta(description="Campaign-marker details\nReview-code 123"),
                default_mode="title",
                policy_file=path,
            )

        self.assertEqual(decision.post_text, "#ad\n\nCampaign-marker details\nReview-code 123")
        self.assertEqual(decision.matched_rules, ("campaign-example",))
        self.assertEqual(decision.allowed_platforms, frozenset({"instagram", "tiktok"}))
        self.assertEqual(decision.disclosures["instagram"], ("paid-partnership",))
        self.assertEqual(decision.disclosures["tiktok"], ("branded-content",))

    def test_non_matching_content_keeps_default_text(self) -> None:
        with TemporaryDirectory() as directory:
            path = self._policy_file({
                "version": 1,
                "rules": [{
                    "name": "not-this-video",
                    "match": {"any": ["missing-marker"]},
                    "post_text": {"mode": "description"},
                }],
            }, directory)
            decision = evaluate_content_policies(
                _meta(), default_mode="title", policy_file=path
            )

        self.assertEqual(decision.post_text, "A useful Short")
        self.assertEqual(decision.matched_rules, ())
        self.assertIsNone(decision.allowed_platforms)

    def test_invalid_rule_fails_before_upload(self) -> None:
        with TemporaryDirectory() as directory:
            path = self._policy_file({
                "version": 1,
                "rules": [{
                    "name": "bad-rule",
                    "match": {"any": ["marker"]},
                    "post_text": {"mode": "invented-mode"},
                }],
            }, directory)
            with self.assertRaisesRegex(ValueError, "post_text.mode"):
                load_policy_rules(path)

    def test_unsupported_disclosure_fails_before_upload(self) -> None:
        with TemporaryDirectory() as directory:
            path = self._policy_file({
                "version": 1,
                "rules": [{
                    "name": "bad-disclosure",
                    "match": {"any": ["marker"]},
                    "disclosures": {"facebook": ["invented-control"]},
                }],
            }, directory)
            with self.assertRaisesRegex(ValueError, "unsupported actions"):
                load_policy_rules(path)

    def test_empty_platform_allowlist_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = self._policy_file({
                "version": 1,
                "rules": [{
                    "name": "empty-platforms",
                    "match": {"any": ["marker"]},
                    "platforms": [],
                }],
            }, directory)
            with self.assertRaisesRegex(ValueError, "platforms"):
                load_policy_rules(path)

    def test_always_rule_applies_ai_label_to_every_video(self) -> None:
        document = {
            "version": 1,
            "rules": [{
                "name": "ai-generated-channel",
                "match": {"always": True},
                "platforms": ["instagram", "tiktok", "naver"],
                "disclosures": {
                    "instagram": ["ai-generated"],
                    "tiktok": ["ai-generated"],
                    "naver": ["ai-generated"],
                },
            }],
        }
        with TemporaryDirectory() as directory:
            path = self._policy_file(document, directory)
            decision = evaluate_content_policies(_meta(), default_mode="title", policy_file=path)

        self.assertEqual(decision.post_text, "A useful Short")
        self.assertEqual(decision.allowed_platforms, frozenset({"instagram", "tiktok", "naver"}))
        for platform in ("instagram", "tiktok", "naver"):
            self.assertEqual(decision.disclosures[platform], ("ai-generated",))

    def test_always_cannot_be_combined_with_markers(self) -> None:
        with TemporaryDirectory() as directory:
            path = self._policy_file({
                "version": 1,
                "rules": [{"name": "bad", "match": {"always": True, "any": ["x"]}}],
            }, directory)
            with self.assertRaisesRegex(ValueError, "always"):
                load_policy_rules(path)

    def test_ai_label_on_unsupported_platform_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = self._policy_file({
                "version": 1,
                "rules": [{
                    "name": "ai",
                    "match": {"always": True},
                    "disclosures": {"threads": ["ai-generated"]},
                }],
            }, directory)
            with self.assertRaisesRegex(ValueError, "unsupported"):
                load_policy_rules(path)
