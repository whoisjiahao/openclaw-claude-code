from __future__ import annotations

import json
import logging
import os
import secrets
import shlex
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

from openclaw_claude_code.errors import BridgeError
from openclaw_claude_code.models import (
    ACTIVE_STATUSES,
    FINAL_RESULT_STATUSES,
    STATUSES,
    TERMINAL_STATUSES,
    Config,
    JobRecord,
    ResultRecord,
    Status,
)
from openclaw_claude_code.runner import (
    OPENCLAW_CLAUDE_CODE_JOB_ID,
    cancel_headless,
    cancel_tmux,
    extract_stream_metadata,
    launch_headless_runner,
    launch_tmux_runner,
    run_job,
    summarize_failure,
    summarize_success,
)
from openclaw_claude_code.timeutils import validate_timezone_name
from openclaw_claude_code.runtime import (
    JobPaths,
    RuntimeSettings,
    active_jobs,
    append_jsonl,
    ensure_job_dir,
    ensure_runtime_root,
    exclusive_lock,
    last_output_at,
    load_config,
    load_job,
    make_hook_marker_name,
    parse_bool_text,
    process_exists,
    read_exit_code,
    require_absolute_cwd,
    scan_artifacts,
    now_in_timezone,
    utc_now_ms,
    write_config,
    write_job,
    write_text,
)

ALLOWED_TRANSITIONS: dict[Status, set[Status]] = {
    "accepted": {"running", "failed", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "completed": {"acknowledged"},
    "failed": {"acknowledged"},
    "cancelled": {"acknowledged"},
    "acknowledged": set(),
}

DEFAULT_RECONCILE_GRACE_SECONDS = 45.0
DEFAULT_RECONCILE_POLL_SECONDS = 0.5


def list_jobs(settings: RuntimeSettings, *, filter_status: str | None) -> dict[str, Any]:
    if filter_status is not None and filter_status not in STATUSES:
        raise BridgeError("invalid_arguments", f"不合法的状态过滤值：`{filter_status}`。")
    from openclaw_claude_code.runtime import list_job_ids

    results: list[dict[str, Any]] = []
    for jid in list_job_ids(settings.paths):
        job_paths = settings.paths.job_paths(jid)
        try:
            job = load_job(job_paths)
        except BridgeError:
            continue
        if filter_status is not None and job.status != filter_status:
            continue
        results.append({
            "job_id": job.job_id,
            "task_name": job.task_name,
            "status": job.status,
            "created_at": job.created_at,
        })
    results.sort(key=lambda j: j["created_at"], reverse=True)
    return {"jobs": results}


def preflight(settings: RuntimeSettings) -> dict[str, Any]:
    import shutil
    import sys
    from pathlib import Path

    def _check_bin(bin_value: str | None) -> dict[str, Any]:
        if not bin_value:
            return {"status": "not_found", "path": None}
        resolved = shutil.which(bin_value)
        if resolved:
            return {"status": "found", "path": resolved}
        return {"status": "not_found", "path": None}

    uv_check = _check_bin("uv")

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 11)
    python_version_check = {
        "status": "ok" if py_ok else "too_low",
        "version": py_ver,
    }

    skill_root = Path(__file__).resolve().parents[2]
    venv_path = skill_root / ".venv"
    venv_check = {
        "status": "ok" if venv_path.is_dir() else "not_found",
        "path": str(venv_path) if venv_path.is_dir() else None,
    }

    claude_check = _check_bin(settings.claude_bin)
    tmux_check = _check_bin(settings.tmux_bin)
    openclaw_check = _check_bin(settings.openclaw_bin)

    ok = (
        uv_check["status"] == "found"
        and py_ok
        and venv_check["status"] == "ok"
        and claude_check["status"] == "found"
    )

    return {
        "ok": ok,
        "runtime_root": str(settings.paths.runtime_root),
        "checks": {
            "uv": uv_check,
            "python_version": python_version_check,
            "venv_ready": venv_check,
            "claude_bin": claude_check,
            "tmux_bin": tmux_check,
            "openclaw_bin": openclaw_check,
        },
    }


def inspect_config(settings: RuntimeSettings) -> dict[str, Any]:
    config = load_config(settings.paths)
    return {
        "onboarding_required": not config.onboarding_completed,
        **config.to_dict(),
    }


def set_config(
    settings: RuntimeSettings,
    *,
    default_agent_teams_enabled: str,
    default_log_tail_lines: int,
    max_concurrent_jobs: int,
    default_cwd: str | None = None,
    timezone: str | None = None,
    default_notify_channel: str | None = None,
    default_notify_target: str | None = None,
    default_permission_mode: str | None = None,
) -> dict[str, Any]:
    from openclaw_claude_code.models import DEFAULT_PERMISSION_MODE, PERMISSION_MODES

    if default_log_tail_lines < 1 or max_concurrent_jobs < 1:
        raise BridgeError("invalid_config", "日志行数和最大并发数都必须大于等于 1。")
    existing = load_config(settings.paths)
    effective_pm = default_permission_mode or DEFAULT_PERMISSION_MODE
    effective_timezone = validate_timezone_name(timezone or existing.timezone)
    if effective_pm not in PERMISSION_MODES:
        raise BridgeError("invalid_config", f"无效的 permission mode：{effective_pm}。")
    if default_cwd:
        from pathlib import Path

        if not Path(default_cwd).is_absolute():
            raise BridgeError("invalid_config", "`default-cwd` 必须是绝对路径。")
    config = Config(
        onboarding_completed=True,
        default_agent_teams_enabled=parse_bool_text(default_agent_teams_enabled),
        default_log_tail_lines=default_log_tail_lines,
        max_concurrent_jobs=max_concurrent_jobs,
        default_cwd=default_cwd or None,
        timezone=effective_timezone,
        default_notify_channel=default_notify_channel or None,
        default_notify_target=default_notify_target or None,
        default_permission_mode=effective_pm,
    )
    write_config(settings.paths, config)
    return config.to_dict()


def submit(
    settings: RuntimeSettings,
    *,
    prompt: str,
    cwd: str,
    task_name: str | None,
    run_mode: str,
    agent_teams: bool | None,
    teammate_mode: str | None,
    artifacts_required: bool,
    permission_mode: str | None = None,
    notify_channel: str | None = None,
    notify_target: str | None = None,
) -> dict[str, Any]:
    from openclaw_claude_code.models import PERMISSION_MODES

    if not prompt.strip():
        raise BridgeError("invalid_arguments", "`prompt` 不能为空。")
    require_absolute_cwd(cwd)
    if run_mode not in {"headless", "tmux"}:
        raise BridgeError("invalid_arguments", "`run-mode` 只能是 `headless` 或 `tmux`。")

    ensure_runtime_root(settings.paths)
    config = load_config(settings.paths)
    agent_teams_enabled = config.default_agent_teams_enabled if agent_teams is None else agent_teams
    if teammate_mode and not agent_teams_enabled:
        raise BridgeError("invalid_arguments", "未启用 Agent Teams 时不能指定 `teammate-mode`。")
    if agent_teams_enabled and teammate_mode is None:
        teammate_mode = "auto"
    if not agent_teams_enabled:
        teammate_mode = None

    effective_permission_mode = permission_mode or config.default_permission_mode
    if effective_permission_mode not in PERMISSION_MODES:
        raise BridgeError("invalid_arguments", f"无效的 permission mode：{effective_permission_mode}。")

    effective_notify_channel = notify_channel or config.default_notify_channel
    effective_notify_target = notify_target or config.default_notify_target

    with exclusive_lock(settings.paths.submit_lock_path):
        active = active_jobs(settings.paths)
        if len(active) >= config.max_concurrent_jobs:
            raise BridgeError(
                "max_concurrent_jobs_reached",
                f"当前活跃任务数已达到上限 {config.max_concurrent_jobs}，请稍后再试。",
            )

        job_id = f"job_{utc_now_ms()}_{secrets.token_hex(3)}"
        created_at = now_in_timezone(config.timezone)
        effective_task_name = task_name or job_id
        job = JobRecord(
            job_id=job_id,
            task_name=effective_task_name,
            prompt=prompt,
            cwd=cwd,
            timezone=config.timezone,
            status="accepted",
            agent_teams_enabled=agent_teams_enabled,
            teammate_mode=teammate_mode,
            artifacts_required=artifacts_required,
            permission_mode=effective_permission_mode,
            process_pid=None,
            tmux_socket_path=None,
            tmux_session_name=None,
            created_at=created_at,
            started_at=None,
            completed_at=None,
            acknowledged_at=None,
            updated_at=created_at,
            notify_channel=effective_notify_channel,
            notify_target=effective_notify_target,
        )
        job_paths = settings.paths.job_paths(job_id)
        ensure_job_dir(job_paths)
        if artifacts_required:
            job_paths.artifacts_dir.mkdir(parents=True, exist_ok=True)

        with exclusive_lock(job_paths.state_lock_path):
            write_job(job_paths, job)
            append_event(job_paths, status="accepted", by="submit", ts=created_at)

        try:
            if run_mode == "headless":
                process_pid = launch_headless_runner(settings, job_id)
                tmux_socket_path = None
                tmux_session_name = None
            else:
                process_pid = None
                tmux_socket_path, tmux_session_name = launch_tmux_runner(settings, job_id, cwd)
        except BridgeError as exc:
            with exclusive_lock(job_paths.state_lock_path):
                current_job = load_job(job_paths)
                completed_at = now_in_timezone(current_job.timezone)
                failed_job = transition_job(
                    current_job,
                    to_status="failed",
                    updated_at=completed_at,
                    completed_at=completed_at,
                )
                write_job(job_paths, failed_job)
                append_event(job_paths, status="failed", by="submit", ts=completed_at)
                write_result(
                    job_paths,
                    ResultRecord(
                        job_id=job_id,
                        task_name=effective_task_name,
                        outcome="failed",
                        message=exc.message,
                        exit_code=None,
                        completed_at=completed_at,
                        artifacts=[],
                    ),
                )
            raise

        started_at = now_in_timezone(config.timezone)
        with exclusive_lock(job_paths.state_lock_path):
            current_job = load_job(job_paths)
            running_job = transition_job(
                current_job,
                to_status="running",
                updated_at=started_at,
                started_at=started_at,
                process_pid=process_pid,
                tmux_socket_path=tmux_socket_path,
                tmux_session_name=tmux_session_name,
            )
            write_job(job_paths, running_job)
            append_event(job_paths, status="running", by="submit", ts=started_at)

        return {
            "job_id": job_id,
            "status": "running",
            "task_name": effective_task_name,
            "cwd": cwd,
            "agent_teams_enabled": agent_teams_enabled,
            "teammate_mode": teammate_mode,
            "artifacts_required": artifacts_required,
            "created_at": created_at,
            "started_at": started_at,
        }


def status(settings: RuntimeSettings, *, job_id: str) -> dict[str, Any]:
    job_paths = settings.paths.job_paths(job_id)
    job = load_job(job_paths)
    return {
        "job_id": job.job_id,
        "status": job.status,
        "started_at": job.started_at,
        "updated_at": job.updated_at,
        "last_output_at": last_output_at(job_paths, job.timezone),
    }


def logs(settings: RuntimeSettings, *, job_id: str, lines: int | None) -> dict[str, Any]:
    job_paths = settings.paths.job_paths(job_id)
    load_job(job_paths)
    config = load_config(settings.paths)
    actual_lines = lines or config.default_log_tail_lines
    if actual_lines < 1:
        raise BridgeError("invalid_arguments", "`lines` 必须大于等于 1。")
    from openclaw_claude_code.runner import humanize_stream_events
    from openclaw_claude_code.runtime import tail_lines

    raw_stdout = tail_lines(job_paths.stdout_path, actual_lines)
    activities = humanize_stream_events(raw_stdout)

    return {
        "job_id": job_id,
        "lines": actual_lines,
        "activities": activities,
        "stderr": tail_lines(job_paths.stderr_path, actual_lines),
    }


def result(settings: RuntimeSettings, *, job_id: str) -> dict[str, Any]:
    job_paths = settings.paths.job_paths(job_id)
    job = load_job(job_paths)
    if job.status not in FINAL_RESULT_STATUSES:
        raise BridgeError("result_not_ready", "任务尚未产生最终结果。")
    try:
        from openclaw_claude_code.runtime import load_json

        return load_json(job_paths.result_path)
    except FileNotFoundError as exc:
        raise BridgeError("internal_error", "任务已进入终态，但缺少 result.json。") from exc


def cancel(settings: RuntimeSettings, *, job_id: str) -> dict[str, Any]:
    job_paths = settings.paths.job_paths(job_id)
    with exclusive_lock(job_paths.state_lock_path):
        job = load_job(job_paths)
        if job.status not in ACTIVE_STATUSES:
            raise BridgeError("invalid_state_transition", f"当前状态 `{job.status}` 不允许取消。")
        if job.process_pid is not None:
            cancel_headless(job.process_pid)
        elif job.tmux_socket_path and job.tmux_session_name:
            cancel_tmux(settings, job.tmux_socket_path, job.tmux_session_name)
        else:
            raise BridgeError("cancel_handle_missing", "当前任务缺少可取消的运行句柄。")

        completed_at = now_in_timezone(job.timezone)
        cancelled_job = transition_job(
            job,
            to_status="cancelled",
            updated_at=completed_at,
            completed_at=completed_at,
        )
        write_job(job_paths, cancelled_job)
        append_event(job_paths, status="cancelled", by="cancel", ts=completed_at)
        write_result(
            job_paths,
            ResultRecord(
                job_id=job.job_id,
                task_name=job.task_name,
                outcome="cancelled",
                message="任务已取消。",
                exit_code=None,
                completed_at=completed_at,
                artifacts=scan_artifacts(job_paths),
            ),
        )
    return {
        "job_id": job_id,
        "status": "cancelled",
        "completed_at": completed_at,
    }


def acknowledge(settings: RuntimeSettings, *, job_id: str) -> dict[str, Any]:
    job_paths = settings.paths.job_paths(job_id)
    with exclusive_lock(job_paths.state_lock_path):
        job = load_job(job_paths)
        if job.status not in TERMINAL_STATUSES:
            raise BridgeError("invalid_state_transition", f"当前状态 `{job.status}` 不允许 acknowledge。")
        acknowledged_at = now_in_timezone(job.timezone)
        acknowledged_job = transition_job(
            job,
            to_status="acknowledged",
            updated_at=acknowledged_at,
            acknowledged_at=acknowledged_at,
        )
        write_job(job_paths, acknowledged_job)
        append_event(job_paths, status="acknowledged", by="acknowledge", ts=acknowledged_at)
    return {
        "job_id": job_id,
        "status": "acknowledged",
        "acknowledged_at": acknowledged_at,
    }


def _is_numeric_target(target: str) -> bool:
    """Return True if target looks like a numeric chat ID or E.164 phone number."""
    stripped = target.lstrip("+-")
    return stripped.isdigit() and len(stripped) > 0


def _notify_target_flags(channel: str, target: str) -> list[str]:
    """Pick the right ``openclaw agent`` target flag based on format.

    Always uses ``--channel`` so the agent runs within the target
    session context (affects session routing). For the target: numeric
    values (chat IDs like ``-1001234567890``, phone numbers like
    ``+15555550123``) use ``--to`` which accepts IDs; human-readable
    targets (``@user``, ``#channel``) use ``--reply-to``.
    """
    if _is_numeric_target(target):
        return ["--channel", channel, "--to", target]
    return ["--channel", channel, "--reply-to", target]


def _try_send_notification(
    settings: RuntimeSettings,
    job: JobRecord,
    result_record: ResultRecord,
) -> None:
    """Best-effort notification via openclaw agent CLI. Never raises.

    Uses ``openclaw agent --message ... --deliver`` so the agent processes
    the instruction and only the formatted reply reaches the user.
    ``openclaw message send`` would post the raw instruction text directly.
    """
    if not job.notify_channel or not job.notify_target:
        return
    if not settings.openclaw_bin:
        return

    parts = [
        f"[openclaw-claude-code] task {result_record.outcome}",
        f"job_id: {job.job_id}",
        f"task_name: {job.task_name}",
        "",
        f"Please run `uv run python scripts/bridge.py result --job-id {job.job_id}` to fetch the full result, "
        "then present it to the user following the template in references/ux-feedback.md.",
    ]
    message = "\n".join(parts)

    cmd = [
        settings.openclaw_bin,
        "agent",
        "--message",
        message,
        "--deliver",
        *_notify_target_flags(job.notify_channel, job.notify_target),
    ]

    try:
        proc = subprocess.run(cmd, timeout=120, capture_output=True)
        if proc.returncode != 0:
            logger.error(
                "notification exited %d for job %s\nstdout: %s\nstderr: %s",
                proc.returncode,
                job.job_id,
                proc.stdout.decode(errors="replace")[:2000],
                proc.stderr.decode(errors="replace")[:2000],
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("notification failed for job %s: %s", job.job_id, exc)


def _build_result_record(
    job_paths: JobPaths,
    job: JobRecord,
    *,
    outcome: Status,
    message: str,
    exit_code: int | None,
    completed_at: str,
) -> ResultRecord:
    meta = extract_stream_metadata(job_paths)
    return ResultRecord(
        job_id=job.job_id,
        task_name=job.task_name,
        outcome="completed" if outcome == "completed" else "failed",
        message=message,
        exit_code=exit_code,
        completed_at=completed_at,
        artifacts=scan_artifacts(job_paths),
        cost_usd=meta["cost_usd"],
        duration_seconds=meta["duration_seconds"],
        num_turns=meta["num_turns"],
        input_tokens=meta["input_tokens"],
        output_tokens=meta["output_tokens"],
        model=meta["model"],
        permission_denials=meta["permission_denials"],
    )


def _persist_terminal_result(
    job_paths: JobPaths,
    job: JobRecord,
    *,
    outcome: Status,
    message: str,
    exit_code: int | None,
    completed_at: str,
    by: str,
) -> tuple[JobRecord, ResultRecord]:
    finalized_job = transition_job(
        job,
        to_status=outcome,
        updated_at=completed_at,
        completed_at=completed_at,
    )
    write_job(job_paths, finalized_job)
    append_event(job_paths, status=outcome, by=by, ts=completed_at)
    result_record = _build_result_record(
        job_paths,
        finalized_job,
        outcome=outcome,
        message=message,
        exit_code=exit_code,
        completed_at=completed_at,
    )
    write_result(job_paths, result_record)
    return finalized_job, result_record


def _reconcile_grace_seconds() -> float:
    raw = os.environ.get("OPENCLAW_CLAUDE_CODE_RECONCILE_GRACE_SECONDS")
    if raw is None:
        return DEFAULT_RECONCILE_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RECONCILE_GRACE_SECONDS
    return max(0.0, value)


def _reconcile_poll_seconds() -> float:
    raw = os.environ.get("OPENCLAW_CLAUDE_CODE_RECONCILE_POLL_SECONDS")
    if raw is None:
        return DEFAULT_RECONCILE_POLL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RECONCILE_POLL_SECONDS
    return max(0.05, value)


def finalize_job(settings: RuntimeSettings, *, job_id: str, by: str) -> dict[str, Any]:
    """Finalize a job from runner-owned facts on disk."""
    job_paths = settings.paths.job_paths(job_id)

    with exclusive_lock(job_paths.state_lock_path):
        job = load_job(job_paths)
        if job.status in {"completed", "failed", "cancelled", "acknowledged"}:
            return {
                "job_id": job.job_id,
                "status": job.status,
                "completed_at": job.completed_at,
            }

        exit_code = read_exit_code(job_paths)
        completed_at = now_in_timezone(job.timezone)
        if exit_code is None:
            outcome: Status = "failed"
            message = "任务收尾失败：runner 未写入 exit-code.txt。"
        elif exit_code == 0:
            outcome = "completed"
            message = summarize_success(job_paths)
        else:
            outcome = "failed"
            message = summarize_failure(job_paths)

        finalized_job, result_record = _persist_terminal_result(
            job_paths,
            job,
            outcome=outcome,
            message=message,
            exit_code=exit_code,
            completed_at=completed_at,
            by=by,
        )

    _try_send_notification(settings, finalized_job, result_record)

    return {
        "job_id": job.job_id,
        "status": outcome,
        "completed_at": completed_at,
    }


def reconcile_job(settings: RuntimeSettings, *, job_id: str, by: str) -> dict[str, Any]:
    """Reconcile a running job without competing with runner-owned finalize."""
    job_paths = settings.paths.job_paths(job_id)
    deadline = time.monotonic() + _reconcile_grace_seconds()
    poll_seconds = _reconcile_poll_seconds()

    while True:
        should_sleep = False
        with exclusive_lock(job_paths.state_lock_path):
            job = load_job(job_paths)
            if job.status in {"completed", "failed", "cancelled", "acknowledged"}:
                return {
                    "job_id": job.job_id,
                    "status": job.status,
                    "completed_at": job.completed_at,
                }

            exit_code = read_exit_code(job_paths)
            if exit_code is not None:
                completed_at = now_in_timezone(job.timezone)
                if exit_code == 0:
                    outcome: Status = "completed"
                    message = summarize_success(job_paths)
                else:
                    outcome = "failed"
                    message = summarize_failure(job_paths)
                finalized_job, result_record = _persist_terminal_result(
                    job_paths,
                    job,
                    outcome=outcome,
                    message=message,
                    exit_code=exit_code,
                    completed_at=completed_at,
                    by=by,
                )
                break

            if not process_exists(job.process_pid):
                completed_at = now_in_timezone(job.timezone)
                finalized_job, result_record = _persist_terminal_result(
                    job_paths,
                    job,
                    outcome="failed",
                    message="任务收尾失败：runner 已退出，但未写入 exit-code.txt。",
                    exit_code=None,
                    completed_at=completed_at,
                    by=by,
                )
                break

            if time.monotonic() >= deadline:
                completed_at = now_in_timezone(job.timezone)
                finalized_job, result_record = _persist_terminal_result(
                    job_paths,
                    job,
                    outcome="failed",
                    message="任务收尾失败：runner 未在宽限期内写入 exit-code.txt。",
                    exit_code=None,
                    completed_at=completed_at,
                    by=by,
                )
                break

            should_sleep = True

        if not should_sleep:
            continue
        time.sleep(poll_seconds)

    _try_send_notification(settings, finalized_job, result_record)

    return {
        "job_id": finalized_job.job_id,
        "status": finalized_job.status,
        "completed_at": finalized_job.completed_at,
    }


def hook_finalize(settings: RuntimeSettings, *, stdin_text: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(stdin_text or "")
    except json.JSONDecodeError as exc:
        raise BridgeError("invalid_arguments", "hook stdin 必须是合法 JSON。") from exc

    session_id = _require_hook_field(payload, "session_id")
    hook_event_name = _require_hook_field(payload, "hook_event_name")
    _require_hook_field(payload, "cwd")

    job_id = os.environ.get(OPENCLAW_CLAUDE_CODE_JOB_ID)
    if not job_id:
        raise BridgeError("hook_job_id_missing", "hook 环境变量 OPENCLAW_CLAUDE_CODE_JOB_ID 缺失。")

    job_paths = settings.paths.job_paths(job_id)
    marker_path = job_paths.hook_finalize_dir / make_hook_marker_name(session_id, hook_event_name)

    if marker_path.exists():
        with exclusive_lock(job_paths.state_lock_path):
            job = load_job(job_paths)
            return {
                "job_id": job.job_id,
                "status": job.status,
                "completed_at": job.completed_at,
            }

    result_payload = reconcile_job(settings, job_id=job_id, by="hook_finalize")
    write_text(marker_path, f"{session_id}:{hook_event_name}\n")
    return result_payload


def run_runner(settings: RuntimeSettings, *, job_id: str, mode: str) -> dict[str, Any]:
    job = load_job(settings.paths.job_paths(job_id))
    return run_job(settings, job, mode)


def transition_job(
    job: JobRecord,
    *,
    to_status: Status,
    updated_at: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    acknowledged_at: str | None = None,
    process_pid: int | None = None,
    tmux_socket_path: str | None = None,
    tmux_session_name: str | None = None,
) -> JobRecord:
    allowed = ALLOWED_TRANSITIONS[job.status]
    if to_status not in allowed:
        raise BridgeError("invalid_state_transition", f"不允许从 `{job.status}` 迁移到 `{to_status}`。")
    next_job = JobRecord.from_dict(job.to_dict())
    next_job.status = to_status
    next_job.updated_at = updated_at
    if started_at is not None:
        next_job.started_at = started_at
    if completed_at is not None:
        next_job.completed_at = completed_at
    if acknowledged_at is not None:
        next_job.acknowledged_at = acknowledged_at
    if process_pid is not None or to_status == "running":
        next_job.process_pid = process_pid
    if to_status == "running":
        next_job.tmux_socket_path = tmux_socket_path
        next_job.tmux_session_name = tmux_session_name
    return next_job


def append_event(job_paths: JobPaths, *, status: Status, by: str, ts: str) -> None:
    append_jsonl(
        job_paths.events_path,
        {
            "ts": ts,
            "status": status,
            "by": by,
        },
    )


def write_result(job_paths: JobPaths, result: ResultRecord) -> None:
    from openclaw_claude_code.runtime import write_json

    write_json(job_paths.result_path, result.to_dict())


def _require_hook_field(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise BridgeError("invalid_arguments", f"hook 字段 `{field}` 缺失或类型错误。")
    return value
