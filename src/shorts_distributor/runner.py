"""코드 우선 업로드 파이프라인.

역할 분담(프로젝트 방향):
  - 이 러너(Playwright)가 기본 업로드 경로다. 토큰을 쓰지 않는다.
  - 에이전트(browser-use/computer-use)는 세 경우에만 개입한다:
      1) 셀이 failed/blocked 로 끝났을 때(리포트의 인계 브리프 사용)
      2) 검증이 missing/inconclusive 인 핵심 확인 지점
      3) UI 리뉴얼로 셀렉터가 깨졌을 때(확인 후 uploaders/<platform>.py 수정)

흐름: plan → prepare(+h264) → 시간순 정렬 → 플랫폼 레인별 업로드 → 검증 → 기록 →
data/runs/<run_id>/report.json + 아티팩트.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import state
from .ads import detect_ad_markers
from .caption import build_caption
from .config import Config
from .content_policies import evaluate_content_policies
from .media import ensure_h264, video_codec
from .platforms import normalize_platform_id
from .uploaders import get_uploader
from .uploaders.base import (
    Job,
    StepFailure,
    Steps,
    VerifyOutcome,
    has_login_cookie,
    login_cookie_present,
    platform_context,
    profile_dir_for,
)
from .youtube import download_video, get_channel_shorts

VIDEO_COOLDOWN_S = 8

# 러너를 마치는 셀 상태. 나머지(failed/blocked/agent-required)는 에이전트 인계 대상.
CLOSED_STATUSES = {
    "verified",
    "published",
    "pending-verify",
    "already",
    "skipped-ad",
    "skipped-policy",
}


# ---------------------------------------------------------------- 후보/준비

def entry_with_ad_flags(entry: dict) -> dict:
    markers = detect_ad_markers(entry.get("title"), entry.get("description"))
    return {**entry, "ad_suspected": bool(markers), "ad_markers": markers}


def _entry_missing_platforms(entry: dict, platforms: list[str]) -> list[str]:
    return [p for p in platforms if not state.is_uploaded(entry["id"], p)]


def plan_batch(
    cfg: Config,
    *,
    lookback_limit: int | None = None,
    upload_limit: int | None = None,
    selection_mode: str | None = None,
    skip_ads: bool | None = None,
    target_platforms: list[str] | None = None,
) -> dict:
    """env 정책(.env 캡)을 적용한 업로드 후보 배치. 러너/plan 명령의 단일 진실원."""
    mode = selection_mode or cfg.shorts_selection_mode
    if mode not in {"recent", "all"}:
        raise ValueError("selection mode must be either 'recent' or 'all'.")
    effective_lookback = lookback_limit or cfg.shorts_lookback_limit
    if upload_limit == "all":  # 명시적 무제한 요청 — env 캡 오버라이드
        effective_upload_limit = None
    elif upload_limit is None:
        effective_upload_limit = cfg.shorts_upload_limit
    else:
        effective_upload_limit = upload_limit
    effective_skip_ads = cfg.shorts_skip_ads if skip_ads is None else skip_ads
    platforms = target_platforms or cfg.target_platforms
    if effective_lookback < 1:
        raise ValueError("lookback limit must be a positive integer.")
    if effective_upload_limit is not None and effective_upload_limit < 1:
        raise ValueError("upload limit must be a positive integer or 'all'.")

    entries = [
        entry_with_ad_flags(e)
        for e in get_channel_shorts(
            cfg.youtube_handle,
            limit=effective_lookback,
            metadata_lang=cfg.youtube_metadata_lang or None,
        )
    ]
    selected: list[dict] = []
    ad_skipped: list[dict] = []
    ad_included: list[dict] = []
    for entry in entries:
        missing_platforms = _entry_missing_platforms(entry, platforms)
        if not missing_platforms:
            continue
        planned = {
            "youtube_id": entry["id"],
            "title": entry.get("title") or "",
            "url": entry.get("url"),
            "missing_platforms": missing_platforms,
            "ad_suspected": entry["ad_suspected"],
            "ad_markers": entry["ad_markers"],
        }
        if entry["ad_suspected"]:
            if effective_skip_ads:
                ad_skipped.append(planned)
                continue
            ad_included.append(planned)
        selected.append(planned)
        if mode == "recent" and effective_upload_limit is not None and len(selected) >= effective_upload_limit:
            break

    if mode == "all" and effective_upload_limit is not None:
        selected = selected[:effective_upload_limit]

    return {
        "channel": cfg.youtube_handle,
        "selection_mode": mode,
        "upload_limit": effective_upload_limit,
        "lookback_limit": effective_lookback,
        "skip_ads": effective_skip_ads,
        "target_platforms": platforms,
        "inspected": len(entries),
        "selected_newest_first": selected,
        "ad_skipped": ad_skipped,
        "ad_included": ad_included,
        "upload_order_note": (
            "Prepare these selected IDs, then upload them sorted by "
            "upload_date/timestamp ascending."
        ),
    }


def prepare_video(cfg: Config, video_id: str) -> dict:
    """다운로드 + 게시 텍스트 생성. 업로드 셀의 입력값."""
    meta = download_video(video_id, cfg.download_dir)
    caption = build_caption(meta)
    ad_markers = detect_ad_markers(meta.title, meta.description, " ".join(meta.tags))
    decision = evaluate_content_policies(
        meta,
        default_mode=cfg.post_text_mode,
        policy_file=cfg.content_policies_file,
    )
    return {
        "youtube_id": meta.id,
        "title": meta.title,
        "title_text": meta.title,
        "description": meta.description,
        "duration": meta.duration,
        "upload_date": meta.upload_date,
        "timestamp": meta.timestamp,
        "webpage_url": meta.webpage_url,
        "file_path": str(meta.file_path.resolve()),
        "video_codec": video_codec(meta.file_path),
        "caption": caption,
        "post_text": decision.post_text,
        "post_text_mode": cfg.post_text_mode,
        "policy_names": list(decision.matched_rules),
        "allowed_platforms": (
            sorted(decision.allowed_platforms) if decision.allowed_platforms is not None else None
        ),
        "disclosures": {
            platform: list(values)
            for platform, values in (decision.disclosures or {}).items()
        },
        "ad_suspected": bool(ad_markers),
        "ad_markers": ad_markers,
    }


# ---------------------------------------------------------------- 셀/리포트

@dataclass
class Cell:
    platform: str
    youtube_id: str
    title: str = ""
    status: str = "planned"
    # planned | verified | published | pending-verify | already | skipped-ad | skipped-policy
    # | failed | blocked | agent-required
    step: str | None = None
    error: str | None = None
    hint: str = ""
    page_url: str | None = None
    post_url: str | None = None
    posted_text: str | None = None
    evidence: str = ""
    verify_status: str = ""  # verified | missing | inconclusive | ""
    artifacts: list[str] = field(default_factory=list)
    file_path: str = ""
    post_text: str = ""
    disclosures: list[str] = field(default_factory=list)

    def needs_agent(self) -> bool:
        return self.status not in CLOSED_STATUSES or self.verify_status == "missing"

    def agent_brief(self) -> str:
        """browser-use/computer-use 에 그대로 넘길 자연어 인계 브리프."""
        lines = [
            f"[에이전트 인계 — {self.platform} / {self.youtube_id}]",
            f"상태: {self.status}" + (f" (실패 단계: {self.step})" if self.step else ""),
        ]
        if self.error:
            lines.append(f"에러: {self.error}")
        if self.hint:
            lines.append(f"힌트: {self.hint}")
        if self.verify_status:
            lines.append(f"코드 검증 결과: {self.verify_status} — {self.evidence}")
        if self.page_url:
            lines.append(f"실패 시점 페이지: {self.page_url}")
        if self.artifacts:
            lines.append("아티팩트: " + ", ".join(self.artifacts))
        lines += [
            f"Chrome 프로필: data/profiles/{self.platform} (로그인 세션 보유)",
            f"영상 파일: {self.file_path}",
            "게시 텍스트(이 텍스트만 정확히, ID/파일명/URL 붙이기 금지):",
            self.post_text,
            *([f"필수 공개 표시(켜진 상태를 확인하지 못하면 게시 금지): {', '.join(self.disclosures)}"]
              if self.disclosures else []),
            "규칙: 게시 전 최근 게시물에서 중복 확인, 화면의 파일/문구가 위 값과 일치할 때만 게시.",
            f"성공·검증 후 기록: uv run shorts-dist mark-uploaded {self.platform} {self.youtube_id} --url '<POST_URL>' --source agent",
        ]
        return "\n".join(lines)


def _apply_failure(cell: Cell, failure: StepFailure) -> None:
    cell.status = "blocked" if failure.blocker else "failed"
    cell.step = failure.step
    cell.error = failure.message
    cell.hint = failure.hint
    cell.page_url = failure.page_url
    cell.artifacts.extend(failure.artifacts)


# ---------------------------------------------------------------- 레인 실행

def _job_for(prepared: dict, platform: str) -> Job:
    return Job(
        platform=platform,
        youtube_id=prepared["youtube_id"],
        file_path=Path(prepared.get("upload_file_path") or prepared["file_path"]),
        post_text=prepared["post_text"],
        title_text=prepared["title_text"],
        upload_date=prepared.get("upload_date"),
        timestamp=prepared.get("timestamp"),
        disclosures=tuple(prepared.get("disclosures", {}).get(platform, ())),
    )


def _verify_cell(ctx, module, job: Job, cfg: Config, run_dir: Path) -> VerifyOutcome:
    page = ctx.new_page()
    steps = Steps(page, run_dir, f"{job.platform}-{job.youtube_id}-verify")
    try:
        return module.verify(page, job, steps, cfg)
    except StepFailure as failure:
        return VerifyOutcome("inconclusive", evidence=f"검증 실패({failure.step}): {failure.message}", artifacts=failure.artifacts)
    finally:
        try:
            page.close()
        except Exception:
            pass


def run_lane(
    cfg: Config,
    platform: str,
    lane: list[Cell],
    prepared_by_id: dict[str, dict],
    run_dir: Path,
    *,
    do_verify: bool,
    headless: bool | None,
) -> None:
    """한 플랫폼 레인을 자체 컨텍스트로 처리(per-platform 전략/단건 업로드용)."""
    try:
        with platform_context(cfg, platform, headless=headless) as ctx:
            _run_lane_in_ctx(ctx, cfg, platform, lane, prepared_by_id, run_dir, do_verify=do_verify)
    except StepFailure as failure:
        # 프로필 없음/사용 중/Chrome 실행 실패 — 레인 전체 blocked.
        for cell in lane:
            if cell.status == "planned":
                _apply_failure(cell, failure)


def _run_lane_in_ctx(
    ctx,
    cfg: Config,
    platform: str,
    lane: list[Cell],
    prepared_by_id: dict[str, dict],
    run_dir: Path,
    *,
    do_verify: bool,
) -> None:
    """한 플랫폼 레인을 시간순으로 처리. blocker 발생 시 나머지 셀은 중단 처리."""
    module = get_uploader(platform)
    if module is None:
        for cell in lane:
            cell.status = "agent-required"
            cell.hint = "레지스트리에 없는 플랫폼 — references/platform-guides.md 가이드로 에이전트가 직접 업로드"
        return

    blocked_reason: str | None = None
    for index, cell in enumerate(lane):
        if blocked_reason:
            cell.status = "blocked"
            cell.error = f"레인 중단: {blocked_reason}"
            cell.hint = "선행 blocker 해소 후 재실행"
            continue
        prepared = prepared_by_id[cell.youtube_id]
        job = _job_for(prepared, platform)
        page = ctx.new_page()
        steps = Steps(page, run_dir, f"{platform}-{cell.youtube_id}")
        try:
            outcome = module.upload(page, job, steps, cfg)
            cell.status = outcome.status
            cell.post_url = outcome.post_url
            cell.posted_text = outcome.posted_text
            cell.evidence = outcome.evidence
            state.mark_uploaded(
                cell.youtube_id,
                platform,
                platform_url=outcome.post_url,
                caption=outcome.posted_text,
                source="script",
                verified=False,
            )
        except StepFailure as failure:
            _apply_failure(cell, failure)
            if failure.blocker:
                blocked_reason = failure.message
        finally:
            try:
                page.close()
            except Exception:
                pass

        # confirm 단계 실패는 게시됐을 수 있는 false negative(LinkedIn/Facebook 등)
        # — 재업로드 대신 검증으로 판정한다(중복 업로드 하드 룰).
        if (
            cell.status == "failed"
            and cell.step == "confirm"
            and do_verify
            and platform != "instagram"
        ):
            verdict = _verify_cell(ctx, module, job, cfg, run_dir)
            cell.verify_status = verdict.status
            cell.artifacts.extend(verdict.artifacts)
            if verdict.status == "verified":
                cell.status = "verified"
                cell.post_url = cell.post_url or verdict.url
                cell.evidence = f"confirm 타임아웃 후 검증으로 확정: {verdict.evidence}"
                state.mark_uploaded(
                    cell.youtube_id,
                    platform,
                    platform_url=cell.post_url,
                    caption=job.post_text,
                    source="script",
                    verified=True,
                )
        elif cell.status == "published" and do_verify:
            if platform == "instagram":
                # 게시 직후 릴스 탭을 바로 확인한다 — 성공한 릴스는 수 초 내
                # 노출된다(운영자 실측). 보이면 그 자리에서 verified 로 종료.
                # 안 보이면 재업로드 대신 pending 으로 남겨 잠시 후 재확인한다
                # (성공 다이얼로그 없이 끝난 모호 셀 대비 — 중복 방지).
                verdict = _verify_cell(ctx, module, job, cfg, run_dir)
                if verdict.status == "verified":
                    cell.status = "verified"
                    cell.verify_status = "verified"
                    cell.post_url = cell.post_url or verdict.url
                    cell.evidence = verdict.evidence or cell.evidence
                    state.mark_verified(cell.youtube_id, platform, platform_url=cell.post_url)
                else:
                    cell.status = "pending-verify"
                    cell.verify_status = verdict.status
                    cell.hint = (
                        "성공 다이얼로그 후에도 릴스 탭 미확인 — 잠시 후(1~2분) "
                        "`uv run shorts-dist verify --pending` 재확인, 그래도 없으면 실패 취급"
                    )
            elif cell.post_url:
                # 성공 토스트에서 실제 게시물 URL 을 캡처한 경우 — 직접 증거.
                # 게시 직후 검색/피드 검증은 색인 지연 false-negative 가 잦다.
                cell.status = "verified"
                cell.verify_status = "verified"
                cell.evidence = (cell.evidence + " | post URL 캡처로 검증 갈음").strip(" |")
                state.mark_verified(cell.youtube_id, platform, platform_url=cell.post_url)
            else:
                verdict = _verify_cell(ctx, module, job, cfg, run_dir)
                cell.verify_status = verdict.status
                cell.evidence = verdict.evidence or cell.evidence
                cell.artifacts.extend(verdict.artifacts)
                if verdict.status == "verified":
                    cell.status = "verified"
                    cell.post_url = cell.post_url or verdict.url
                    state.mark_verified(cell.youtube_id, platform, platform_url=cell.post_url)

        if index < len(lane) - 1:
            time.sleep(VIDEO_COOLDOWN_S)


# ---------------------------------------------------------------- 검증 스윕

def sweep_pending(
    cfg: Config,
    run_dir: Path,
    *,
    platform_filter: list[str] | None = None,
    headless: bool | None = None,
) -> list[dict]:
    """script 업로드 중 미검증 셀을 코드로 검증."""
    rows = state.pending_verification()
    results: list[dict] = []
    by_platform: dict[str, list[dict]] = {}
    for row in rows:
        platform = row["platform"]
        if platform_filter and platform not in platform_filter:
            continue
        if get_uploader(platform) is None:
            continue
        by_platform.setdefault(platform, []).append(row)

    if not by_platform:
        return results

    # shared 프로필이면 한 컨텍스트로 전 플랫폼을 검증한다.
    platforms = list(by_platform)
    if cfg.profile_strategy == "shared":
        groups: list[tuple[str, list[str]]] = [(platforms[0], platforms)]
    else:
        groups = [(p, [p]) for p in platforms]

    for anchor, group_platforms in groups:
        try:
            with platform_context(cfg, anchor, headless=headless) as ctx:
                for platform in group_platforms:
                    module = get_uploader(platform)
                    for row in by_platform[platform]:
                        caption = row.get("caption") or ""
                        if not caption:
                            results.append({**row, "verify_status": "inconclusive", "evidence": "기록된 캡션 없음 — 에이전트 확인 필요"})
                            continue
                        job = Job(
                            platform=platform,
                            youtube_id=row["youtube_id"],
                            file_path=Path(),
                            post_text=caption,
                            title_text=caption.splitlines()[0] if caption else "",
                        )
                        verdict = _verify_cell(ctx, module, job, cfg, run_dir)
                        if verdict.status == "verified":
                            state.mark_verified(row["youtube_id"], platform, platform_url=verdict.url)
                        results.append({
                            **row,
                            "verify_status": verdict.status,
                            "evidence": verdict.evidence,
                            "post_url": verdict.url or row.get("platform_url"),
                            "artifacts": verdict.artifacts,
                        })
        except StepFailure as failure:
            for platform in group_platforms:
                for row in by_platform[platform]:
                    results.append({**row, "verify_status": "inconclusive", "evidence": f"{failure.step}: {failure.message}"})
    return results


# ---------------------------------------------------------------- 배치 실행

def _new_run_dir(cfg: Config) -> tuple[str, Path]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = cfg.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def run_batch(
    cfg: Config,
    *,
    platforms: list[str] | None = None,
    video_ids: list[str] | None = None,
    upload_limit: int | None = None,
    lookback_limit: int | None = None,
    selection_mode: str | None = None,
    skip_ads: bool | None = None,
    dry_run: bool = False,
    do_verify: bool = True,
    headless: bool | None = None,
) -> dict:
    run_id, run_dir = _new_run_dir(cfg)
    target_platforms = (
        [normalize_platform_id(p) for p in platforms] if platforms else list(cfg.target_platforms)
    )

    # 0) 이전 run 의 미검증(script) 셀 스윕.
    pending_results: list[dict] = []
    if do_verify and not dry_run:
        pending_results = sweep_pending(cfg, run_dir, platform_filter=target_platforms, headless=headless)

    # 1) 후보 선정: 명시적 --video 는 후보 선정만 우회한다. 콘텐츠 정책은 항상 적용한다.
    plan: dict | None = None
    ad_skipped: list[dict] = []
    if video_ids:
        selected_ids = list(dict.fromkeys(video_ids))
    else:
        plan = plan_batch(
            cfg,
            lookback_limit=lookback_limit,
            upload_limit=upload_limit,
            selection_mode=selection_mode,
            skip_ads=skip_ads,
            target_platforms=target_platforms,
        )
        selected_ids = [item["youtube_id"] for item in plan["selected_newest_first"]]
        ad_skipped = plan["ad_skipped"]

    # 2) 준비(다운로드 + 텍스트 + h264 보장). 실패한 영상은 전 플랫폼 failed 셀로.
    prepared_by_id: dict[str, dict] = {}
    prepare_failures: dict[str, str] = {}
    for video_id in selected_ids:
        try:
            prepared = prepare_video(cfg, video_id)
            upload_path, codec_note = ensure_h264(Path(prepared["file_path"]))
            prepared["upload_file_path"] = str(upload_path)
            prepared["codec_note"] = codec_note
            prepared_by_id[video_id] = prepared
        except Exception as exc:  # yt-dlp/ffmpeg 실패
            prepare_failures[video_id] = str(exc)[:300]

    # 3) 시간순 정렬(오래된 것 먼저) — SNS 피드 정렬 하드 룰.
    batch = sorted(
        prepared_by_id.values(),
        key=lambda p: (p.get("upload_date") or "", p.get("timestamp") or 0, p["youtube_id"]),
    )

    cells: list[Cell] = []
    for video_id, error in prepare_failures.items():
        for platform in target_platforms:
            cells.append(Cell(platform, video_id, status="failed", step="prepare", error=error,
                              hint="yt-dlp/ffmpeg 로컬 준비 실패 — 네트워크/영상 상태 확인"))

    # 4) 플랫폼 레인 구성(cfg 순서 = 업로드 순서). 레인 안은 시간순.
    exec_lanes: list[tuple[str, list[Cell]]] = []
    for platform in target_platforms:
        lane: list[Cell] = []
        for prepared in batch:
            video_id = prepared["youtube_id"]
            if state.is_uploaded(video_id, platform):
                cells.append(Cell(platform, video_id, title=prepared["title"], status="already"))
                continue
            allowed = prepared.get("allowed_platforms")
            if allowed is not None and platform not in allowed:
                policy_names = ", ".join(prepared.get("policy_names") or [])
                cells.append(Cell(
                    platform,
                    video_id,
                    title=prepared["title"],
                    status="skipped-policy",
                    hint=f"Excluded by content policy: {policy_names or 'unnamed policy'}",
                ))
                continue
            lane.append(Cell(
                platform,
                video_id,
                title=prepared["title"],
                file_path=prepared.get("upload_file_path") or prepared["file_path"],
                post_text=prepared["post_text"],
                disclosures=list(prepared.get("disclosures", {}).get(platform, ())),
            ))
        if not lane:
            continue
        if get_uploader(platform) is None:
            for cell in lane:
                cell.status = "agent-required"
                cell.hint = "레지스트리에 없는 플랫폼 — references/platform-guides.md 가이드로 에이전트가 직접 업로드"
            cells.extend(lane)
            continue
        exec_lanes.append((platform, lane))

    if dry_run:
        for _, lane in exec_lanes:
            cells.extend(lane)  # status=planned 그대로 리포트에 노출
        report = _build_report(
            run_id, run_dir, cfg, target_platforms, batch, cells, plan, pending_results, ad_skipped,
            dry_run=True,
        )
        _write_report(run_dir, report)
        return report

    # 5) 실행 — shared 전략이면 브라우저 컨텍스트 1개를 전 레인이 재사용.
    if exec_lanes:
        if cfg.profile_strategy == "shared":
            try:
                with platform_context(cfg, exec_lanes[0][0], headless=headless) as ctx:
                    for i, (platform, lane) in enumerate(exec_lanes):
                        _run_lane_in_ctx(ctx, cfg, platform, lane, prepared_by_id, run_dir, do_verify=do_verify)
                        if i < len(exec_lanes) - 1:
                            time.sleep(3)
            except StepFailure as failure:
                for _, lane in exec_lanes:
                    for cell in lane:
                        if cell.status == "planned":
                            _apply_failure(cell, failure)
        else:
            for platform, lane in exec_lanes:
                run_lane(cfg, platform, lane, prepared_by_id, run_dir, do_verify=do_verify, headless=headless)
                time.sleep(3)
        for _, lane in exec_lanes:
            cells.extend(lane)

    report = _build_report(
        run_id, run_dir, cfg, target_platforms, batch, cells, plan, pending_results, ad_skipped,
        dry_run=False,
    )
    _write_report(run_dir, report)
    return report


def _build_report(
    run_id: str,
    run_dir: Path,
    cfg: Config,
    target_platforms: list[str],
    batch: list[dict],
    cells: list[Cell],
    plan: dict | None,
    pending_results: list[dict],
    ad_skipped: list[dict],
    *,
    dry_run: bool,
) -> dict:
    attention = [] if dry_run else [c for c in cells if c.needs_agent()]
    exit_code = 0 if not attention else 3
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "dry_run": dry_run,
        "channel": cfg.youtube_handle,
        "target_platforms": target_platforms,
        "batch_oldest_first": [
            {
                "youtube_id": p["youtube_id"],
                "title": p["title"],
                "upload_date": p.get("upload_date"),
                "file": p.get("upload_file_path") or p["file_path"],
                "codec": p.get("codec_note", p.get("video_codec", "")),
                "post_text": p["post_text"],
                "policy_names": p.get("policy_names", []),
                "allowed_platforms": p.get("allowed_platforms"),
                "disclosures": p.get("disclosures", {}),
            }
            for p in batch
        ],
        "cells": [asdict(c) for c in cells],
        "agent_briefs": [c.agent_brief() for c in attention],
        "pending_verification_sweep": pending_results,
        "ad_skipped": ad_skipped,
        "plan": plan,
        "exit_code": exit_code,
    }


def _write_report(run_dir: Path, report: dict) -> None:
    import json

    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------- 단건/검증/환경

def upload_single(
    cfg: Config,
    platform: str,
    video_id: str,
    *,
    force: bool = False,
    do_verify: bool = True,
    headless: bool | None = None,
) -> dict:
    """단일 (video, platform) 셀 업로드. 스킬/에이전트의 부분 재시도용."""
    platform = normalize_platform_id(platform)
    run_id, run_dir = _new_run_dir(cfg)
    if state.is_uploaded(video_id, platform) and not force:
        cell = Cell(platform, video_id, status="already",
                    hint="이미 기록됨 — 재업로드 전 fresh verification 필수. 강제하려면 --force.")
        report = {"run_id": run_id, "run_dir": str(run_dir), "cells": [asdict(cell)], "agent_briefs": [], "exit_code": 0}
        _write_report(run_dir, report)
        return report

    prepared = prepare_video(cfg, video_id)
    upload_path, codec_note = ensure_h264(Path(prepared["file_path"]))
    prepared["upload_file_path"] = str(upload_path)
    prepared["codec_note"] = codec_note
    allowed = prepared.get("allowed_platforms")
    if allowed is not None and platform not in allowed:
        policy_names = ", ".join(prepared.get("policy_names") or [])
        cell = Cell(
            platform,
            video_id,
            title=prepared["title"],
            status="skipped-policy",
            hint=f"Excluded by content policy: {policy_names or 'unnamed policy'}",
        )
        report = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "cells": [asdict(cell)],
            "agent_briefs": [],
            "exit_code": 0,
        }
        _write_report(run_dir, report)
        return report
    cell = Cell(platform, video_id, title=prepared["title"],
                file_path=str(upload_path), post_text=prepared["post_text"],
                disclosures=list(prepared.get("disclosures", {}).get(platform, ())))
    if get_uploader(platform) is None:
        cell.status = "agent-required"
        cell.hint = "레지스트리에 없는 플랫폼 — references/platform-guides.md 가이드로 에이전트가 직접 업로드"
    else:
        run_lane(cfg, platform, [cell], {video_id: prepared}, run_dir, do_verify=do_verify, headless=headless)
    report = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "cells": [asdict(cell)],
        "agent_briefs": [cell.agent_brief()] if cell.needs_agent() else [],
        "exit_code": 0 if not cell.needs_agent() else 3,
    }
    _write_report(run_dir, report)
    return report


def verify_uploads(
    cfg: Config,
    *,
    platform: str | None = None,
    video_ids: list[str] | None = None,
    pending: bool = False,
    headless: bool | None = None,
) -> list[dict]:
    """게시 후 검증. --pending 은 미검증 script 셀 스윕, 명시 ID 는 해당 셀만."""
    _, run_dir = _new_run_dir(cfg)
    if pending or not video_ids:
        platform_filter = [normalize_platform_id(platform)] if platform else None
        return sweep_pending(cfg, run_dir, platform_filter=platform_filter, headless=headless)

    if not platform:
        raise ValueError("--video-id 검증에는 --platform 이 필요합니다.")
    platform = normalize_platform_id(platform)
    module = get_uploader(platform)
    if module is None:
        raise ValueError(f"코드 검증을 지원하지 않는 플랫폼: {platform}")

    results: list[dict] = []
    with platform_context(cfg, platform, headless=headless) as ctx:
        for video_id in video_ids:
            row = state.get_upload(video_id, platform) or {}
            caption = row.get("caption") or ""
            if not caption:
                prepared = prepare_video(cfg, video_id)
                caption = prepared["post_text"]
            job = Job(
                platform=platform,
                youtube_id=video_id,
                file_path=Path(),
                post_text=caption,
                title_text=caption.splitlines()[0] if caption else "",
            )
            verdict = _verify_cell(ctx, module, job, cfg, run_dir)
            if verdict.status == "verified":
                state.mark_verified(video_id, platform, platform_url=verdict.url)
            results.append({
                "youtube_id": video_id,
                "platform": platform,
                "verify_status": verdict.status,
                "evidence": verdict.evidence,
                "post_url": verdict.url or row.get("platform_url"),
                "artifacts": verdict.artifacts,
            })
    return results


def doctor(cfg: Config, *, headless: bool = True) -> dict:
    """업로드 전 환경/세션 점검(게시 없음). 로그인 만료를 토큰 없이 잡아내는 지점."""
    tools = {name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe", "yt-dlp")}
    env_checks = {"YOUTUBE_HANDLE": bool(cfg.youtube_handle)}
    required_env = {
        "instagram": [("INSTAGRAM_HANDLE", cfg.instagram_handle)],
        "linkedin": [("LINKEDIN_RECENT_ACTIVITY_URL", cfg.linkedin_recent_activity_url)],
        "facebook": [("FACEBOOK_VIDEOS_URL", cfg.facebook_videos_url)],
        "naver": [
            ("NAVER_CHANNEL_SLUG 또는 NAVER_CLIP_URL", cfg.naver_channel_slug or cfg.naver_clip_url),
            ("NAVER_CATEGORY_1", cfg.naver_category_1),
        ],
    }

    # 세션 점검: shared 전략이면 프로필 하나만 띄워 전 플랫폼 쿠키를 한 번에 읽는다.
    session_by_platform: dict[str, tuple[str, str]] = {}  # platform -> (state, hint)

    def _session_from_cookies(cookies: list[dict], platform: str) -> tuple[str, str]:
        present = login_cookie_present(cookies, platform)
        if present is True:
            return "ok", ""
        if present is None:
            return "unknown", ""
        return "no-login", f"uv run shorts-dist login {platform}"

    if cfg.profile_strategy == "shared":
        if not (cfg.shared_profile_dir / "Local State").exists():
            for platform in cfg.target_platforms:
                session_by_platform[platform] = (
                    "profile-missing",
                    f"uv run shorts-dist login {platform} (공유 프로필 생성)",
                )
        else:
            try:
                with platform_context(cfg, cfg.target_platforms[0], headless=headless) as ctx:
                    cookies = ctx.cookies()
                for platform in cfg.target_platforms:
                    session_by_platform[platform] = _session_from_cookies(cookies, platform)
            except StepFailure as failure:
                for platform in cfg.target_platforms:
                    session_by_platform[platform] = ("error", f"{failure.message} — {failure.hint}")
    else:
        for platform in cfg.target_platforms:
            profile_dir = cfg.profiles_dir / platform
            if not (profile_dir / "Local State").exists():
                session_by_platform[platform] = ("profile-missing", f"uv run shorts-dist login {platform}")
                continue
            try:
                with platform_context(cfg, platform, headless=headless) as ctx:
                    cookies = ctx.cookies()
                session_by_platform[platform] = _session_from_cookies(cookies, platform)
            except StepFailure as failure:
                session_by_platform[platform] = ("error", f"{failure.message} — {failure.hint}")

    platforms: list[dict] = []
    for platform in cfg.target_platforms:
        session, hint = session_by_platform[platform]
        row: dict = {
            "platform": platform,
            "code_upload": get_uploader(platform) is not None,
            "missing_env": [name for name, value in required_env.get(platform, []) if not value],
            "session": session,
        }
        if hint:
            row["hint"] = hint
        platforms.append(row)

    ok = (
        all(tools.values())
        and all(env_checks.values())
        and all(p["session"] == "ok" and not p["missing_env"] for p in platforms if p["code_upload"])
    )
    return {"ok": ok, "tools": tools, "env": env_checks, "platforms": platforms}


def login(cfg: Config, platform: str) -> bool:
    """플랫폼 프로필 생성/로그인(헤디드). 유일하게 사람 상호작용이 필요한 명령."""
    platform = normalize_platform_id(platform)
    module = get_uploader(platform)
    login_url = getattr(module, "LOGIN_URL", None) or f"https://www.{platform}.com/"
    profile_label = "공유 프로필" if cfg.profile_strategy == "shared" else f"{platform} 프로필"
    print(f"[login] {profile_label}로 Chrome 을 엽니다: {profile_dir_for(cfg, platform)}")
    print("[login] 창에서 로그인(2FA 포함)을 완료한 뒤, 이 터미널에서 Enter 를 누르세요.")
    with platform_context(cfg, platform, headless=False, require_profile=False) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(login_url, wait_until="domcontentloaded")
        input("로그인 완료 후 Enter > ")
        cookie = has_login_cookie(ctx, platform)
    if cookie:
        print(f"[login] {platform} 세션 쿠키 확인 — 완료.")
        return True
    if cookie is None:
        print(f"[login] {platform} 는 세션 판별 규칙이 없어 쿠키 확인을 건너뜁니다.")
        return True
    print(f"[login] {platform} 세션 쿠키가 보이지 않습니다. 로그인 상태를 다시 확인하세요.")
    return False
