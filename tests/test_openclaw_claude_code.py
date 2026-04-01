from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI_SCRIPT = ROOT / "scripts" / "bridge.py"


def run_cli(
    runtime_root: Path,
    *args: str,
    env: dict[str, str],
    input_text: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    command = [sys.executable, str(CLI_SCRIPT), "--runtime-root", str(runtime_root), *args]
    completed = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        env=env,
        cwd=ROOT,
    )
    assert completed.stdout, completed.stderr
    return completed, json.loads(completed.stdout)


def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for condition.")


def configure(runtime_root: Path, env: dict[str, str], *, lines: int = 120, max_jobs: int = 2) -> None:
    completed, payload = run_cli(
        runtime_root,
        "config",
        "set",
        "--default-agent-teams-enabled",
        "false",
        "--default-log-tail-lines",
        str(lines),
        "--max-concurrent-jobs",
        str(max_jobs),
        env=env,
    )
    assert completed.returncode == 0
    assert payload["onboarding_completed"] is True


def read_job(runtime_root: Path, job_id: str) -> dict[str, object]:
    return json.loads((runtime_root / "jobs" / job_id / "job.json").read_text(encoding="utf-8"))


def read_events(runtime_root: Path, job_id: str) -> list[dict[str, object]]:
    path = runtime_root / "jobs" / job_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_hook_stdin(cwd: Path, *, session_id: str = "sess_123", hook_event_name: str = "Stop") -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "cwd": str(cwd),
            "hook_event_name": hook_event_name,
        }
    )


@pytest.fixture
def fake_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    fake_claude = bin_dir / "fake-claude.py"
    fake_claude.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            from __future__ import annotations

            import json
            import os
            import sys
            import time
            from pathlib import Path

            scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "success")
            stdout_text = os.environ.get("FAKE_CLAUDE_STDOUT", "任务执行完成。")
            stderr_text = os.environ.get("FAKE_CLAUDE_STDERR", "任务执行失败。")
            sleep_s = float(os.environ.get("FAKE_CLAUDE_SLEEP", "0"))
            artifacts_dir = os.environ.get("OPENCLAW_CLAUDE_CODE_ARTIFACTS_DIR")

            if artifacts_dir and os.environ.get("FAKE_CLAUDE_WRITE_ARTIFACT") == "1":
                target = Path(artifacts_dir)
                target.mkdir(parents=True, exist_ok=True)
                (target / "report.md").write_text("artifact generated\\n", encoding="utf-8")

            # Emit stream-json JSONL output
            init_event = {"type": "system", "subtype": "init", "session_id": "fake",
                          "tools": [], "mcp_servers": []}
            print(json.dumps(init_event), flush=True)

            if stdout_text:
                for line in stdout_text.strip().splitlines():
                    evt = {"type": "assistant", "session_id": "fake",
                           "message": {"id": "msg", "type": "message", "role": "assistant",
                                       "content": [{"type": "text", "text": line}],
                                       "usage": {"input_tokens": 10, "output_tokens": 5}}}
                    print(json.dumps(evt), flush=True)

            if sleep_s:
                time.sleep(sleep_s)

            if scenario == "failure":
                if stderr_text:
                    print(stderr_text, end="", file=sys.stderr, flush=True)
                err_evt = {"type": "result", "subtype": "error", "session_id": "fake",
                           "is_error": True, "result": stderr_text or "",
                           "total_cost_usd": 0.001, "duration_ms": 100, "num_turns": 1}
                print(json.dumps(err_evt), flush=True)
                sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT_CODE", "1")))

            ok_evt = {"type": "result", "subtype": "success", "session_id": "fake",
                      "is_error": False, "result": stdout_text.strip() if stdout_text else "",
                      "total_cost_usd": 0.01, "duration_ms": 500, "num_turns": 1}
            print(json.dumps(ok_evt), flush=True)
            sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT_CODE", "0")))
            """
        ),
        encoding="utf-8",
    )
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    fake_tmux = bin_dir / "fake-tmux.py"
    fake_tmux.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            from __future__ import annotations

            import json
            import os
            import signal
            import subprocess
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            socket_path = None
            if args[:1] == ["-S"]:
                socket_path = args[1]
                args = args[2:]

            if socket_path is None:
                socket_path = "/tmp/fake-tmux.sock"

            state_path = Path(socket_path + ".json")
            state_path.parent.mkdir(parents=True, exist_ok=True)
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
            else:
                state = {}

            command = args[0]
            if command == "new-session":
                session = args[args.index("-s") + 1]
                cmd = args[args.index(session) + 1 :]
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                state[session] = proc.pid
                state_path.write_text(json.dumps(state), encoding="utf-8")
                sys.exit(0)

            if command == "kill-session":
                session = args[args.index("-t") + 1]
                pid = state.pop(session, None)
                if pid is not None:
                    try:
                        os.killpg(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if state:
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                else:
                    state_path.unlink(missing_ok=True)
                sys.exit(0)

            sys.exit(1)
            """
        ),
        encoding="utf-8",
    )
    fake_tmux.chmod(fake_tmux.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env["OPENCLAW_CLAUDE_CODE_CLAUDE_BIN"] = str(fake_claude)
    env["OPENCLAW_CLAUDE_CODE_TMUX_BIN"] = str(fake_tmux)
    env["OPENCLAW_CLAUDE_CODE_PYTHON_BIN"] = sys.executable
    env["PYTHONUNBUFFERED"] = "1"
    return env


def test_config_round_trip(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    completed, payload = run_cli(runtime_root, "config", "inspect", env=fake_env)
    assert completed.returncode == 0
    assert payload == {
        "onboarding_required": True,
        "onboarding_completed": False,
        "default_agent_teams_enabled": False,
        "default_log_tail_lines": 4,
        "max_concurrent_jobs": 2,
        "default_cwd": str(Path.home()),
        "default_notify_channel": None,
        "default_notify_target": None,
        "default_permission_mode": "bypassPermissions",
    }

    configure(runtime_root, fake_env, lines=42, max_jobs=3)
    completed, payload = run_cli(runtime_root, "config", "inspect", env=fake_env)
    assert completed.returncode == 0
    assert payload["onboarding_required"] is False
    assert payload["default_log_tail_lines"] == 42
    assert payload["max_concurrent_jobs"] == 3


def test_headless_lifecycle_with_finalize_and_acknowledge(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env, lines=2)
    env = fake_env | {"FAKE_CLAUDE_STDOUT": "line1\nline2\nline3\n"}

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "do work",
        "--cwd",
        str(tmp_path),
        env=env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])

    result_path = runtime_root / "jobs" / job_id / "result.json"
    wait_for(result_path.exists)

    completed, result_payload = run_cli(runtime_root, "result", "--job-id", job_id, env=env)
    assert completed.returncode == 0
    assert result_payload["outcome"] == "completed"
    assert result_payload["message"] == "line1\nline2\nline3"

    completed, logs_payload = run_cli(runtime_root, "logs", "--job-id", job_id, env=env)
    assert completed.returncode == 0
    assert logs_payload["lines"] == 2
    assert "activities" in logs_payload
    assert isinstance(logs_payload["activities"], list)

    completed, finalize_payload = run_cli(
        runtime_root,
        "hook",
        "finalize",
        "--stdin-json",
        env=env | {"OPENCLAW_CLAUDE_CODE_JOB_ID": job_id},
        input_text=build_hook_stdin(tmp_path),
    )
    assert completed.returncode == 0
    assert finalize_payload["status"] == "completed"

    completed, ack_payload = run_cli(runtime_root, "acknowledge", "--job-id", job_id, env=env)
    assert completed.returncode == 0
    assert ack_payload["status"] == "acknowledged"

    events = read_events(runtime_root, job_id)
    statuses = [event["status"] for event in events]
    assert statuses == ["accepted", "running", "completed", "acknowledged"]
    completed_event = next(e for e in events if e["status"] == "completed")
    assert completed_event["by"] == "runner"


def test_submit_startup_failure_writes_failed_result(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)
    env = fake_env | {"OPENCLAW_CLAUDE_CODE_PYTHON_BIN": str(tmp_path / "missing-python")}

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "do work",
        "--cwd",
        str(tmp_path),
        env=env,
    )
    assert completed.returncode == 1
    assert payload["error_code"] == "start_failed"

    job_dirs = list((runtime_root / "jobs").iterdir())
    assert len(job_dirs) == 1
    job_id = job_dirs[0].name
    job = read_job(runtime_root, job_id)
    assert job["status"] == "failed"

    result_payload = json.loads((runtime_root / "jobs" / job_id / "result.json").read_text(encoding="utf-8"))
    assert result_payload["outcome"] == "failed"
    assert result_payload["exit_code"] is None


def test_hook_finalize_missing_exit_code_marks_failed(tmp_path: Path, fake_env: dict[str, str]) -> None:
    """If runner is already gone and left no exit-code.txt, compensation should fail the job."""
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)

    job_id = "job_0000000000000_aaaaaa"
    job_dir = runtime_root / "jobs" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "hook-finalize").mkdir()

    job_json = {
        "job_id": job_id,
        "task_name": job_id,
        "prompt": "do work",
        "cwd": str(tmp_path),
        "status": "running",
        "agent_teams_enabled": False,
        "teammate_mode": None,
        "artifacts_required": False,
        "process_pid": 99999,
        "tmux_socket_path": None,
        "tmux_session_name": None,
        "created_at": "2026-03-24T00:00:00Z",
        "started_at": "2026-03-24T00:00:01Z",
        "completed_at": None,
        "acknowledged_at": None,
        "updated_at": "2026-03-24T00:00:01Z",
    }
    (job_dir / "job.json").write_text(json.dumps(job_json), encoding="utf-8")
    (job_dir / "events.jsonl").write_text(
        '{"ts":"2026-03-24T00:00:00Z","status":"accepted","by":"submit"}\n'
        '{"ts":"2026-03-24T00:00:01Z","status":"running","by":"submit"}\n',
        encoding="utf-8",
    )

    completed, payload = run_cli(
        runtime_root,
        "hook",
        "finalize",
        "--stdin-json",
        env=fake_env | {"OPENCLAW_CLAUDE_CODE_JOB_ID": job_id},
        input_text=build_hook_stdin(tmp_path),
    )
    assert completed.returncode == 0
    assert payload["status"] == "failed"

    completed, result_payload = run_cli(runtime_root, "result", "--job-id", job_id, env=fake_env)
    assert completed.returncode == 0
    assert result_payload["message"] == "任务收尾失败：runner 已退出，但未写入 exit-code.txt。"


def test_hook_finalize_waits_for_runner_exit_code(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)

    job_id = "job_0000000000000_race01"
    job_dir = runtime_root / "jobs" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "hook-finalize").mkdir()
    (job_dir / "stdout.log").write_text("", encoding="utf-8")
    (job_dir / "stderr.log").write_text("", encoding="utf-8")

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.0)"])
    try:
        job_json = {
            "job_id": job_id,
            "task_name": job_id,
            "prompt": "do work",
            "cwd": str(tmp_path),
            "status": "running",
            "agent_teams_enabled": False,
            "teammate_mode": None,
            "artifacts_required": False,
            "permission_mode": "bypassPermissions",
            "process_pid": sleeper.pid,
            "tmux_socket_path": None,
            "tmux_session_name": None,
            "created_at": "2026-03-24T00:00:00Z",
            "started_at": "2026-03-24T00:00:01Z",
            "completed_at": None,
            "acknowledged_at": None,
            "updated_at": "2026-03-24T00:00:01Z",
        }
        (job_dir / "job.json").write_text(json.dumps(job_json), encoding="utf-8")
        (job_dir / "events.jsonl").write_text(
            '{"ts":"2026-03-24T00:00:00Z","status":"accepted","by":"submit"}\n'
            '{"ts":"2026-03-24T00:00:01Z","status":"running","by":"submit"}\n',
            encoding="utf-8",
        )

        def delayed_exit_code_write() -> None:
            time.sleep(0.2)
            (job_dir / "stdout.log").write_text(
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "session_id": "fake",
                        "is_error": False,
                        "result": "runner completed",
                        "total_cost_usd": 0.01,
                        "duration_ms": 500,
                        "num_turns": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (job_dir / "exit-code.txt").write_text("0\n", encoding="utf-8")

        writer = threading.Thread(target=delayed_exit_code_write, daemon=True)
        writer.start()

        env = fake_env | {
            "OPENCLAW_CLAUDE_CODE_JOB_ID": job_id,
            "OPENCLAW_CLAUDE_CODE_RECONCILE_GRACE_SECONDS": "2",
            "OPENCLAW_CLAUDE_CODE_RECONCILE_POLL_SECONDS": "0.05",
        }
        completed, payload = run_cli(
            runtime_root,
            "hook",
            "finalize",
            "--stdin-json",
            env=env,
            input_text=build_hook_stdin(tmp_path),
        )
        writer.join(timeout=2)
        assert completed.returncode == 0
        assert payload["status"] == "completed"

        completed, result_payload = run_cli(runtime_root, "result", "--job-id", job_id, env=fake_env)
        assert completed.returncode == 0
        assert result_payload["outcome"] == "completed"
        assert result_payload["exit_code"] == 0
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)


def test_hook_finalize_is_idempotent(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)
    env = fake_env | {"FAKE_CLAUDE_STDOUT": "ok\n"}

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "do work",
        "--cwd",
        str(tmp_path),
        env=env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])
    exit_code_path = runtime_root / "jobs" / job_id / "exit-code.txt"
    wait_for(exit_code_path.exists)

    stdin_text = build_hook_stdin(tmp_path, session_id="sess_repeat")
    completed, _ = run_cli(
        runtime_root,
        "hook",
        "finalize",
        "--stdin-json",
        env=env | {"OPENCLAW_CLAUDE_CODE_JOB_ID": job_id},
        input_text=stdin_text,
    )
    assert completed.returncode == 0
    first_events = read_events(runtime_root, job_id)

    completed, second_payload = run_cli(
        runtime_root,
        "hook",
        "finalize",
        "--stdin-json",
        env=env | {"OPENCLAW_CLAUDE_CODE_JOB_ID": job_id},
        input_text=stdin_text,
    )
    assert completed.returncode == 0
    second_events = read_events(runtime_root, job_id)
    assert second_payload["status"] == "completed"
    assert first_events == second_events


def test_cancel_headless_job(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)
    env = fake_env | {"FAKE_CLAUDE_SLEEP": "30", "FAKE_CLAUDE_STDOUT": "running\n"}

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "long task",
        "--cwd",
        str(tmp_path),
        env=env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])

    completed, result_payload = run_cli(runtime_root, "result", "--job-id", job_id, env=env)
    assert completed.returncode == 1
    assert result_payload["error_code"] == "result_not_ready"

    completed, cancel_payload = run_cli(runtime_root, "cancel", "--job-id", job_id, env=env)
    assert completed.returncode == 0
    assert cancel_payload["status"] == "cancelled"

    completed, result_payload = run_cli(runtime_root, "result", "--job-id", job_id, env=env)
    assert completed.returncode == 0
    assert result_payload["outcome"] == "cancelled"


def test_tmux_submit_and_cancel(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)
    env = fake_env | {"FAKE_CLAUDE_SLEEP": "30"}

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "tmux task",
        "--cwd",
        str(tmp_path),
        "--run-mode",
        "tmux",
        env=env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])

    job = read_job(runtime_root, job_id)
    assert job["process_pid"] is None
    assert job["tmux_socket_path"]
    assert job["tmux_session_name"]

    completed, cancel_payload = run_cli(runtime_root, "cancel", "--job-id", job_id, env=env)
    assert completed.returncode == 0
    assert cancel_payload["status"] == "cancelled"


def test_artifacts_are_returned_in_result(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)
    env = fake_env | {"FAKE_CLAUDE_WRITE_ARTIFACT": "1"}

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "generate file",
        "--cwd",
        str(tmp_path),
        "--artifacts-required",
        env=env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])
    exit_code_path = runtime_root / "jobs" / job_id / "exit-code.txt"
    wait_for(exit_code_path.exists)

    completed, payload = run_cli(
        runtime_root,
        "hook",
        "finalize",
        "--stdin-json",
        env=env | {"OPENCLAW_CLAUDE_CODE_JOB_ID": job_id},
        input_text=build_hook_stdin(tmp_path),
    )
    assert completed.returncode == 0
    assert payload["status"] == "completed"

    completed, result_payload = run_cli(runtime_root, "result", "--job-id", job_id, env=env)
    assert completed.returncode == 0
    assert "task_name" in result_payload
    expected_artifact = str((runtime_root / "jobs" / job_id / "artifacts" / "report.md").resolve())
    assert result_payload["artifacts"] == [expected_artifact]


def test_concurrent_submit_respects_max_jobs(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env, max_jobs=1)
    env = fake_env | {"FAKE_CLAUDE_SLEEP": "20", "FAKE_CLAUDE_STDOUT": "busy\n"}

    def submit_once() -> tuple[int, dict[str, object]]:
        completed, payload = run_cli(
            runtime_root,
            "submit",
            "--prompt",
            "long task",
            "--cwd",
            str(tmp_path),
            env=env,
        )
        return completed.returncode, payload

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = [future.result() for future in [executor.submit(submit_once), executor.submit(submit_once)]]

    results = [first, second]
    success = next(item for item in results if item[0] == 0)
    failure = next(item for item in results if item[0] != 0)

    assert success[1]["status"] == "running"
    assert failure[1]["error_code"] == "max_concurrent_jobs_reached"

    run_cli(runtime_root, "cancel", "--job-id", str(success[1]["job_id"]), env=env)


def test_list_jobs(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)

    completed, payload = run_cli(runtime_root, "list", env=fake_env)
    assert completed.returncode == 0
    assert payload["jobs"] == []

    env = fake_env | {"FAKE_CLAUDE_SLEEP": "30"}
    completed, submit_payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "task one",
        "--cwd",
        str(tmp_path),
        "--task-name",
        "my-task",
        env=env,
    )
    assert completed.returncode == 0

    completed, payload = run_cli(runtime_root, "list", env=fake_env)
    assert completed.returncode == 0
    assert len(payload["jobs"]) == 1
    assert payload["jobs"][0]["task_name"] == "my-task"
    assert payload["jobs"][0]["status"] == "running"

    completed, payload = run_cli(runtime_root, "list", "--status", "completed", env=fake_env)
    assert completed.returncode == 0
    assert payload["jobs"] == []

    run_cli(runtime_root, "cancel", "--job-id", str(submit_payload["job_id"]), env=env)


def test_default_run_mode_is_headless(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "default mode task",
        "--cwd",
        str(tmp_path),
        env=fake_env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])
    job = read_job(runtime_root, job_id)
    assert job["process_pid"] is not None
    assert job["tmux_socket_path"] is None
    assert job["tmux_session_name"] is None

    result_path = runtime_root / "jobs" / job_id / "result.json"
    wait_for(result_path.exists)


def test_logs_lines_overrides_default(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env, lines=2)
    env = fake_env | {"FAKE_CLAUDE_STDOUT": "a\nb\nc\nd\ne\n"}

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "log task",
        "--cwd",
        str(tmp_path),
        env=env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])

    result_path = runtime_root / "jobs" / job_id / "result.json"
    wait_for(result_path.exists)

    # stdout is stream-json JSONL: 1 init + 5 assistant + 1 result = 7 lines
    # logs returns humanized activities instead of raw JSON
    completed, default_logs = run_cli(runtime_root, "logs", "--job-id", job_id, env=env)
    assert completed.returncode == 0
    assert default_logs["lines"] == 2
    activities_2 = default_logs["activities"]
    assert isinstance(activities_2, list)
    assert len(activities_2) <= 2

    completed, custom_logs = run_cli(runtime_root, "logs", "--job-id", job_id, "--lines", "4", env=env)
    assert completed.returncode == 0
    assert custom_logs["lines"] == 4
    activities_4 = custom_logs["activities"]
    assert isinstance(activities_4, list)
    assert len(activities_4) <= 4
    assert len(activities_4) > len(activities_2)


def test_acknowledge_rejects_non_terminal_state(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)
    env = fake_env | {"FAKE_CLAUDE_SLEEP": "30"}

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "running task",
        "--cwd",
        str(tmp_path),
        env=env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])

    completed, ack_payload = run_cli(runtime_root, "acknowledge", "--job-id", job_id, env=env)
    assert completed.returncode == 1
    assert ack_payload["error_code"] == "invalid_state_transition"

    run_cli(runtime_root, "cancel", "--job-id", job_id, env=env)


def test_submit_rejects_nonexistent_cwd(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "do work",
        "--cwd",
        str(tmp_path / "nonexistent"),
        env=fake_env,
    )
    assert completed.returncode == 1
    assert payload["error_code"] == "invalid_arguments"
    assert "不存在" in payload["message"]


def test_preflight_all_found(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    completed, payload = run_cli(runtime_root, "preflight", env=fake_env)
    assert completed.returncode == 0
    assert payload["ok"] is True
    checks = payload["checks"]
    assert checks["uv"]["status"] == "found"
    assert checks["python_version"]["status"] == "ok"
    assert checks["venv_ready"]["status"] == "ok"
    assert checks["claude_bin"]["status"] == "found"
    assert checks["claude_bin"]["path"] is not None


def test_preflight_claude_missing(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    env_no_claude = {**fake_env, "OPENCLAW_CLAUDE_CODE_CLAUDE_BIN": "nonexistent-binary-xyz"}
    completed, payload = run_cli(runtime_root, "preflight", env=env_no_claude)
    assert completed.returncode == 0
    assert payload["ok"] is False
    assert payload["checks"]["claude_bin"]["status"] == "not_found"
    assert payload["checks"]["claude_bin"]["path"] is None


def test_hook_finalize_requires_job_id_env(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)

    completed, payload = run_cli(
        runtime_root,
        "hook",
        "finalize",
        "--stdin-json",
        env=fake_env,
        input_text=build_hook_stdin(tmp_path),
    )
    assert completed.returncode == 1
    assert payload["error_code"] == "hook_job_id_missing"


def configure_with_notify(
    runtime_root: Path,
    env: dict[str, str],
    *,
    channel: str = "telegram",
    target: str = "-123456",
) -> None:
    completed, payload = run_cli(
        runtime_root,
        "config",
        "set",
        "--default-agent-teams-enabled",
        "false",
        "--default-log-tail-lines",
        "120",
        "--max-concurrent-jobs",
        "2",
        "--default-notify-channel",
        channel,
        "--default-notify-target",
        target,
        env=env,
    )
    assert completed.returncode == 0
    assert payload["default_notify_channel"] == channel
    assert payload["default_notify_target"] == target


def test_config_set_with_notify_params(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure_with_notify(runtime_root, fake_env, channel="discord", target="99999")

    completed, payload = run_cli(runtime_root, "config", "inspect", env=fake_env)
    assert completed.returncode == 0
    assert payload["default_notify_channel"] == "discord"
    assert payload["default_notify_target"] == "99999"


def test_submit_inherits_config_notify_defaults(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure_with_notify(runtime_root, fake_env, channel="telegram", target="-999")

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "notify test",
        "--cwd",
        str(tmp_path),
        env=fake_env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])

    job = read_job(runtime_root, job_id)
    assert job["notify_channel"] == "telegram"
    assert job["notify_target"] == "-999"

    result_path = runtime_root / "jobs" / job_id / "result.json"
    wait_for(result_path.exists)


def test_submit_overrides_notify_params(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure_with_notify(runtime_root, fake_env, channel="telegram", target="-999")

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "notify override test",
        "--cwd",
        str(tmp_path),
        "--notify-channel",
        "discord",
        "--notify-target",
        "12345",
        env=fake_env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])

    job = read_job(runtime_root, job_id)
    assert job["notify_channel"] == "discord"
    assert job["notify_target"] == "12345"

    result_path = runtime_root / "jobs" / job_id / "result.json"
    wait_for(result_path.exists)


def test_submit_no_notify_when_not_configured(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"
    configure(runtime_root, fake_env)

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "no notify test",
        "--cwd",
        str(tmp_path),
        env=fake_env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])

    job = read_job(runtime_root, job_id)
    assert job["notify_channel"] is None
    assert job["notify_target"] is None

    result_path = runtime_root / "jobs" / job_id / "result.json"
    wait_for(result_path.exists)


def test_notification_fires_on_finalize(tmp_path: Path, fake_env: dict[str, str]) -> None:
    """Verify _try_send_notification is called with correct args by using a fake openclaw."""
    runtime_root = tmp_path / "runtime"

    bin_dir = tmp_path / "notifybin"
    bin_dir.mkdir()
    fake_openclaw = bin_dir / "fake-openclaw.py"
    log_file = tmp_path / "notify-calls.log"
    fake_openclaw.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys
            from pathlib import Path
            Path("{log_file}").write_text(" ".join(sys.argv[1:]), encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    fake_openclaw.chmod(fake_openclaw.stat().st_mode | stat.S_IEXEC)

    env = fake_env | {"OPENCLAW_CLAUDE_CODE_OPENCLAW_BIN": str(fake_openclaw)}
    configure_with_notify(runtime_root, env, channel="telegram", target="-5555")

    completed, payload = run_cli(
        runtime_root,
        "submit",
        "--prompt",
        "notification test",
        "--cwd",
        str(tmp_path),
        env=env,
    )
    assert completed.returncode == 0
    job_id = str(payload["job_id"])

    result_path = runtime_root / "jobs" / job_id / "result.json"
    wait_for(result_path.exists)

    wait_for(log_file.exists)
    call_args = log_file.read_text(encoding="utf-8")
    assert "agent" in call_args
    assert "--deliver" in call_args
    assert "--channel telegram" in call_args
    assert "--to -5555" in call_args
    assert job_id in call_args
    assert "[openclaw-claude-code] task completed" in call_args
    assert f"uv run python scripts/bridge.py result --job-id {job_id}" in call_args


def test_preflight_shows_openclaw_bin(tmp_path: Path, fake_env: dict[str, str]) -> None:
    runtime_root = tmp_path / "runtime"

    env_no_openclaw = {**fake_env, "OPENCLAW_CLAUDE_CODE_OPENCLAW_BIN": "nonexistent-openclaw-xyz"}
    completed, payload = run_cli(runtime_root, "preflight", env=env_no_openclaw)
    assert completed.returncode == 0
    assert payload["ok"] is True
    assert payload["checks"]["openclaw_bin"]["status"] == "not_found"

    bin_dir = tmp_path / "oclawbin"
    bin_dir.mkdir()
    fake_oc = bin_dir / "fake-oc.py"
    fake_oc.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    fake_oc.chmod(fake_oc.stat().st_mode | stat.S_IEXEC)

    env_with_openclaw = {**fake_env, "OPENCLAW_CLAUDE_CODE_OPENCLAW_BIN": str(fake_oc)}
    completed, payload = run_cli(runtime_root, "preflight", env=env_with_openclaw)
    assert completed.returncode == 0
    assert payload["ok"] is True
    assert payload["checks"]["openclaw_bin"]["status"] == "found"
    assert payload["checks"]["openclaw_bin"]["path"] == str(fake_oc)
