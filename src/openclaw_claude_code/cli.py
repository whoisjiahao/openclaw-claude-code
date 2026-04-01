from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from openclaw_claude_code.errors import BridgeError
from openclaw_claude_code.runtime import build_runtime_settings
from openclaw_claude_code.service import (
    acknowledge,
    cancel,
    hook_finalize,
    inspect_config,
    list_jobs,
    logs,
    preflight,
    result,
    run_runner,
    set_config,
    status,
    submit,
)


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - exercised via CLI integration tests
        raise BridgeError("invalid_arguments", message)


def build_parser() -> argparse.ArgumentParser:
    parser = JSONArgumentParser(prog="bridge.py")
    parser.add_argument("--runtime-root", dest="runtime_root", default=None)
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=JSONArgumentParser)

    subparsers.add_parser("preflight")

    config_parser = subparsers.add_parser("config")
    config_subparsers = config_parser.add_subparsers(
        dest="config_command",
        required=True,
        parser_class=JSONArgumentParser,
    )
    config_subparsers.add_parser("inspect")
    config_set_parser = config_subparsers.add_parser("set")
    config_set_parser.add_argument("--default-agent-teams-enabled", required=True)
    config_set_parser.add_argument("--default-log-tail-lines", type=int, required=True)
    config_set_parser.add_argument("--max-concurrent-jobs", type=int, required=True)
    config_set_parser.add_argument("--default-cwd", default=None)
    config_set_parser.add_argument("--timezone", default=None)
    config_set_parser.add_argument("--default-notify-channel", default=None)
    config_set_parser.add_argument("--default-notify-target", default=None)
    config_set_parser.add_argument("--default-permission-mode", default=None)

    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--prompt", required=True)
    submit_parser.add_argument("--cwd", required=True)
    submit_parser.add_argument("--task-name")
    submit_parser.add_argument("--run-mode", default="headless")
    agent_group = submit_parser.add_mutually_exclusive_group()
    agent_group.add_argument("--agent-teams", dest="agent_teams", action="store_true")
    agent_group.add_argument("--no-agent-teams", dest="agent_teams", action="store_false")
    submit_parser.set_defaults(agent_teams=None)
    submit_parser.add_argument("--teammate-mode")
    submit_parser.add_argument("--artifacts-required", action="store_true")
    submit_parser.add_argument("--permission-mode", default=None)
    submit_parser.add_argument("--notify-channel", default=None)
    submit_parser.add_argument("--notify-target", default=None)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--status", dest="filter_status", default=None)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--job-id", required=True)

    logs_parser = subparsers.add_parser("logs")
    logs_parser.add_argument("--job-id", required=True)
    logs_parser.add_argument("--lines", type=int)

    result_parser = subparsers.add_parser("result")
    result_parser.add_argument("--job-id", required=True)

    cancel_parser = subparsers.add_parser("cancel")
    cancel_parser.add_argument("--job-id", required=True)

    acknowledge_parser = subparsers.add_parser("acknowledge")
    acknowledge_parser.add_argument("--job-id", required=True)

    hook_parser = subparsers.add_parser("hook")
    hook_subparsers = hook_parser.add_subparsers(dest="hook_command", required=True, parser_class=JSONArgumentParser)
    hook_finalize_parser = hook_subparsers.add_parser("finalize")
    hook_finalize_parser.add_argument("--stdin-json", action="store_true")

    runner_parser = subparsers.add_parser("runner", help=argparse.SUPPRESS)
    runner_parser.add_argument("--job-id", required=True)
    runner_parser.add_argument("--mode", required=True, choices=["headless", "tmux"])

    return parser


def _setup_logging(runtime_root: Any) -> None:
    from pathlib import Path

    log_file = Path(runtime_root) / "openclaw-claude-code.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_file),
        level=logging.ERROR,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        settings = build_runtime_settings(args.runtime_root)
        _setup_logging(settings.paths.runtime_root)
        payload = dispatch(args, settings)
    except BridgeError as exc:
        print(json.dumps(exc.to_payload(), ensure_ascii=False))
        return exc.exit_status
    except Exception as exc:  # pragma: no cover - safety net for CLI consumers
        print(json.dumps({"error_code": "internal_error", "message": f"未处理异常：{exc}"}, ensure_ascii=False))
        return 1

    if payload is not None:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


def dispatch(args: argparse.Namespace, settings: Any) -> dict[str, Any] | None:
    if args.command == "preflight":
        return preflight(settings)
    if args.command == "config":
        if args.config_command == "inspect":
            return inspect_config(settings)
        if args.config_command == "set":
            return set_config(
                settings,
                default_agent_teams_enabled=args.default_agent_teams_enabled,
                default_log_tail_lines=args.default_log_tail_lines,
                max_concurrent_jobs=args.max_concurrent_jobs,
                default_cwd=args.default_cwd,
                timezone=args.timezone,
                default_notify_channel=args.default_notify_channel,
                default_notify_target=args.default_notify_target,
                default_permission_mode=args.default_permission_mode,
            )
    if args.command == "list":
        return list_jobs(settings, filter_status=args.filter_status)
    if args.command == "submit":
        return submit(
            settings,
            prompt=args.prompt,
            cwd=args.cwd,
            task_name=args.task_name,
            run_mode=args.run_mode,
            agent_teams=args.agent_teams,
            teammate_mode=args.teammate_mode,
            artifacts_required=args.artifacts_required,
            permission_mode=args.permission_mode,
            notify_channel=args.notify_channel,
            notify_target=args.notify_target,
        )
    if args.command == "status":
        return status(settings, job_id=args.job_id)
    if args.command == "logs":
        return logs(settings, job_id=args.job_id, lines=args.lines)
    if args.command == "result":
        return result(settings, job_id=args.job_id)
    if args.command == "cancel":
        return cancel(settings, job_id=args.job_id)
    if args.command == "acknowledge":
        return acknowledge(settings, job_id=args.job_id)
    if args.command == "hook":
        if args.hook_command == "finalize":
            if not args.stdin_json:
                raise BridgeError("invalid_arguments", "`hook finalize` 需要 `--stdin-json`。")
            return hook_finalize(settings, stdin_text=sys.stdin.read())
    if args.command == "runner":
        return run_runner(settings, job_id=args.job_id, mode=args.mode)
    raise BridgeError("invalid_arguments", "未知命令。")
