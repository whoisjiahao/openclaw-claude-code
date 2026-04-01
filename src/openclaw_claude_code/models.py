from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

from openclaw_claude_code.errors import BridgeError

Status = Literal["accepted", "running", "completed", "failed", "cancelled", "acknowledged"]
TerminalStatus = Literal["completed", "failed", "cancelled"]

STATUSES = {
    "accepted",
    "running",
    "completed",
    "failed",
    "cancelled",
    "acknowledged",
}
ACTIVE_STATUSES = {"accepted", "running"}
FINAL_RESULT_STATUSES = {"completed", "failed", "cancelled", "acknowledged"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
PERMISSION_MODES = {"acceptEdits", "bypassPermissions", "default", "dontAsk", "plan", "auto"}
DEFAULT_PERMISSION_MODE = "bypassPermissions"


@dataclass(slots=True)
class Config:
    onboarding_completed: bool = False
    default_agent_teams_enabled: bool = False
    default_log_tail_lines: int = 4
    max_concurrent_jobs: int = 2
    default_cwd: str = ""
    default_notify_channel: str | None = None
    default_notify_target: str | None = None
    default_permission_mode: str = DEFAULT_PERMISSION_MODE

    @classmethod
    def _default_cwd(cls) -> str:
        return str(Path.home())

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "Config":
        if payload is None:
            return cls(default_cwd=cls._default_cwd())
        try:
            onboarding_completed = _require_bool(payload, "onboarding_completed")
            default_agent_teams_enabled = _require_bool(payload, "default_agent_teams_enabled")
            default_log_tail_lines = _require_positive_int(payload, "default_log_tail_lines")
            max_concurrent_jobs = _require_positive_int(payload, "max_concurrent_jobs")
        except KeyError as exc:
            raise BridgeError("invalid_config", f"配置缺少字段：{exc.args[0]}。") from exc
        return cls(
            onboarding_completed=onboarding_completed,
            default_agent_teams_enabled=default_agent_teams_enabled,
            default_log_tail_lines=default_log_tail_lines,
            max_concurrent_jobs=max_concurrent_jobs,
            default_cwd=payload.get("default_cwd") or cls._default_cwd(),
            default_notify_channel=payload.get("default_notify_channel"),
            default_notify_target=payload.get("default_notify_target"),
            default_permission_mode=payload.get("default_permission_mode", DEFAULT_PERMISSION_MODE),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class JobRecord:
    job_id: str
    task_name: str
    prompt: str
    cwd: str
    status: Status
    agent_teams_enabled: bool
    teammate_mode: str | None
    artifacts_required: bool
    permission_mode: str
    process_pid: int | None
    tmux_socket_path: str | None
    tmux_session_name: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    acknowledged_at: str | None
    updated_at: str
    notify_channel: str | None = None
    notify_target: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "JobRecord":
        status = _require_status(payload, "status")
        return cls(
            job_id=_require_str(payload, "job_id"),
            task_name=_require_str(payload, "task_name"),
            prompt=_require_str(payload, "prompt"),
            cwd=_require_str(payload, "cwd"),
            status=status,
            agent_teams_enabled=_require_bool(payload, "agent_teams_enabled"),
            teammate_mode=_optional_str(payload, "teammate_mode"),
            artifacts_required=_require_bool(payload, "artifacts_required"),
            permission_mode=payload.get("permission_mode", DEFAULT_PERMISSION_MODE),
            process_pid=_optional_int(payload, "process_pid"),
            tmux_socket_path=_optional_str(payload, "tmux_socket_path"),
            tmux_session_name=_optional_str(payload, "tmux_session_name"),
            created_at=_require_str(payload, "created_at"),
            started_at=_optional_str(payload, "started_at"),
            completed_at=_optional_str(payload, "completed_at"),
            acknowledged_at=_optional_str(payload, "acknowledged_at"),
            updated_at=_require_str(payload, "updated_at"),
            notify_channel=payload.get("notify_channel"),
            notify_target=payload.get("notify_target"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ResultRecord:
    job_id: str
    task_name: str
    outcome: TerminalStatus
    message: str
    exit_code: int | None
    completed_at: str
    artifacts: list[str]
    cost_usd: float | None = None
    duration_seconds: int | None = None
    num_turns: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None
    permission_denials: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload[field]
    if type(value) is not bool:
        raise BridgeError("invalid_config", f"字段 `{field}` 必须是布尔值。")
    return cast(bool, value)


def _require_positive_int(payload: dict[str, Any], field: str) -> int:
    value = payload[field]
    if type(value) is not int or value < 1:
        raise BridgeError("invalid_config", f"字段 `{field}` 必须是大于等于 1 的整数。")
    return cast(int, value)


def _require_str(payload: dict[str, Any], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str) or value == "":
        raise BridgeError("internal_error", f"字段 `{field}` 必须是非空字符串。")
    return value


def _optional_str(payload: dict[str, Any], field: str) -> str | None:
    value = payload[field]
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise BridgeError("internal_error", f"字段 `{field}` 必须是字符串或 null。")
    return value


def _optional_int(payload: dict[str, Any], field: str) -> int | None:
    value = payload[field]
    if value is None:
        return None
    if type(value) is not int:
        raise BridgeError("internal_error", f"字段 `{field}` 必须是整数或 null。")
    return cast(int, value)


def _require_status(payload: dict[str, Any], field: str) -> Status:
    value = _require_str(payload, field)
    if value not in STATUSES:
        raise BridgeError("internal_error", f"字段 `{field}` 不是合法状态。")
    return cast(Status, value)

