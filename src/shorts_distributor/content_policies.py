"""Declarative, creator-owned rules for exceptional content.

The core pipeline must not contain campaign, sponsor, or creator-specific names.
Those decisions belong in an untracked ``config/content-policies.json`` file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .caption import build_caption
from .platforms import normalize_platform_id
from .youtube import VideoMeta

TEXT_MODES = {"default", "caption", "title", "description"}
SUPPORTED_DISCLOSURES = {
    "instagram": {"paid-partnership", "ai-generated"},
    "tiktok": {"branded-content", "ai-generated"},
    "naver": {"ai-generated"},
}


@dataclass(frozen=True)
class PolicyDecision:
    post_text: str
    matched_rules: tuple[str, ...] = ()
    allowed_platforms: frozenset[str] | None = None
    disclosures: dict[str, tuple[str, ...]] | None = None


def _text_for_mode(meta: VideoMeta, mode: str, default_mode: str) -> str:
    effective = default_mode if mode == "default" else mode
    if effective == "title":
        return meta.title.strip()
    if effective == "description":
        return meta.description.strip() or meta.title.strip()
    if effective == "caption":
        return build_caption(meta)
    raise ValueError(f"Unsupported post text mode: {effective}")


def _string_list(value: object, field: str, *, allow_empty: bool = True) -> list[str]:
    if value is None and allow_empty:
        return []
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise ValueError(f"{field} must be a list of non-empty strings.")
    return [item.strip() for item in value]


def load_policy_rules(path: Path | None) -> list[dict]:
    if path is None:
        return []
    if not path.exists():
        raise ValueError(f"CONTENT_POLICIES_FILE does not exist: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read content policies from {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Content policies must be an object with version: 1.")
    rules = document.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("Content policy rules must be a list.")

    seen: set[str] = set()
    for index, rule in enumerate(rules):
        label = f"rules[{index}]"
        if not isinstance(rule, dict):
            raise ValueError(f"{label} must be an object.")
        name = rule.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label}.name must be a non-empty string.")
        if name in seen:
            raise ValueError(f"Duplicate content policy name: {name}")
        seen.add(name)

        match = rule.get("match")
        if not isinstance(match, dict):
            raise ValueError(f"{label}.match must be an object.")
        always = match.get("always", False)
        if not isinstance(always, bool):
            raise ValueError(f"{label}.match.always must be true or false.")
        any_markers = _string_list(match.get("any"), f"{label}.match.any")
        all_markers = _string_list(match.get("all"), f"{label}.match.all")
        if always and (any_markers or all_markers):
            raise ValueError(f"{label}.match.always cannot be combined with any/all markers.")
        if not always and not any_markers and not all_markers:
            raise ValueError(f"{label}.match needs always: true or at least one marker in any or all.")

        if "platforms" in rule:
            platforms = _string_list(rule["platforms"], f"{label}.platforms", allow_empty=False)
            for platform in platforms:
                normalize_platform_id(platform)

        post_text = rule.get("post_text", {})
        if not isinstance(post_text, dict):
            raise ValueError(f"{label}.post_text must be an object.")
        mode = post_text.get("mode", "default")
        if mode not in TEXT_MODES:
            raise ValueError(f"{label}.post_text.mode must be one of: {', '.join(sorted(TEXT_MODES))}")
        for field in ("prepend", "append"):
            if field in post_text and not isinstance(post_text[field], str):
                raise ValueError(f"{label}.post_text.{field} must be a string.")

        disclosures = rule.get("disclosures", {})
        if not isinstance(disclosures, dict):
            raise ValueError(f"{label}.disclosures must be an object.")
        for platform, values in disclosures.items():
            normalize_platform_id(platform)
            actions = _string_list(values, f"{label}.disclosures.{platform}")
            unsupported = sorted(set(actions) - SUPPORTED_DISCLOSURES.get(platform, set()))
            if unsupported:
                raise ValueError(
                    f"{label}.disclosures.{platform} contains unsupported actions: "
                    f"{', '.join(unsupported)}"
                )
    return rules


def evaluate_content_policies(
    meta: VideoMeta,
    *,
    default_mode: str,
    policy_file: Path | None,
) -> PolicyDecision:
    rules = load_policy_rules(policy_file)
    searchable = "\n".join((meta.title, meta.description, " ".join(meta.tags))).casefold()
    post_text = _text_for_mode(meta, "default", default_mode)
    matched: list[str] = []
    allowed: set[str] | None = None
    disclosures: dict[str, set[str]] = {}

    for rule in rules:
        match = rule["match"]
        # always: 채널 전체에 적용되는 규칙(예: 모든 영상이 AI 생성 콘텐츠).
        any_markers = [marker.casefold() for marker in match.get("any", [])]
        all_markers = [marker.casefold() for marker in match.get("all", [])]
        if any_markers and not any(marker in searchable for marker in any_markers):
            continue
        if all_markers and not all(marker in searchable for marker in all_markers):
            continue

        matched.append(rule["name"])
        if "platforms" in rule:
            rule_platforms = {normalize_platform_id(platform) for platform in rule["platforms"]}
            allowed = rule_platforms if allowed is None else allowed & rule_platforms

        text_rule = rule.get("post_text", {})
        mode = text_rule.get("mode", "default")
        if mode != "default":
            post_text = _text_for_mode(meta, mode, default_mode)
        post_text = f"{text_rule.get('prepend', '')}{post_text}{text_rule.get('append', '')}"

        for platform, values in rule.get("disclosures", {}).items():
            normalized = normalize_platform_id(platform)
            disclosures.setdefault(normalized, set()).update(values)

    return PolicyDecision(
        post_text=post_text.strip(),
        matched_rules=tuple(matched),
        allowed_platforms=frozenset(allowed) if allowed is not None else None,
        disclosures={platform: tuple(sorted(values)) for platform, values in disclosures.items()},
    )
