#!/usr/bin/env python3
"""Interactive debug harness that simulates the OpenClaw → openclaw-claude-code workflow.

Usage:
    uv run python scripts/debug_run.py

The script walks through the same steps OpenClaw would:
  1. preflight  — verify environment
  2. config     — inspect / onboard if needed
  3. submit     — accept a task from the user interactively
  4. poll       — watch job status and tail logs in real time
  5. result     — display the final result JSON when done
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI_SCRIPT = [sys.executable, str(ROOT / "scripts" / "bridge.py")]

POLL_INTERVAL = 3
LOG_TAIL_LINES = 200


def run_cli(*args: str, input_text: str | None = None, echo: bool = True) -> tuple[int, dict | None]:
    cmd = [*CLI_SCRIPT, *args]
    if echo:
        shell_cmd = "uv run python scripts/bridge.py " + " ".join(shlex.quote(a) for a in args)
        print(f"\n  $ {shell_cmd}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input_text,
        cwd=str(ROOT),
    )
    payload = None
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        print(f"  [ERROR] exit={result.returncode}: {stderr}")
    return result.returncode, payload


def step_preflight() -> bool:
    print("\n=== Step 1: Preflight ===")
    rc, payload = run_cli("preflight")
    if rc != 0 or not payload:
        print("  preflight failed")
        return False
    print(f"  ok: {payload.get('ok')}")
    for name, check in payload.get("checks", {}).items():
        status = check.get("status", "?")
        path = check.get("path", "")
        marker = "✓" if status == "found" else "✗" if status == "not_found" else "?"
        print(f"  {marker} {name}: {status}  {path}")
    if not payload.get("ok"):
        print("\n  Environment check failed. Fix the issues above and retry.")
        return False
    return True


def step_config() -> bool:
    print("\n=== Step 2: Config Inspect ===")
    rc, payload = run_cli("config", "inspect")
    if rc != 0 or not payload:
        print("  config inspect failed")
        return False
    if payload.get("onboarding_required"):
        default_workspace = prompt_input("Default workspace directory", str(Path.cwd()))
        print("  Onboarding required — running default config set...")
        rc2, _ = run_cli(
            "config", "set",
            "--default-agent-teams-enabled", "false",
            "--default-log-tail-lines", "4",
            "--max-concurrent-jobs", "2",
            "--default-cwd", default_workspace,
        )
        if rc2 != 0:
            print("  config set failed")
            return False
        print("  Onboarding complete with defaults.")
    else:
        print("  Already configured.")
    return True


def prompt_input(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"  {label}{suffix}: ").strip()
    return value or default


def _get_default_cwd() -> str:
    """Read default_cwd from config, fall back to current directory."""
    rc, payload = run_cli("config", "inspect", echo=False)
    if rc == 0 and payload:
        configured = payload.get("default_cwd")
        if configured:
            return configured
    return str(Path.cwd())


def step_submit() -> str | None:
    print("\n=== Step 3: Submit Task ===")
    prompt = prompt_input("Task prompt (required)")
    if not prompt:
        print("  No prompt provided. Aborting.")
        return None

    default_dir = _get_default_cwd()
    cwd = prompt_input("Working directory", default_dir)
    if not cwd:
        print("  No working directory provided. Aborting.")
        return None
    task_name = prompt_input("Task name (optional)", "")
    artifacts = prompt_input("Require artifacts? (y/n)", "n")
    run_mode = prompt_input("Run mode (headless/tmux)", "headless")

    args = ["submit", "--prompt", prompt, "--cwd", cwd]
    if task_name:
        args.extend(["--task-name", task_name])
    if artifacts.lower() in ("y", "yes"):
        args.append("--artifacts-required")
    if run_mode == "tmux":
        args.extend(["--run-mode", "tmux"])

    print(f"\n  Submitting...")
    rc, payload = run_cli(*args)
    if rc != 0 or not payload:
        return None

    job_id = payload.get("job_id", "")
    print(f"  Job submitted: {job_id}")
    print(f"  Status: {payload.get('status')}")

    show_claude_command(job_id)
    return job_id


def show_claude_command(job_id: str) -> None:
    """Read job.json and reconstruct the claude command that will be executed."""
    from openclaw_claude_code.runtime import build_runtime_settings

    settings = build_runtime_settings(None)
    job_paths = settings.paths.job_paths(job_id)
    job_json = job_paths.job_dir / "job.json"
    if not job_json.exists():
        return

    with job_json.open(encoding="utf-8") as f:
        job_data = json.load(f)

    claude_bin = settings.claude_bin
    prompt = job_data.get("prompt", "")
    cwd_val = job_data.get("cwd", "")
    agent_teams = job_data.get("agent_teams_enabled", False)
    teammate_mode = job_data.get("teammate_mode")
    artifacts_required = job_data.get("artifacts_required", False)
    artifacts_dir = str(job_paths.artifacts_dir)

    if artifacts_required:
        prompt += (
            f"\n\n[OpenClaw Claude Code delivery]\n"
            f"- 所有需要交付的文件必须写入目录：{artifacts_dir}\n"
            "- 不要把需要交付的文件写到该目录之外。\n"
            "- 最终回复里可说明已写入哪些文件。"
        )

    permission_mode = job_data.get("permission_mode", "")
    cmd_parts = [claude_bin, "-p", json.dumps(prompt, ensure_ascii=False),
                 "--output-format", "stream-json", "--verbose"]
    if permission_mode:
        cmd_parts.extend(["--permission-mode", permission_mode])
    if artifacts_required:
        cmd_parts.extend(["--add-dir", artifacts_dir])
    if agent_teams and teammate_mode:
        cmd_parts.extend(["--teammate-mode", teammate_mode])
    for arg in settings.claude_extra_args:
        cmd_parts.append(arg)

    print(f"\n  --- Claude Code command ---")
    print(f"  cwd: {cwd_val}")
    print(f"  {' '.join(cmd_parts)}")

    env_vars = []
    if agent_teams:
        env_vars.append("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1")
    if artifacts_required:
        env_vars.append(f"OPENCLAW_CLAUDE_CODE_ARTIFACTS_DIR={artifacts_dir}")
    env_vars.append(f"OPENCLAW_CLAUDE_CODE_JOB_ID={job_id}")
    env_vars.append(f"OPENCLAW_CLAUDE_CODE_RUNTIME_ROOT={settings.paths.runtime_root}")
    if env_vars:
        print(f"  env: {' '.join(env_vars)}")
    print()


def step_poll(job_id: str) -> None:
    print(f"\n=== Step 4: Polling job {job_id} ===")
    print(f"  (polling every {POLL_INTERVAL}s, Ctrl+C to stop polling)\n")

    seen_count = 0
    while True:
        try:
            rc, payload = run_cli("status", "--job-id", job_id, echo=False)
            if payload:
                status = payload.get("status", "?")
                elapsed = payload.get("elapsed_seconds")
                elapsed_str = f" ({elapsed}s)" if elapsed is not None else ""
                print(f"  [{time.strftime('%H:%M:%S')}] status={status}{elapsed_str}")

                if status in ("completed", "failed", "cancelled", "acknowledged"):
                    print(f"\n  Job reached terminal state: {status}")
                    return

            rc_log, log_payload = run_cli(
                "logs", "--job-id", job_id, "--lines", str(LOG_TAIL_LINES), echo=False,
            )
            if log_payload:
                activities = log_payload.get("activities", [])
                if len(activities) > seen_count:
                    new_items = activities[seen_count:]
                    for act in new_items:
                        print(f"  │ {act}")
                    seen_count = len(activities)

            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n  Polling stopped by user.")
            return


def step_result(job_id: str) -> None:
    print(f"\n=== Step 5: Result for {job_id} ===")
    rc, payload = run_cli("result", "--job-id", job_id)
    if rc != 0 or not payload:
        print("  Could not retrieve result (job may still be running).")
        return

    print(f"  outcome:    {payload.get('outcome')}")
    print(f"  task_name:  {payload.get('task_name')}")
    print(f"  exit_code:  {payload.get('exit_code')}")
    print(f"  completed:  {payload.get('completed_at')}")

    artifacts = payload.get("artifacts", [])
    if artifacts:
        print(f"  artifacts:")
        for a in artifacts:
            print(f"    - {a}")

    message = payload.get("message", "")
    print(f"\n  --- message (last 2000 chars) ---")
    display = message[-2000:] if len(message) > 2000 else message
    for line in display.splitlines():
        print(f"  │ {line}")

    print(f"\n  --- raw JSON ---")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> int:
    print("╔════════════════════════════════════════════╗")
    print("║  openclaw-claude-code debug harness              ║")
    print("║  Simulates OpenClaw → Claude Code workflow ║")
    print("╚════════════════════════════════════════════╝")

    if not step_preflight():
        return 1
    if not step_config():
        return 1

    job_id = step_submit()
    if not job_id:
        return 1

    step_poll(job_id)
    step_result(job_id)

    print("\n  Done. You can also inspect raw files at:")
    print(f"    stdout.log:  runtime/jobs/{job_id}/stdout.log")
    print(f"    stderr.log:  runtime/jobs/{job_id}/stderr.log")
    print(f"    result.json: runtime/jobs/{job_id}/result.json")
    print(f"    job.json:    runtime/jobs/{job_id}/job.json")

    rc_ack, _ = run_cli("acknowledge", "--job-id", job_id)
    if rc_ack == 0:
        print(f"\n  Job {job_id} acknowledged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
