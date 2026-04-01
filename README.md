# OpenClaw Claude Code

OpenClaw Claude Code 是一个 [OpenClaw](https://openclaw.ai) skill，让你通过任意聊天渠道（Telegram、WhatsApp、Discord、Slack 等）向 Claude Code 异步派发编程任务。

你在手机上跟 AI 说一句"帮我重构认证模块"，它就在后台启动 Claude Code 执行，完成后自动把结果推送回你的聊天窗口。

## 工作原理

```
用户 (Telegram/WhatsApp/...) → OpenClaw Gateway → OpenClaw Claude Code Skill → Claude Code (本地)
                                                                              ↓
用户 ← 自动通知 ← OpenClaw Gateway ← 任务完成 ←────────────────────────────────┘
```

核心流程：

1. 用户在聊天中描述编程任务
2. OpenClaw 通过此 skill 将任务提交给本地 Claude Code
3. Claude Code 在后台异步执行（支持 headless 和 tmux 两种模式）
4. 任务完成/失败/取消后自动推送通知到指定渠道

## 前置要求

- macOS / Linux
- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) — Python 包管理器
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude` CLI) — 已安装并可用
- [OpenClaw](https://openclaw.ai) — 已安装并运行 Gateway

## 安装

### 方式一：手动安装

将此仓库克隆到 OpenClaw 的 managed skills 目录：

```bash
git clone https://github.com/CNZSMJ/openclaw-claude-code.git ~/.openclaw/skills/openclaw-claude-code
```

安装 Python 依赖：

```bash
cd ~/.openclaw/skills/openclaw-claude-code
uv sync
```

重启 OpenClaw Gateway 使 skill 生效：

```bash
openclaw gateway restart
```

### 方式二：通过 OpenClaw 安装

如果 skill 已发布到 ClawHub：

```bash
clawhub install openclaw-claude-code
```

或者直接在聊天中告诉 OpenClaw：

> 安装 openclaw-claude-code 这个 skill

OpenClaw 会自动完成安装和配置。

## 首次使用

首次激活时，skill 会引导你完成 onboarding，配置以下偏好：

- 是否默认开启 Agent Teams
- 日志显示行数
- 最大并发任务数
- 默认工作目录（项目搜索范围）
- 任务完成通知渠道和目标

配置完成后你会看到：

> 🎉 OpenClaw Claude Code 已完成初始化。
>
> 后续我可以把编程任务异步派发给 Claude Code 执行。任务完成后会自动通知你。
>
> 常用操作：  
> \- 💬 直接告诉我编程任务，我会自动派发执行  
> \- 📊 "任务进度怎样了" — 查看运行状态  
> \- 📋 "看下日志" — 查看实时输出  
> \- 📝 "结果呢" — 获取完成后的详细结果  
> \- 🚫 "取消任务" — 停止正在运行的任务  
> \- 📃 "有哪些任务" — 列出所有任务

## 消息排版示例

以下是各场景中用户实际收到的消息格式。

### 任务派发

用户说"帮我重构 auth 模块的中间件，补上单元测试"后：

> 🚀 **任务已派发**
>
> 📋 任务：auth-refactor  
> 🔖 编号：`job_1774251330123_a1b2c3`  
> 📂 目录：`my-project/src`  
> 🎯 目标：重构认证中间件并补齐单元测试  
> ⏰ 开始时间：2026-03-27T10:15:30Z
>
> 任务在后台运行中，完成后会自动播报到 Telegram · 404战报中心。  
> 如需查看进度，随时告诉我。

如果开启了 Agent Teams 和交付物：

> 🚀 **任务已派发**
>
> 📋 任务：generate-report  
> 🔖 编号：`job_1774253000456_d4e5f6`  
> 📂 目录：`analytics-service`  
> 🎯 目标：生成本月用户增长分析报告  
> ⏰ 开始时间：2026-03-27T14:30:00Z
>
> 🤝 Agent Teams：已开启  
> 👥 协作模式：auto
>
> 📦 交付物：已要求，完成后会列出文件路径
>
> 任务在后台运行中，完成后会自动播报到 Telegram · 404战报中心。  
> 如需查看进度，随时告诉我。

### 查看状态

用户问"任务跑得怎样了"：

> ⚙️ 任务 `auth-refactor` 正在运行中。
>
> 已运行 3 分 42 秒，最近一次输出在 15 秒前。
>
> 需要我帮你看一下最新的日志吗？

### 查看日志

用户说"看下日志"：

> 🔄 任务仍在运行中，最近动态：
>
> \- 🔧 Read → src/service.py  
> \- 💬 正在分析服务层的依赖关系…  
> \- 🔧 Bash → pytest tests/ -v  
> \- 💬 测试全部通过，开始编写报告

### 任务完成

任务成功完成后自动推送：

> ✅ **任务完成**
>
> 📋 任务：auth-refactor  
> 🔖 编号：`job_1774251330123_a1b2c3`
>
> 已完成认证中间件重构：将 `AuthMiddleware` 拆分为 `TokenValidator` 和 `SessionManager` 两个独立组件，新增 12 个单元测试，覆盖率从 43% 提升到 91%。
>
> ⏱ 耗时 2 分 34 秒 · 25 轮交互  
> 💰 $1.39 · 177,138 tokens

带交付物的完成：

> ✅ **任务完成**
>
> 📋 任务：generate-report  
> 🔖 编号：`job_1774253000456_d4e5f6`
>
> 已生成用户增长分析报告，包含注册趋势、留存率和渠道分布三个维度的数据。
>
> 📦 交付物：  
> \- `analytics-service/output/growth-report.md`  
> \- `analytics-service/output/charts.json`
>
> ⏱ 耗时 4 分 12 秒 · 38 轮交互  
> 💰 $2.15 · 245,302 tokens

### 任务失败

> ❌ **任务失败**
>
> 📋 任务：auth-refactor  
> 🔖 编号：`job_1774251330123_a1b2c3`
>
> 重构过程中发现循环依赖无法自动解决：`AuthService` → `UserService` → `AuthService`。建议先手动解耦这两个模块的依赖关系。
>
> ⏱ 耗时 1 分 8 秒 · 12 轮交互  
> 💰 $0.52 · 68,421 tokens

如果执行过程中有权限被拒绝：

> ⚠️ 执行过程中有 2 次权限被拒绝，可能影响任务完整性：  
> \- 🔒 `Bash`: `rm -rf node_modules && npm install`  
> \- 🔒 `Write`: `src/config/production.json`

### 任务取消

> 🚫 **任务已取消**
>
> 📋 任务：auth-refactor  
> 🔖 编号：`job_1774251330123_a1b2c3`
>
> 如果需要了解取消前的执行情况，我可以帮你查看日志。

### 并发上限

> ⚠️ 当前正在运行的任务已达到上限（2 个），暂时无法接收新任务。
>
> 你可以等待当前任务完成，或取消某个任务后再提交。

## Hook 配置

为保证任务在 runner 进程意外中断时仍能正常收尾，需要配置 Claude Code 的 stop hook 作为 fallback。

在 `~/.claude/settings.json` 中添加：

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

将路径替换为你的实际安装路径。

## 项目结构

```
openclaw-claude-code/
├── SKILL.md                  # OpenClaw skill 定义（触发条件、工作流程）
├── references/
│   ├── protocol.md           # CLI 协议、schema、运行时布局
│   ├── ux-feedback.md        # 用户消息模板（强制遵守）
│   └── error-codes.md        # 错误码及恢复指引
├── scripts/
│   └── bridge.py              # CLI 入口
├── src/openclaw_claude_code/
│   ├── cli.py                # 参数解析
│   ├── service.py            # 核心业务逻辑
│   ├── runner.py             # Claude Code 进程管理
│   ├── runtime.py            # 路径解析、环境检测
│   ├── models.py             # 数据模型
│   └── errors.py             # 错误定义
└── tests/
    └── test_openclaw_claude_code.py
```

## License

MIT
