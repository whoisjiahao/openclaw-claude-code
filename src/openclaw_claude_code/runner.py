from __future__ import annotations

import json as _json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any

from openclaw_claude_code.errors import BridgeError
from openclaw_claude_code.models import JobRecord
from openclaw_claude_code.runtime import (
    JobPaths,
    RuntimeSettings,
    ensure_text_file,
    tail_chars,
    write_text,
)

OPENCLAW_CLAUDE_CODE_JOB_ID = "OPENCLAW_CLAUDE_CODE_JOB_ID"
OPENCLAW_CLAUDE_CODE_RUNTIME_ROOT = "OPENCLAW_CLAUDE_CODE_RUNTIME_ROOT"
OPENCLAW_CLAUDE_CODE_ARTIFACTS_DIR = "OPENCLAW_CLAUDE_CODE_ARTIFACTS_DIR"


def launch_headless_runner(settings: RuntimeSettings, job_id: str) -> int:
    command = [
        settings.python_bin,
        str(settings.entry_script),
        "--runtime-root",
        str(settings.paths.runtime_root),
        "runner",
        "--job-id",
        job_id,
        "--mode",
        "headless",
    ]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise BridgeError("start_failed", f"启动 Claude Code 失败：{exc}.") from exc
    return process.pid


def launch_tmux_runner(settings: RuntimeSettings, job_id: str, cwd: str) -> tuple[str, str]:
    socket_path = str(settings.paths.job_paths(job_id).job_dir / "tmux.sock")
    session_name = f"claude_code_{job_id}"
    command = [
        settings.tmux_bin,
        "-S",
        socket_path,
        "new-session",
        "-d",
        "-s",
        session_name,
        settings.python_bin,
        str(settings.entry_script),
        "--runtime-root",
        str(settings.paths.runtime_root),
        "runner",
        "--job-id",
        job_id,
        "--mode",
        "tmux",
    ]
    try:
        subprocess.run(
            command,
            check=True,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise BridgeError("start_failed", f"启动 Claude Code 失败：{exc}.") from exc
    except subprocess.CalledProcessError as exc:
        raise BridgeError("start_failed", f"启动 Claude Code 失败：tmux 启动命令退出码 {exc.returncode}。") from exc
    return socket_path, session_name


def run_job(settings: RuntimeSettings, job: JobRecord, mode: str) -> dict[str, Any]:
    job_paths = settings.paths.job_paths(job.job_id)
    ensure_text_file(job_paths.stdout_path)
    ensure_text_file(job_paths.stderr_path)
    command = build_claude_command(job, settings, job_paths)
    env = build_job_env(job, settings, job_paths)

    try:
        exit_code = _run_direct(command, env, job_paths, job)
    except BaseException as exc:  # pragma: no cover - defensive fallback for runner process
        with job_paths.stderr_path.open("a", encoding="utf-8") as handle:
            handle.write(f"Runner failed: {exc}\n")
        exit_code = 1

    write_text(job_paths.exit_code_path, f"{exit_code}\n")

    from openclaw_claude_code.service import finalize_job

    finalize_job(settings, job_id=job.job_id, by="runner")

    return {
        "job_id": job.job_id,
        "exit_code": exit_code,
    }


def build_job_env(job: JobRecord, settings: RuntimeSettings, job_paths: JobPaths) -> dict[str, str]:
    env = os.environ.copy()
    env[OPENCLAW_CLAUDE_CODE_JOB_ID] = job.job_id
    env[OPENCLAW_CLAUDE_CODE_RUNTIME_ROOT] = str(settings.paths.runtime_root)
    if job.agent_teams_enabled:
        env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
    if job.artifacts_required:
        env[OPENCLAW_CLAUDE_CODE_ARTIFACTS_DIR] = str(job_paths.artifacts_dir)
    return env


def build_claude_command(job: JobRecord, settings: RuntimeSettings, job_paths: JobPaths) -> list[str]:
    prompt = job.prompt
    if job.artifacts_required:
        prompt = (
            f"{prompt}\n\n"
            f"[OpenClaw Claude Code delivery]\n"
            f"- 所有需要交付的文件必须写入目录：{job_paths.artifacts_dir}\n"
            "- 不要把需要交付的文件写到该目录之外。\n"
            "- 最终回复里可说明已写入哪些文件。"
        )

    command = [
        settings.claude_bin, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", job.permission_mode,
    ]
    if job.artifacts_required:
        command.extend(["--add-dir", str(job_paths.artifacts_dir)])
    if job.agent_teams_enabled and job.teammate_mode:
        command.extend(["--teammate-mode", job.teammate_mode])
    command.extend(settings.claude_extra_args)
    return command


def cancel_headless(process_pid: int | None) -> None:
    if process_pid is None:
        raise BridgeError("cancel_handle_missing", "当前任务缺少可取消的进程句柄。")
    try:
        os.killpg(process_pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise BridgeError("cancel_handle_missing", f"无法终止进程组 {process_pid}：{exc}.") from exc


def cancel_tmux(settings: RuntimeSettings, socket_path: str | None, session_name: str | None) -> None:
    if not socket_path or not session_name:
        raise BridgeError("cancel_handle_missing", "当前任务缺少可取消的 tmux 句柄。")
    command = [settings.tmux_bin, "-S", socket_path, "kill-session", "-t", session_name]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise BridgeError("cancel_handle_missing", "tmux 不可用，无法取消当前任务。") from exc
    except subprocess.CalledProcessError:
        return



def _run_direct(command: list[str], env: dict[str, str], job_paths: JobPaths, job: JobRecord) -> int:
    stdout_fd = os.open(str(job_paths.stdout_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        with job_paths.stderr_path.open("a", encoding="utf-8") as stderr_handle:
            try:
                completed = subprocess.run(
                    command,
                    cwd=job.cwd,
                    env=env,
                    stdout=stdout_fd,
                    stderr=stderr_handle,
                )
                return completed.returncode
            except OSError as exc:
                stderr_handle.write(f"{exc}\n")
                return 127
    finally:
        os.close(stdout_fd)


def _parse_stream_json_result(job_paths: JobPaths) -> str | None:
    """Extract the final result text from stream-json JSONL output.

    Scans stdout.log from the end looking for the ``result`` event which
    contains the ``result`` field with Claude Code's final answer.
    Falls back to collecting the last assistant text blocks if no result
    event is found.
    """
    try:
        lines = job_paths.stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result_text = event.get("result", "")
            if result_text:
                return result_text
            break

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            content_blocks = event.get("message", {}).get("content", [])
            texts = [b["text"] for b in content_blocks if b.get("type") == "text" and b.get("text")]
            if texts:
                return "\n".join(texts)

    return None


def extract_stream_metadata(job_paths: JobPaths) -> dict[str, Any]:
    """Extract cost, duration, tokens, model, and permission denials from the result event."""
    empty: dict[str, Any] = {
        "cost_usd": None,
        "duration_seconds": None,
        "num_turns": None,
        "input_tokens": None,
        "output_tokens": None,
        "model": None,
        "permission_denials": None,
    }
    try:
        lines = job_paths.stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return empty

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if event.get("type") != "result":
            continue

        duration_ms = event.get("duration_ms")
        usage = event.get("usage", {})
        model_usage = event.get("modelUsage", {})
        primary_model = next(iter(model_usage), None) if model_usage else None

        denials = event.get("permission_denials") or []
        denials_clean = [
            {"tool": d.get("tool_name", "?"), "input": d.get("tool_input", {})}
            for d in denials
        ] if denials else None

        return {
            "cost_usd": event.get("total_cost_usd"),
            "duration_seconds": round(duration_ms / 1000) if duration_ms else None,
            "num_turns": event.get("num_turns"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "model": primary_model,
            "permission_denials": denials_clean,
        }

    return empty


def summarize_success(job_paths: JobPaths) -> str:
    result_text = _parse_stream_json_result(job_paths)
    if result_text:
        return result_text
    message = tail_chars(job_paths.stdout_path, 4000).strip()
    if not message:
        return "任务已完成，但未捕获到最终文本输出。"
    return message


def summarize_failure(job_paths: JobPaths) -> str:
    result_text = _parse_stream_json_result(job_paths)
    if result_text:
        return result_text
    stderr_message = tail_chars(job_paths.stderr_path, 4000).strip()
    if stderr_message:
        return stderr_message
    stdout_message = tail_chars(job_paths.stdout_path, 4000).strip()
    if stdout_message:
        return stdout_message
    return "任务执行失败，且未捕获到错误输出。"


def _humanize_event(event: dict) -> str | None:
    """Convert a single stream-json event dict into a human-readable line."""
    etype = event.get("type")

    if etype == "system":
        subtype = event.get("subtype", "")
        if subtype == "init":
            model = event.get("model") or ""
            if model:
                return f"🚀 会话启动（模型：{model}）"
        return None

    if etype == "assistant":
        blocks = event.get("message", {}).get("content", [])
        parts: list[str] = []
        for b in blocks:
            if b.get("type") == "text":
                text = b["text"]
                preview = text[:80] + "…" if len(text) > 80 else text
                preview = preview.replace("\n", " ")
                parts.append(f"💬 {preview}")
            elif b.get("type") == "tool_use":
                name = b.get("name", "?")
                tool_input = b.get("input", {})
                detail = ""
                if name in ("Read", "Write") and "file_path" in tool_input:
                    detail = f" → {tool_input['file_path']}"
                elif name == "Bash" and "command" in tool_input:
                    cmd = tool_input["command"]
                    detail = f" → {cmd[:60]}{'…' if len(cmd) > 60 else ''}"
                elif name == "Glob" and "pattern" in tool_input:
                    detail = f" → {tool_input['pattern']}"
                parts.append(f"🔧 {name}{detail}")
        return " | ".join(parts) if parts else None

    if etype == "result":
        subtype = event.get("subtype", "")
        cost = event.get("total_cost_usd", 0)
        turns = event.get("num_turns", 0)
        if subtype == "success":
            return f"✅ 完成（{turns} 轮，${cost:.4f}）"
        return f"❌ 失败（{turns} 轮，${cost:.4f}）"

    return None


def humanize_stream_events(raw_lines: str) -> list[str]:
    """Parse raw JSONL text and return a list of human-readable activity lines."""
    activities: list[str] = []
    for raw in raw_lines.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = _json.loads(raw)
        except _json.JSONDecodeError:
            continue
        line = _humanize_event(event)
        if line:
            activities.append(line)
    return activities
