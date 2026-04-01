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

If your OpenClaw installation supports skill installation from chat, send:

```text
Install the skill from `https://github.com/whoisjiahao/openclaw-claude-code`.
```

## First Run and Onboarding

On first activation, the skill asks for:

- default Agent Teams preference
- default log tail size
- maximum concurrent jobs
- default workspace root used for project discovery
- user timezone
- default notification channel and target

After onboarding succeeds, the user will see:

```text
🎉 OpenClaw Claude Code is ready.

I can now dispatch coding tasks to Claude Code asynchronously and report back when they finish.

Common actions:
- 💬 Tell me the coding task and I will dispatch it
- 📊 "How is the task going?" — check status
- 📋 "Show me the logs" — inspect recent output
- 📝 "What is the result?" — fetch the final outcome
- 🚫 "Cancel the task" — stop a running job
- 📃 "List the tasks" — show all jobs
```

## Examples

### Install through OpenClaw chat

```text
Install the skill from `https://github.com/whoisjiahao/openclaw-claude-code`.
```

### Dispatch summary

```text
🚀 **Task dispatched**

📋 Task: auth-refactor  
🔖 Job ID: `job_1774251330123_a1b2c3`  
📂 Directory: `my-project/src`  
🎯 Goal: Refactor the authentication middleware and add unit tests  
⏰ Started at: 2026-03-27T18:15:30+08:00

The task is running in the background and will be reported back to the current conversation when complete.  
Ask anytime if you want to check progress.
```

### Dispatch summary with Agent Teams and artifacts

```text
🚀 **Task dispatched**

📋 Task: generate-report
🔖 Job ID: `job_1774253000456_d4e5f6`
📂 Directory: `analytics-service`
🎯 Goal: Generate the monthly user growth analysis report
⏰ Started at: 2026-03-27T22:30:00+08:00

🤝 Agent Teams: enabled
👥 Collaboration mode: auto

📦 Artifacts: required and will be listed when the task completes

The task is running in the background and will be reported back to the current conversation when complete.
Ask anytime if you want to check progress.
```

### Status

```text
⚙️ Task `auth-refactor` is running.

It has been running for 3 minutes 42 seconds, and the most recent output was 15 seconds ago.

Do you want me to show the latest logs?
```

### Logs

```text
🔄 The task is still running. Recent activity:

- 🔧 Read → src/service.py  
- 💬 Analyzing service-layer dependencies...  
- 🔧 Bash → pytest tests/ -v  
- 💬 All tests passed. Preparing the final report.
```

### Successful completion

```text
✅ **Task completed**

📋 Task: auth-refactor
🔖 Job ID: `job_1774251330123_a1b2c3`

Refactored the authentication middleware by splitting `AuthMiddleware` into `TokenValidator` and `SessionManager`, and added 12 unit tests. Coverage increased from 43% to 91%.

⏱ Duration: 2 minutes 34 seconds · 25 turns
💰 $1.39 · 177,138 tokens
```

### Successful completion with artifacts

```text
✅ **Task completed**

📋 Task: generate-report
🔖 Job ID: `job_1774253000456_d4e5f6`

Generated the monthly user growth report covering registration trends, retention, and channel distribution.

📦 Artifacts:
- `analytics-service/output/growth-report.md`
- `analytics-service/output/charts.json`

⏱ Duration: 4 minutes 12 seconds · 38 turns
💰 $2.15 · 245,302 tokens
```

### Failure

```text
❌ **Task failed**

📋 Task: auth-refactor
🔖 Job ID: `job_1774251330123_a1b2c3`

The refactor surfaced a circular dependency that could not be resolved automatically: `AuthService` → `UserService` → `AuthService`. Decouple these modules first and retry.

⏱ Duration: 1 minute 8 seconds · 12 turns
💰 $0.52 · 68,421 tokens
```

### Permission denials

```text
⚠️ There were 2 permission denials during execution, which may affect completeness:
- 🔒 `Bash`: `rm -rf node_modules && npm install`
- 🔒 `Write`: `src/config/production.json`
```

### Cancellation

```text
🚫 **Task cancelled**

📋 Task: auth-refactor
🔖 Job ID: `job_1774251330123_a1b2c3`

If needed, I can still show the logs produced before cancellation.
```

### Max concurrent jobs reached

```text
⚠️ The maximum number of running tasks (2) has been reached, so I cannot accept a new one right now.

You can wait for a current task to finish, or cancel one and try again.
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
