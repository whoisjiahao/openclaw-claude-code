# OpenClaw Claude Code

[中文说明](./README.zh-CN.md)

OpenClaw Claude Code is an OpenClaw skill that dispatches coding tasks to local Claude Code as asynchronous jobs.

Instead of keeping a long coding session in the chat, OpenClaw can hand the work off to Claude Code, let it run in the background, and push the final result back to the user when the job is done.

## What It Does

- Dispatches coding tasks from chat to local Claude Code
- Runs jobs in `headless` or `tmux` mode
- Persists job state, logs, results, and artifacts on disk
- Supports cancellation, status checks, log inspection, and acknowledgements
- Uses a Claude stop hook as a reconciliation fallback
- Stores user onboarding preferences, including timezone
- Displays task timestamps in the configured user timezone

## Architecture

```text
User chat (Telegram / WhatsApp / Discord / Slack / ...)
    -> OpenClaw Gateway
    -> OpenClaw Claude Code skill
    -> Claude Code running locally
    -> OpenClaw Gateway notification delivery
    -> User chat
```

Core flow:

1. A user describes a coding task in chat.
2. OpenClaw submits the task to this skill.
3. The skill starts Claude Code in the background.
4. Runtime state is written under the OpenClaw data directory.
5. When the job finishes, OpenClaw fetches the full result and delivers a formatted reply.

## Requirements

- macOS or Linux
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) available as `claude`
- [OpenClaw](https://openclaw.ai) with Gateway running

## Installation

Clone the repository into the managed OpenClaw skills directory:

```bash
git clone git@github.com:<owner>/openclaw-claude-code.git ~/.openclaw/skills/openclaw-claude-code
cd ~/.openclaw/skills/openclaw-claude-code
uv sync
openclaw gateway restart
```

If you install skills through OpenClaw itself, you can also trigger installation from chat once this repository is available to your installation flow.

## First Run and Onboarding

On first activation, the skill asks for:

- default Agent Teams preference
- default log tail size
- maximum concurrent jobs
- default workspace root used for project discovery
- user timezone
- default notification channel and target

The timezone captured during onboarding becomes the default display timezone for later task timestamps such as:

- `created_at`
- `started_at`
- `updated_at`
- `completed_at`
- `acknowledged_at`
- `last_output_at`

Example dispatch summary using a user configured for `Asia/Shanghai`:

```text
🚀 Task dispatched

Task: auth-refactor
Job ID: job_1774251330123_a1b2c3
Directory: my-project/src
Goal: Refactor the authentication middleware and add unit tests
Started at: 2026-03-27T18:15:30+08:00

The task is running in the background and will be reported back to the current conversation when complete.
```

## Runtime Layout

By default, runtime data is written to:

```text
$OPENCLAW_HOME/data/openclaw-claude-code
```

or, if `OPENCLAW_HOME` is not set:

```text
~/.openclaw/data/openclaw-claude-code
```

Each job gets its own directory with:

- `job.json`
- `events.jsonl`
- `stdout.log`
- `stderr.log`
- `exit-code.txt`
- `result.json`
- `artifacts/` when artifacts are requested

## Hook Configuration

To ensure jobs can still be reconciled if the runner process exits unexpectedly, configure a Claude Code `Stop` hook:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /absolute/path/to/openclaw-claude-code python /absolute/path/to/scripts/bridge.py --runtime-root /abs/runtime hook finalize --stdin-json"
          }
        ]
      }
    ]
  }
}
```

Replace the project path and runtime path with your actual installation paths.

## Repository Layout

```text
openclaw-claude-code/
├── README.md
├── README.zh-CN.md
├── SKILL.md
├── references/
│   ├── protocol.md
│   ├── ux-feedback.md
│   └── error-codes.md
├── scripts/
│   ├── bridge.py
│   └── debug_run.py
├── src/openclaw_claude_code/
│   ├── cli.py
│   ├── errors.py
│   ├── models.py
│   ├── runner.py
│   ├── runtime.py
│   ├── service.py
│   └── timeutils.py
└── tests/
    └── test_openclaw_claude_code.py
```

## Documentation

- [SKILL.md](./SKILL.md): OpenClaw skill behavior and operator workflow
- [references/protocol.md](./references/protocol.md): CLI protocol, schemas, runtime layout
- [references/ux-feedback.md](./references/ux-feedback.md): user-facing reply templates
- [references/error-codes.md](./references/error-codes.md): error codes and recovery guidance

## Development

Install dependencies and run tests:

```bash
uv sync
.venv/bin/pytest -q
```

Optional syntax check:

```bash
python -m compileall src tests
```
