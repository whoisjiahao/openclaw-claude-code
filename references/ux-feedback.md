# UX Feedback Reference

## Core interaction model

Default user experience is two-touch:

1. Send one dispatch summary after `submit` succeeds.
2. Send one final outcome after task completion notification arrives.

Use `status` and `logs` only when the user explicitly asks about progress.

Task completion notifications are pushed automatically by the runtime via `openclaw agent --deliver`. When the notification arrives, OpenClaw should immediately fetch the full result via `result` command, format it, and present it to the user — the user should receive one complete message, not a brief alert followed by a separate result.

## Onboarding

Ask for the following preferences:

- whether Agent Teams should be enabled by default
- default number of log lines to read
- maximum concurrent jobs
- **where to send task completion notifications** (default: this chat)

Suggested welcome reply after onboarding succeeds:

> 🎉 OpenClaw Claude Code 已完成初始化。
>
> 后续我可以把编程任务异步派发给 Claude Code 执行。任务完成后会自动通知你。
>
> 常用操作：
> - 💬 直接告诉我编程任务，我会自动派发执行
> - 📊 "任务进度怎样了" — 查看运行状态
> - 📋 "看下日志" — 查看实时输出
> - 📝 "结果呢" — 获取完成后的详细结果
> - 🚫 "取消任务" — 停止正在运行的任务
> - 📃 "有哪些任务" — 列出所有任务

## Dispatch summary

Build the summary from the `submit` response. Use this template:

> 🚀 **任务已派发**
>
> 📋 任务：{task_name}
> 🔖 编号：`{job_id}`
> 📂 目录：`{cwd}`
> 🎯 目标：{one-line task summary in natural language}
> ⏰ 开始时间：{created_at}
>
> {if agent_teams_enabled}
> 🤝 Agent Teams：已开启
> 👥 协作模式：{teammate_mode}
> {/if}
>
> {if artifacts_required}
> 📦 交付物：已要求，完成后会列出文件路径
> {/if}
>
> {if notify_channel && notify_target}
> 任务在后台运行中，完成后会自动播报到 {notify_channel_display}。
> {else}
> 任务在后台运行中，完成后会自动通知你。
> {/if}

Note: `notify_target` is typically a numeric ID (e.g. `-5189558203`). Do NOT show the raw ID to the user. Resolve it to the chat or group name that you are aware of. For example, display "Telegram · 我的工作群" instead of "telegram（-5189558203）". If you cannot resolve the name, use a generic description like "当前对话".
> 如需查看进度，随时告诉我。

Fields to include from `submit` response:

- `job_id`
- `task_name`
- `cwd`
- `agent_teams_enabled` / `teammate_mode` (only mention if enabled)
- `artifacts_required` (only mention if true)
- `created_at`

Do not fabricate additional fields. Derive the one-line task summary from the original user request, not from the prompt field.

## Status replies

Map `status` responses into short updates:

- `accepted`: "⏳ 任务已创建，正在准备启动。"
- `running`: "⚙️ 任务正在运行中。"
- `completed`: "✅ 任务已完成。"
- `failed`: "❌ 任务已失败。"
- `cancelled`: "🚫 任务已取消。"
- `acknowledged`: "📬 任务结果已处理完成。"

When present, surface:

- `started_at` — how long the task has been running
- `updated_at` — when the last state change occurred
- `last_output_at` — when the last output was written

Example:

> ⚙️ 任务 `auth-refactor` 正在运行中。
>
> 已运行 3 分 42 秒，最近一次输出在 15 秒前。
>
> 需要我帮你看一下最新的日志吗？

Always end a running-state status reply with a follow-up offer to view logs.

## Log replies

The `logs` command returns an `activities` array — human-readable summaries of recent Claude Code events (tool calls, assistant responses, completion status). Default `lines` is 4.

Suggested reply structure:

- one line explaining whether the task is still running
- list the activities from the `activities` array as a bulleted list — these are already human-readable, present them directly
- if `stderr` is non-empty, mention it briefly

Example:

> 🔄 任务仍在运行中，最近动态：
>
> - 🔧 Read → src/service.py
> - 💬 正在分析服务层的依赖关系…
> - 🔧 Bash → pytest tests/ -v
> - 💬 测试全部通过，开始编写报告

If the task is already terminal, say that first before showing activities.

## Task completion — notification and result delivery

When a job finishes, the runtime automatically sends a notification to OpenClaw via `openclaw agent --deliver`. The notification contains the `job_id` and instructs OpenClaw to fetch the full result.

**OpenClaw's responsibility upon receiving the notification:**

1. Call `uv run python scripts/bridge.py result --job-id {job_id}` to get the full result
2. Format the result using the template below
3. Send the formatted message to the user
4. Call `uv run python scripts/bridge.py acknowledge --job-id {job_id}` after delivery

The user should only receive **one message** — the fully formatted result. They should NOT see the raw notification from the runtime.

### Extracting the conclusion from `result.message`

`result.message` contains Claude Code's final answer text, extracted from the `result` field of the stream-json `result` event.

This text may contain markdown formatting, code blocks, or verbose explanations. Follow these steps:

1. **Read the message**: `result.message` is Claude Code's concluding response. It typically summarizes what was done, what was changed, or what the result is.
2. **Decide if formatting is needed**: if the message is already clean and readable, present it as-is. If it contains markdown, code blocks, or bullet lists, reformat lightly for the target chat platform. Do NOT rewrite or summarize — preserve Claude Code's original wording.

### Success without artifacts

> ✅ **任务完成**
>
> 📋 任务：{task_name}
> 🔖 编号：`{job_id}`
>
> {extracted conclusion from result.message}
>
> ⏱ 耗时 {duration} · {num_turns} 轮交互
> 💰 ${cost_usd} · {input_tokens + output_tokens} tokens

### Success with artifacts

> ✅ **任务完成**
>
> 📋 任务：{task_name}
> 🔖 编号：`{job_id}`
>
> {extracted conclusion from result.message}
>
> 📦 交付物：
> - `{artifact_absolute_path_1}`
> - `{artifact_absolute_path_2}`
>
> ⏱ 耗时 {duration} · {num_turns} 轮交互
> 💰 ${cost_usd} · {input_tokens + output_tokens} tokens

### Failure

> ❌ **任务失败**
>
> 📋 任务：{task_name}
> 🔖 编号：`{job_id}`
>
> {extracted conclusion or error description from result.message}
>
> {if artifacts present, still list them}
>
> ⏱ 耗时 {duration} · {num_turns} 轮交互
> 💰 ${cost_usd} · {input_tokens + output_tokens} tokens

### Permission denials

If `permission_denials` is non-empty, append a warning block after the stats line:

> ⚠️ 执行过程中有 {len(permission_denials)} 次权限被拒绝，可能影响任务完整性：
> - 🔒 `{tool_name}`: {tool_input 的简要描述，如命令内容或文件路径}
> - 🔒 `{tool_name}`: {tool_input 的简要描述}

Each denial entry in `result.permission_denials` has `tool` (tool name) and `input` (tool input dict). Extract the most relevant field from `input` to describe what was attempted (e.g., `command` for Bash, `file_path` for Read/Write).

### Stats line formatting rules

- `duration_seconds`: convert to human-readable format (e.g., 154 → "2 分 34 秒", 45 → "45 秒")
- `cost_usd`: display as `$x.xx` (2 decimal places)
- tokens: show as `{input_tokens + output_tokens}` total, e.g., "177,138 tokens"
- If any stat field is `null`, omit that segment from the line rather than showing "null"

### Cancellation (via notification)

> 🚫 **任务已取消**
>
> 📋 任务：{task_name}
> 🔖 编号：`{job_id}`
>
> 如果需要了解取消前的执行情况，我可以帮你查看日志。

## Cancellation replies (user-initiated)

After a successful `cancel`:

> 🚫 **任务已取消**
>
> 📋 任务：{task_name}
> 🔖 编号：`{job_id}`
>
> 如果需要了解取消前的执行情况，我可以帮你查看日志。

If you then deliver the final cancellation outcome from `result`, you may call `acknowledge`.

## Max concurrent jobs reached

When `submit` returns `error_code = max_concurrent_jobs_reached`:

> ⚠️ 当前正在运行的任务已达到上限（{max_concurrent_jobs} 个），暂时无法接收新任务。
>
> 你可以等待当前任务完成，或取消某个任务后再提交。

## General guidelines

- Use emoji sparingly but consistently as visual anchors.
- Always include `task_name` and `job_id` so the user can correlate messages with tasks.
- Keep messages concise. The user can always ask for details (logs, full result).
- Never dump raw JSON or stdout/stderr into user-facing messages. The `logs` command already returns human-readable `activities`; present them directly.
- For `result.message`, if the content is very long, truncate and offer to show the full version.
- Each field should be on its own line for readability. Do not cram multiple fields on one line.

### Path display rules

Absolute paths stored in `result.json` (artifacts, cwd, etc.) are often too long for mobile display. When presenting paths to the user, apply context-aware shortening:

1. **Project paths** (paths under `default_cwd`): Strip the `default_cwd` prefix and show the relative portion. Example: `/Users/me/workspace/my-project/src/main.py` → `my-project/src/main.py`.
2. **Artifacts** (paths under RUNTIME_ROOT): Replace HOME with `~` and collapse the fixed middle segments (`data/openclaw-claude-code/`) to `...`. Keep `job_id`, `artifacts/`, and filename visible. Example: `/Users/me/.openclaw/data/openclaw-claude-code/jobs/job_xxx/artifacts/report.md` → `~/.openclaw/data/.../job_xxx/artifacts/report.md`.
3. **Other paths under HOME**: Replace the HOME prefix with `~`. Example: `/Users/me/Downloads/data.csv` → `~/Downloads/data.csv`.
4. **All other paths**: Show the full absolute path.

The original absolute paths in `result.json` must remain unchanged — shortening is purely a display concern.
