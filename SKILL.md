---
name: openclaw-claude-code
version: 0.1.0
description: "Activate when a request should be delegated to Claude Code for asynchronous work on a local repository: coding tasks (write/modify/refactor/debug/test/review), repository-level investigation or analysis (e.g., tracing scheduling logic and producing a report), any instruction that asks Claude Code/agent to go into a specific project path and execute work, or management of previously submitted async coding jobs."
metadata:
  {
    "openclaw": { "emoji": "💻", "requires": { "anyBins": ["claude", "uv"] } },
  }
---

# OpenClaw Claude Code

Use this skill to hand off programming work to Claude Code asynchronously rather than handling it inline in the current conversation.

Reference documents (must be read and followed):

- [references/protocol.md](references/protocol.md) — CLI syntax, schemas, runtime layout, hook configuration, environment overrides.
- [references/ux-feedback.md](references/ux-feedback.md) — **mandatory** message templates for all user-facing replies. Every dispatch summary, status update, log reply, result delivery, and error message MUST use the exact templates defined here.
- [references/error-codes.md](references/error-codes.md) — error code recovery guidance.

## Trigger conditions

### Programming tasks — dispatch a new job

Activate this skill whenever the user's request involves:

- Writing, modifying, refactoring, or optimizing code
- Fixing bugs, debugging, or investigating issues in code
- Adding, updating, or fixing tests
- Generating code-related documentation, comments, or changelogs
- Performing code reviews or static analysis
- Bootstrapping new projects, scaffolding, or initializing codebases
- Any work that requires reading from or writing to files in a local code repository
- An explicit request to have Claude Code run a task asynchronously

The deciding factor is whether the task requires hands-on programming against the local filesystem. The user does not need to mention "Claude Code" or "async" explicitly.

### Task management — operate on existing jobs

Also activate this skill when:

- The user asks whether a previously submitted job is still running.
- The user wants to view job logs or check on progress.
- The user asks for the final result of a completed job.
- The user wants to cancel a running job.
- OpenClaw needs to verify whether onboarding has been completed.

### When NOT to activate

- Pure knowledge questions that do not touch any code repository.
- Casual conversation unrelated to programming.
- Troubleshooting the OpenClaw Claude Code runtime itself — handle that directly; do not recursively dispatch.

## Core rules

**STRICT: All user-facing messages MUST use the exact templates defined in [references/ux-feedback.md](references/ux-feedback.md).**

- Every dispatch summary, status update, log reply, result delivery, failure report, and cancellation notice MUST follow the corresponding template in `ux-feedback.md`. Do NOT rephrase, restructure, add extra commentary, or omit required fields. The templates are the single source of truth for message formatting.
- Do NOT add greetings, pleasantries, opinions, or any text outside the template structure when delivering task results, status updates, or log replies.
- If a template includes conditional blocks (e.g. `{if artifacts_required}`), evaluate the condition and include/exclude the block accordingly. Do NOT invent new conditional blocks.

**Operational rules:**

- Always run `uv run python scripts/bridge.py config inspect` before assuming onboarding is done.
- Never read `config.json` directly; always go through the CLI.
- Do not call `acknowledge` until the final result, failure notice, or cancellation message has been delivered to the user.
- Default communication cadence is two-touch: one dispatch summary, one final outcome. Only invoke `status` or `logs` when the user explicitly asks.
- Determine `artifacts_required` before submitting. Do not leave it to Claude Code to guess whether files should be produced.
- When receiving a task completion notification from the runtime, fetch the result, format it per `ux-feedback.md`, and deliver exactly one message to the user. The raw notification must never be visible to the user.

## Workflow

### 0. Environment check

Before anything else, verify that the host environment has the required tooling:

```bash
uv run python scripts/bridge.py preflight
```

If `ok` is `false`, report the missing dependency to the user (typically Claude Code is not installed or not on `PATH`) and stop. Do not proceed to onboarding or job submission.

If `ok` is `true`, continue to the next step. The `checks` object also indicates whether `tmux` is available; mention it only if the user tries to use tmux mode.

### 1. Onboarding

Run:

```bash
uv run python scripts/bridge.py config inspect
```

If `onboarding_required` is `true`, gather the following preferences from the user:

- `default_agent_teams_enabled`
- `default_log_tail_lines`
- `max_concurrent_jobs`
- **Default workspace (`default_cwd`)**: Ask the user for a default working directory. This is the root directory where their code projects live (e.g. `~/workspace` or `~/projects`). It serves as the search scope when locating a specific project — scan subdirectories within this directory first before asking the user. Note: `--cwd` is always required when submitting; `default_cwd` is a search anchor, not a fallback.
- **Task completion notifications**: Ask where notifications should be sent. The default is the current chat channel. Collect the channel name (e.g. `telegram`, `discord`) and target ID. If the user accepts the default, use the channel and target of the current conversation.

Then persist them:

```bash
uv run python scripts/bridge.py config set \
  --default-agent-teams-enabled false \
  --default-log-tail-lines 4 \
  --max-concurrent-jobs 2 \
  --default-cwd /Users/username/workspace \
  --default-notify-channel telegram \
  --default-notify-target -5189558203 \
  --default-permission-mode bypassPermissions
```

The `--default-cwd`, `--default-notify-channel`, `--default-notify-target`, and `--default-permission-mode` flags are optional during `config set`. `--cwd` is always required when submitting a job — `default_cwd` serves only as a search scope for project inference. Notifications are disabled if channel/target are omitted. Permission mode defaults to `bypassPermissions` (required for headless `-p` mode).

If `config set` succeeds during onboarding, reply using the **"Onboarding"** welcome template from `ux-feedback.md`, then proceed to handle the original request.

### 2. Submitting a new job

Before calling `submit`:

1. Decide whether the task is file-delivery oriented.
2. Set `artifacts_required` only when the task must produce file outputs.
3. Determine `cwd` (the directory where Claude Code will run).

   **Important: NEVER submit a task without the user explicitly confirming the target directory.**

   Resolution flow:

   a. **Infer a candidate directory** from the user's message:
      - If `default_cwd` is configured (check via `config inspect`), use it as the search scope. Scan subdirectories within `default_cwd` for matching project/repo names based on keywords in the user's message.
      - If `default_cwd` is not configured, infer from explicit paths, GitHub URLs, or contextual clues in the message.
      - If no candidate can be inferred, ask the user: "请问要在哪个目录下启动 Claude Code？"

   b. **When the user provides a directory** (either from step a or in response to your question):
      - Determine whether the path is **relative or absolute**. If it looks like a relative path (no leading `/`), resolve it against `default_cwd` to produce an absolute path. If `default_cwd` is not configured and the path is relative, ask the user for the full absolute path.
      - Verify the resolved absolute path points to an **existing directory**.
      - If the directory does not exist, inform the user and ask again.

   c. **Confirm with the user before proceeding** — always ask: "我将在 `{resolved_absolute_path}` 启动 Claude Code 执行任务，可以吗？" Do NOT submit until the user confirms.

   CLI notes:
   - `--cwd` is required. OpenClaw must always pass the confirmed absolute path explicitly.
   - `default_cwd` is a search anchor only — it is NOT a fallback for `--cwd`.
4. Collect the remaining parameters:
   - `prompt`
   - optional `task_name`
   - optional Agent Teams preference
   - optional `teammate_mode`

Submit:

```bash
uv run python scripts/bridge.py submit \
  --prompt "..." \
  --cwd /abs/path \
  --task-name optional-name
```

Optional flags:

- `--run-mode tmux`
- `--agent-teams` / `--no-agent-teams`
- `--teammate-mode auto`
- `--artifacts-required`
- `--permission-mode <mode>` — override the Claude Code permission mode for this job. Valid values: `acceptEdits`, `bypassPermissions`, `default`, `dontAsk`, `plan`, `auto`. Omit to use the configured default (`bypassPermissions`).
- `--notify-channel <channel>` / `--notify-target <target>` — **if `config inspect` shows `default_notify_channel` and `default_notify_target` are set, you MUST pass them explicitly.** The runtime does not read config at notification time; it only uses the values stored on the job record. Omitting these flags when config has values will result in no notification being sent.

On success, reply to the user using the **"Dispatch summary"** template from `ux-feedback.md`. Use only the fields returned by the CLI; do not add commentary or fabricate fields.

After a job completes, fails, or is cancelled, the runtime automatically sends a notification via `openclaw agent --deliver`. The agent receives the instruction, fetches the result, formats it per `references/ux-feedback.md`, and delivers the formatted reply to the configured channel. This is best-effort; if the `openclaw` CLI is not available or the notification target is not set, the job completes normally without notification.

### 3. Checking status

When the user simply asks whether a task is still running:

```bash
uv run python scripts/bridge.py status --job-id <job_id>
```

Reply using the **"Status replies"** template from `ux-feedback.md`.

### 4. Viewing logs

When the user asks for logs or wants to see what is happening:

1. Call `status` first.
2. If the job has already reached a terminal state, mention that before showing logs.
3. Then call:

```bash
uv run python scripts/bridge.py logs --job-id <job_id>
```

Pass `--lines N` only when the user explicitly requests a different window size.

Reply using the **"Log replies"** template from `ux-feedback.md`. Present the `activities` array as a bulleted list — do not add extra interpretation or commentary.

### 5. Retrieving the final result

When the user asks for the outcome:

```bash
uv run python scripts/bridge.py result --job-id <job_id>
```

Format the result using the **"Task completion"** templates from `ux-feedback.md`. Choose the correct template (success / success with artifacts / failure) based on `exit_code` and `artifacts`. Do NOT add text outside the template.

Once the result, failure explanation, or cancellation notice has been successfully delivered to the user, mark the job as consumed:

```bash
uv run python scripts/bridge.py acknowledge --job-id <job_id>
```

### 6. Cancelling a job

When the user asks to stop a running task:

```bash
uv run python scripts/bridge.py cancel --job-id <job_id>
```

Reply using the **"Cancellation replies"** template from `ux-feedback.md`. If further details are needed, `status` or `result` may still be queried.

## Artifacts decision

Enable `--artifacts-required` only when the task should produce file outputs, for example:

- Generated reports
- Markdown, JSON, or CSV exports
- Patch or diff files
- Outputs intended for downstream machine consumption

Do **not** enable artifacts for:

- General Q&A
- Status or log checks
- Ordinary code-editing tasks whose deliverable is a text summary

## Agent Teams

Agent Teams support is limited to parameter passthrough.

- Surface whether Agent Teams is enabled for the current job.
- Surface the selected `teammate_mode`.
- Do not attempt to model sub-agent lifecycle or progress.

## Hook setup

The runner process is responsible for finalizing each job after the Claude Code process exits. Claude Code's stop hook serves as an idempotent fallback in case the runner is interrupted.

To configure the fallback hook, add the following to `~/.claude/settings.json`:

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

Replace `/absolute/path/to/openclaw-claude-code`, `/absolute/path/to/scripts/bridge.py`, and `/abs/runtime` with the actual paths on the target host. The `--project` flag tells uv where to find the project's virtual environment.

`submit` injects `OPENCLAW_CLAUDE_CODE_JOB_ID` and `OPENCLAW_CLAUDE_CODE_RUNTIME_ROOT` into the Claude process environment. Both the runner and `hook finalize` rely on these variables to locate the correct job.

To verify everything works end-to-end, submit a test job and confirm that `result.json` appears in the job directory once the task completes.
