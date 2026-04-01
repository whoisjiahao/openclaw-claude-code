# Protocol Reference

## Scope

This file is the compact protocol reference for the implemented openclaw-claude-code runtime.
Use it when you need command syntax, runtime layout, schemas, hook behavior, or environment overrides.

## Public CLI

### Preflight

```bash
uv run python scripts/bridge.py preflight
```

Returns `{ "ok": bool, "runtime_root": str, "checks": { ... } }`.

`ok` is `true` when all hard requirements are met: `uv` found, `python_version` >= 3.11, `venv_ready`, and `claude_bin` found. `tmux_bin` and `openclaw_bin` are advisory.

### Config

```bash
uv run python scripts/bridge.py config inspect
uv run python scripts/bridge.py config set \
  --default-agent-teams-enabled true|false \
  --default-log-tail-lines <int>=1 \
  --max-concurrent-jobs <int>=1 \
  [--default-cwd /abs/path] \
  [--default-notify-channel <channel>] \
  [--default-notify-target <target>] \
  [--default-permission-mode <mode>]
```

### Jobs

```bash
uv run python scripts/bridge.py submit \
  --prompt "..." \
  --cwd /abs/path \
  [--task-name name] \
  [--run-mode headless|tmux] \
  [--agent-teams|--no-agent-teams] \
  [--teammate-mode auto|in-process|tmux] \
  [--artifacts-required] \
  [--permission-mode <mode>] \
  [--notify-channel <channel>] \
  [--notify-target <target>]

The underlying Claude Code process is launched with `--output-format stream-json`, producing JSONL output in `stdout.log` for real-time streaming and structured result extraction.

uv run python scripts/bridge.py list [--status accepted|running|completed|failed|cancelled|acknowledged]

uv run python scripts/bridge.py status --job-id <job_id>
uv run python scripts/bridge.py logs --job-id <job_id> [--lines <int>]
```

`logs` returns human-readable activity summaries parsed from stream-json events (default `lines` is `default_log_tail_lines` from config):

```json
{
  "job_id": "job_1774251330123_a1b2c3",
  "lines": 4,
  "activities": [
    "🔧 Read → src/main.py",
    "💬 正在分析代码结构…",
    "🔧 Write → output.md",
    "✅ 完成（12 轮，$0.3200）"
  ],
  "stderr": ""
}
```

```bash
uv run python scripts/bridge.py result --job-id <job_id>
uv run python scripts/bridge.py cancel --job-id <job_id>
uv run python scripts/bridge.py acknowledge --job-id <job_id>
uv run python scripts/bridge.py hook finalize --stdin-json
```

Every command also accepts:

```bash
--runtime-root /abs/path
```

## Runtime root resolution

Resolution order:

1. CLI `--runtime-root`
2. Environment variable `OPENCLAW_CLAUDE_CODE_RUNTIME_ROOT`
3. `$OPENCLAW_HOME/data/openclaw-claude-code`
4. `$HOME/.openclaw/data/openclaw-claude-code`

## Runtime layout

```text
<runtime_root>/
  config.json
  submit.lock
  jobs/
    <job_id>/
      job.json
      events.jsonl
      stdout.log          # stream-json JSONL (one JSON event per line)
      stderr.log
      exit-code.txt
      result.json
      state.lock
      hook-finalize/
      artifacts/        # only when artifacts are required
      tmux.sock         # only when run-mode = tmux
```

## Stable schemas

### `config.json`

```json
{
  "onboarding_completed": true,
  "default_agent_teams_enabled": false,
  "default_log_tail_lines": 4,
  "max_concurrent_jobs": 2,
  "default_cwd": "/Users/username",
  "default_notify_channel": "telegram",
  "default_notify_target": "-5189558203",
  "default_permission_mode": "bypassPermissions"
}
```

### `job.json`

```json
{
  "job_id": "job_1774251330123_a1b2c3",
  "task_name": "job_1774251330123_a1b2c3",
  "prompt": "重构认证中间件并补齐测试",
  "cwd": "/abs/path",
  "status": "running",
  "agent_teams_enabled": false,
  "teammate_mode": null,
  "artifacts_required": false,
  "permission_mode": "bypassPermissions",
  "process_pid": 43210,
  "tmux_socket_path": null,
  "tmux_session_name": null,
  "created_at": "2026-03-23T10:15:30Z",
  "started_at": "2026-03-23T10:15:32Z",
  "completed_at": null,
  "acknowledged_at": null,
  "updated_at": "2026-03-23T10:15:32Z",
  "notify_channel": "telegram",
  "notify_target": "-5189558203"
}
```

Notes:

- `process_pid` is the cancellable headless runner process-group handle.
- `tmux_socket_path` and `tmux_session_name` are present only for tmux jobs.
- The runtime does not persist an explicit `run_mode` field; it is inferred from the handles.

### `events.jsonl`

Each line is a JSON object:

```json
{"ts":"2026-03-23T10:15:30Z","status":"accepted","by":"submit"}
```

`by` is one of:

- `submit`
- `runner`
- `hook_finalize`
- `cancel`
- `acknowledge`

### `result.json`

```json
{
  "job_id": "job_1774251330123_a1b2c3",
  "task_name": "auth-refactor",
  "outcome": "completed",
  "message": "已完成认证中间件重构并补齐测试。",
  "exit_code": 0,
  "completed_at": "2026-03-23T10:20:01Z",
  "artifacts": ["/abs/path/to/artifacts/report.md"],
  "cost_usd": 1.39,
  "duration_seconds": 154,
  "num_turns": 25,
  "input_tokens": 172369,
  "output_tokens": 4769,
  "model": "claude-sonnet-4-20250514",
  "permission_denials": null
}
```

`outcome` is one of: `completed`, `failed`, `cancelled`.

Optional fields (all nullable, extracted from stream-json result event):

- `cost_usd` — total API cost in USD
- `duration_seconds` — total execution time
- `num_turns` — number of interaction turns
- `input_tokens` / `output_tokens` — token consumption
- `model` — primary model used
- `permission_denials` — array of `{"tool": "...", "input": {...}}` or `null`

## State machine

Allowed states:

- `accepted`
- `running`
- `completed`
- `failed`
- `cancelled`
- `acknowledged`

Allowed transitions:

- `accepted -> running|failed|cancelled`
- `running -> completed|failed|cancelled`
- `completed|failed|cancelled -> acknowledged`

Terminal result availability:

- `result` is allowed only after `completed`, `failed`, `cancelled`, or `acknowledged`.

## Job ID rule

The runtime generates:

```text
job_<unix_ms>_<rand6hex>
```

Implementation uses UTC milliseconds plus `secrets.token_hex(3)`.

## Hook finalize contract

### Required environment

- `OPENCLAW_CLAUDE_CODE_JOB_ID`
- `OPENCLAW_CLAUDE_CODE_RUNTIME_ROOT`

### Required stdin JSON fields

```json
{
  "session_id": "sess_123",
  "cwd": "/abs/path",
  "hook_event_name": "Stop"
}
```

### Idempotency

Idempotency key:

```text
<session_id>:<hook_event_name>
```

Marker path:

```text
<job_dir>/hook-finalize/<session_id>__<hook_event_name>
```

### Final result derivation

Data sources:

- `job.json`
- `stdout.log`
- `stderr.log`
- `exit-code.txt`

Rules:

- Missing `exit-code.txt` during runner finalize -> `failed`, `exit_code = null`, message `任务收尾失败：runner 未写入 exit-code.txt。`
- `hook finalize` first tries reconciliation: use `exit-code.txt` if it appears, otherwise fail only after runner exits or the grace period expires.
- Exit code `0` -> `completed`, message = parsed from `stdout.log` (stream-json JSONL): first try `result` event's `result` field, then last `assistant` text, then fallback to last 4000 UTF-8 chars. Final fallback `任务已完成，但未捕获到最终文本输出。`
- Non-zero exit code -> `failed`, message = same stream-json extraction, then `stderr.log` last 4000 chars, then `stdout.log` last 4000 chars, fallback `任务执行失败，且未捕获到错误输出。`

## Environment overrides

Supported runtime overrides:

- `OPENCLAW_CLAUDE_CODE_RUNTIME_ROOT`
- `OPENCLAW_CLAUDE_CODE_CLAUDE_BIN`
- `OPENCLAW_CLAUDE_CODE_TMUX_BIN`
- `OPENCLAW_CLAUDE_CODE_CLAUDE_EXTRA_ARGS`
- `OPENCLAW_CLAUDE_CODE_PYTHON_BIN`
- `OPENCLAW_CLAUDE_CODE_OPENCLAW_BIN`

Agent/runtime injection during job execution:

- `OPENCLAW_CLAUDE_CODE_JOB_ID`
- `OPENCLAW_CLAUDE_CODE_RUNTIME_ROOT`
- `OPENCLAW_CLAUDE_CODE_ARTIFACTS_DIR` when artifacts are enabled
- `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` when Agent Teams are enabled

## Task completion notification

After `finalize_job()` writes a terminal state, the runtime attempts to send a notification via OpenClaw CLI. This is best-effort and never affects job status.

### Prerequisites

- `job.notify_channel` and `job.notify_target` are both non-null
- `settings.openclaw_bin` is available (resolved from `OPENCLAW_CLAUDE_CODE_OPENCLAW_BIN` or `shutil.which("openclaw")`)

### Command

```bash
<openclaw_bin> agent --message <text> --deliver --channel <notify_channel> --to|--reply-to <notify_target>
```

The instruction is processed by the agent internally. Only the agent's formatted reply is delivered to the user.

### Instruction format

```
[openclaw-claude-code] task {completed|failed|cancelled}
job_id: {job_id}
task_name: {task_name}

Please run `uv run python scripts/bridge.py result --job-id {job_id}` to fetch the full result, then present it to the user following the template in references/ux-feedback.md.
```

This text is sent as the `--message` argument to `openclaw agent`. The agent processes it internally (runs the `result` command, formats per ux-feedback.md), and only the agent's reply is delivered to the user via `--deliver`. The raw instruction is never visible to the user.

### Behavior

- Notification fires only on the actual finalization path (not the idempotent early return)
- 120-second timeout on the subprocess call (agent needs time to process and format)
- Any failure is silently ignored (job status and return value are unaffected)
- Notification targets are resolved at submit time: explicit CLI args override config defaults
