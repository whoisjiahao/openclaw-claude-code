from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from openclaw_claude_code.errors import BridgeError
from openclaw_claude_code.models import Config, JobRecord
from openclaw_claude_code.timeutils import current_time_iso, timestamp_to_iso

try:
    import fcntl
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("This project requires fcntl support.") from exc


DEFAULT_RUNTIME_ENV = "OPENCLAW_CLAUDE_CODE_RUNTIME_ROOT"
DEFAULT_OPENCLAW_HOME = "OPENCLAW_HOME"
DEFAULT_CLAUDE_BIN_ENV = "OPENCLAW_CLAUDE_CODE_CLAUDE_BIN"
TMUX_BIN_ENV = "OPENCLAW_CLAUDE_CODE_TMUX_BIN"
CLAUDE_EXTRA_ARGS_ENV = "OPENCLAW_CLAUDE_CODE_CLAUDE_EXTRA_ARGS"
PYTHON_BIN_ENV = "OPENCLAW_CLAUDE_CODE_PYTHON_BIN"
OPENCLAW_BIN_ENV = "OPENCLAW_CLAUDE_CODE_OPENCLAW_BIN"


@dataclass(slots=True)
class RuntimePaths:
    runtime_root: Path

    @property
    def jobs_root(self) -> Path:
        return self.runtime_root / "jobs"

    @property
    def config_path(self) -> Path:
        return self.runtime_root / "config.json"

    @property
    def submit_lock_path(self) -> Path:
        return self.runtime_root / "submit.lock"

    def job_paths(self, job_id: str) -> "JobPaths":
        return JobPaths(self.jobs_root / job_id)


@dataclass(slots=True)
class JobPaths:
    job_dir: Path

    @property
    def job_json_path(self) -> Path:
        return self.job_dir / "job.json"

    @property
    def events_path(self) -> Path:
        return self.job_dir / "events.jsonl"

    @property
    def stdout_path(self) -> Path:
        return self.job_dir / "stdout.log"

    @property
    def stderr_path(self) -> Path:
        return self.job_dir / "stderr.log"

    @property
    def exit_code_path(self) -> Path:
        return self.job_dir / "exit-code.txt"

    @property
    def result_path(self) -> Path:
        return self.job_dir / "result.json"

    @property
    def state_lock_path(self) -> Path:
        return self.job_dir / "state.lock"

    @property
    def hook_finalize_dir(self) -> Path:
        return self.job_dir / "hook-finalize"

    @property
    def artifacts_dir(self) -> Path:
        return self.job_dir / "artifacts"


@dataclass(slots=True)
class RuntimeSettings:
    paths: RuntimePaths
    entry_script: Path
    python_bin: str
    claude_bin: str
    tmux_bin: str
    claude_extra_args: tuple[str, ...]
    openclaw_bin: str | None = None


def _resolve_venv_python() -> str | None:
    venv_python = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    return None


def build_runtime_settings(runtime_root_arg: str | None, env: Mapping[str, str] | None = None) -> RuntimeSettings:
    current_env = dict(os.environ if env is None else env)
    runtime_root = resolve_runtime_root(runtime_root_arg, current_env)
    return RuntimeSettings(
        paths=RuntimePaths(runtime_root=runtime_root),
        entry_script=Path(__file__).resolve().parents[2] / "scripts" / "bridge.py",
        python_bin=current_env.get(PYTHON_BIN_ENV) or _resolve_venv_python() or os.sys.executable,
        claude_bin=current_env.get(DEFAULT_CLAUDE_BIN_ENV) or "claude",
        tmux_bin=current_env.get(TMUX_BIN_ENV, "tmux"),
        claude_extra_args=tuple(shlex.split(current_env.get(CLAUDE_EXTRA_ARGS_ENV, ""))),
        openclaw_bin=current_env.get(OPENCLAW_BIN_ENV) or which("openclaw"),
    )


def resolve_runtime_root(runtime_root_arg: str | None, env: Mapping[str, str] | None = None) -> Path:
    current_env = dict(os.environ if env is None else env)
    explicit = runtime_root_arg or current_env.get(DEFAULT_RUNTIME_ENV)
    if explicit:
        return Path(explicit).expanduser()
    openclaw_home = current_env.get(DEFAULT_OPENCLAW_HOME)
    if openclaw_home:
        return Path(openclaw_home).expanduser() / "data" / "openclaw-claude-code"
    home = current_env.get("HOME")
    if home:
        return Path(home).expanduser() / ".openclaw" / "data" / "openclaw-claude-code"
    return Path.home() / ".openclaw" / "data" / "openclaw-claude-code"


def ensure_runtime_root(paths: RuntimePaths) -> None:
    paths.runtime_root.mkdir(parents=True, exist_ok=True)
    paths.jobs_root.mkdir(parents=True, exist_ok=True)


def ensure_job_dir(job_paths: JobPaths) -> None:
    job_paths.job_dir.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise BridgeError("internal_error", f"JSON 文件损坏：{path}。") from exc


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_config(paths: RuntimePaths) -> Config:
    try:
        payload = load_json(paths.config_path)
    except FileNotFoundError:
        return Config.from_dict(None)
    return Config.from_dict(payload)


def write_config(paths: RuntimePaths, config: Config) -> None:
    ensure_runtime_root(paths)
    write_json(paths.config_path, config.to_dict())


def load_job(job_paths: JobPaths) -> JobRecord:
    try:
        payload = load_json(job_paths.job_json_path)
    except FileNotFoundError as exc:
        raise BridgeError("job_not_found", "未找到对应 job。") from exc
    return JobRecord.from_dict(payload)


def write_job(job_paths: JobPaths, job: JobRecord) -> None:
    write_json(job_paths.job_json_path, job.to_dict())


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def now_in_timezone(timezone_name: str) -> str:
    return current_time_iso(timezone_name)


def utc_now_ms() -> int:
    return time.time_ns() // 1_000_000


def iso_from_timestamp(timestamp: float, timezone_name: str) -> str:
    return timestamp_to_iso(timestamp, timezone_name)


def last_output_at(job_paths: JobPaths, timezone_name: str) -> str | None:
    candidates: list[float] = []
    for path in (job_paths.stdout_path, job_paths.stderr_path):
        try:
            candidates.append(path.stat().st_mtime)
        except FileNotFoundError:
            continue
    if not candidates:
        return None
    return iso_from_timestamp(max(candidates), timezone_name)


def tail_lines(path: Path, lines: int) -> str:
    if lines < 1:
        raise BridgeError("invalid_arguments", "`lines` 必须大于等于 1。")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    content_lines = text.splitlines()
    return "\n".join(content_lines[-lines:])


def tail_chars(path: Path, chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    return text[-chars:]


def scan_artifacts(job_paths: JobPaths) -> list[str]:
    if not job_paths.artifacts_dir.exists():
        return []
    result: list[str] = []
    for file_path in sorted(p for p in job_paths.artifacts_dir.rglob("*") if p.is_file()):
        result.append(str(file_path.resolve()))
    return result


def active_jobs(paths: RuntimePaths) -> list[JobRecord]:
    jobs: list[JobRecord] = []
    if not paths.jobs_root.exists():
        return jobs
    for job_dir in sorted(p for p in paths.jobs_root.iterdir() if p.is_dir()):
        try:
            job = load_job(JobPaths(job_dir))
        except BridgeError:
            continue
        if job.status in {"accepted", "running"}:
            jobs.append(job)
    return jobs


def which(name: str) -> str | None:
    return shutil.which(name)


def require_absolute_cwd(cwd: str) -> None:
    p = Path(cwd)
    if not p.is_absolute():
        raise BridgeError("invalid_arguments", "`cwd` 必须是绝对路径。")
    if not p.is_dir():
        raise BridgeError("invalid_arguments", f"`cwd` 目录不存在：{cwd}。")


def parse_bool_text(value: str) -> bool:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise BridgeError("invalid_arguments", "布尔参数必须是 `true` 或 `false`。")


def make_hook_marker_name(session_id: str, hook_event_name: str) -> str:
    safe_session = session_id.replace("/", "_")
    safe_event = hook_event_name.replace("/", "_")
    return f"{safe_session}__{safe_event}"


def ensure_text_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def read_exit_code(job_paths: JobPaths) -> int | None:
    try:
        raw = job_paths.exit_code_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise BridgeError("internal_error", "exit-code.txt 不是合法整数。") from exc


def process_exists(process_pid: int | None) -> bool:
    if process_pid is None:
        return False
    try:
        os.kill(process_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def list_job_ids(paths: RuntimePaths) -> Iterable[str]:
    if not paths.jobs_root.exists():
        return []
    return [path.name for path in paths.jobs_root.iterdir() if path.is_dir()]
