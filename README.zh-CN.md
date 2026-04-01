# OpenClaw Claude Code

[English](./README.md)

OpenClaw Claude Code 是一个 OpenClaw skill，用来把聊天中的编程任务异步派发给本地 Claude Code 执行。

它的目标不是在当前对话里长时间同步写代码，而是把任务提交为后台作业，持久化运行状态，并在任务完成后把完整结果再推回用户对话。

## 能力概览

- 将聊天中的编程任务派发给本地 Claude Code
- 支持 `headless` 和 `tmux` 两种运行模式
- 持久化 job 状态、日志、结果和交付物
- 支持取消任务、查看状态、查看日志、获取结果、acknowledge
- 使用 Claude `Stop` hook 作为异常收尾补偿
- 支持 onboarding 用户配置，包括时区
- 所有时间展示默认使用用户配置的时区

## 架构

```text
用户聊天渠道（Telegram / WhatsApp / Discord / Slack / ...）
    -> OpenClaw Gateway
    -> OpenClaw Claude Code skill
    -> 本地 Claude Code
    -> OpenClaw Gateway 通知投递
    -> 用户聊天渠道
```

核心流程：

1. 用户在聊天里描述编程任务。
2. OpenClaw 把任务提交给本 skill。
3. Skill 在后台启动 Claude Code。
4. 运行状态写入 OpenClaw 数据目录。
5. 任务完成后，OpenClaw 获取完整结果并投递格式化回复。

## 依赖要求

- macOS 或 Linux
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- 已安装并可执行的 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) `claude`
- 已运行 Gateway 的 [OpenClaw](https://openclaw.ai)

## 安装

将仓库克隆到 OpenClaw 管理的 skills 目录：

```bash
git clone git@github.com:<owner>/openclaw-claude-code.git ~/.openclaw/skills/openclaw-claude-code
cd ~/.openclaw/skills/openclaw-claude-code
uv sync
openclaw gateway restart
```

如果你的环境支持通过 OpenClaw 自身安装 skill，也可以在后续接入安装流程后通过聊天触发安装。

## 首次使用与 Onboarding

首次激活时，skill 会收集以下偏好：

- 默认是否开启 Agent Teams
- 默认日志尾部行数
- 最大并发任务数
- 默认工作区根目录，用于项目发现
- 用户时区
- 默认通知渠道和目标

onboarding 中配置的时区会成为后续所有任务时间展示的默认时区，例如：

- `created_at`
- `started_at`
- `updated_at`
- `completed_at`
- `acknowledged_at`
- `last_output_at`

下面是一个用户时区为 `Asia/Shanghai` 的任务派发示例：

```text
🚀 任务已派发

任务：auth-refactor
编号：job_1774251330123_a1b2c3
目录：my-project/src
目标：重构认证中间件并补齐单元测试
开始时间：2026-03-27T18:15:30+08:00

任务会在后台运行，完成后自动回报到当前对话。
```

## 运行时目录

默认情况下，运行数据会写到：

```text
$OPENCLAW_HOME/data/openclaw-claude-code
```

如果没有设置 `OPENCLAW_HOME`，则写到：

```text
~/.openclaw/data/openclaw-claude-code
```

每个 job 都有独立目录，包含：

- `job.json`
- `events.jsonl`
- `stdout.log`
- `stderr.log`
- `exit-code.txt`
- `result.json`
- 当要求交付物时创建 `artifacts/`

## Hook 配置

为了避免 runner 异常退出后任务无法正常收尾，需要配置 Claude Code 的 `Stop` hook：

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

请把项目路径和运行目录替换成你的真实安装路径。

## 仓库结构

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

## 参考文档

- [SKILL.md](./SKILL.md)：OpenClaw skill 行为与操作规范
- [references/protocol.md](./references/protocol.md)：CLI 协议、schema、运行时布局
- [references/ux-feedback.md](./references/ux-feedback.md)：用户回复模板
- [references/error-codes.md](./references/error-codes.md)：错误码与恢复指引

## 开发

安装依赖并运行测试：

```bash
uv sync
.venv/bin/pytest -q
```

可选语法检查：

```bash
python -m compileall src tests
```
