# Project Inbox

本机 Issue 收件箱与可编辑看板，日常流程是：**Cherry Studio → 工作 → Project Inbox → 随手记录 → 模型整理 → 看板查看和管理**。沿用现有 Qwen38 / New API，任务保存为本机 Markdown。

安装目录：`/Users/wangyichuan/selfapp/ProjectInbox/`。Backlog.md 提供看板和唯一任务库；Project Inbox 保留活跃项目发现、匹配、查重及原有 10 个 MCP 工具。无需 Docker；看板已配置为 macOS launchd 用户后台服务，登录后自动启动、退出后自动恢复。

## 立即使用

1. 在 Cherry Studio 的 **工作 → Project Inbox** 中发送自然语言需求，例如「记一下演示项目：上传后希望保持文件选择顺序」。模型识别项目、检查重复并保存，返回实际 `TASK-1` 形式的编号。
2. 双击工作区中的 **[打开看板.command](bin/打开看板.command)**，浏览器会打开 [本机看板](http://127.0.0.1:6420/board)。看板在后台常驻，无需保留终端窗口；双击仅打开页面。
3. 在看板按项目、类型、优先级筛选，点开 Issue 修改描述、状态和优先级。之后在 Cherry 中重新查询即可读到最新值。
4. 任务描述不完整或需要确认项目现状时，在任务详情或看板勾选任务后先点 **先调研**。把生成的只读指令复制到新的 Agent 会话；Agent 只查看项目、区分事实与假设，把调研报告追加到原任务 notes，并将任务设为待验收。确认调研结论后，再点 **复制给 Agent** 生成执行指令。目标明确且改动很小的任务可以直接执行。两种交接都不自动启动 Agent，复制本身也不修改任务；看板上的「完成后直接合并部署」仅对执行模式生效，默认不勾选。

看板已中文化，六列为收件箱、待办、进行中、待验收、受阻、已完成；原生状态值 Inbox / To Do / In Progress / Review / Blocked / Done 保持兼容。Agent 完成后只设为待验收（review），由你说「验收通过」后再标记完成。修改状态只管理记录，不自动启动编码 Agent。已完成的 Issue 可留在 Done；需要收起时让助手归档。

已实际验证「Cherry 创建 → 看板可见 → 看板修改为进行中、P1 → Cherry 重新读取」、单条/多选执行交接，以及“先调研”交接；合成数据全部位于隔离库，正式库未写入演示任务。当前 60 项后端测试与 17 项界面测试通过，证据与限制见 [部署结果](docs/deployment-report.md) 和本轮 [进度日志](progress/2026-11-02-research-handoff.md)。

## CLI

已安装 `/Users/wangyichuan/.local/bin/project-inbox`。PATH 包含该目录时可直接使用；`TASK-ID` 须替换为实际编号。

2026-11 起新增交接单流程：讨论结束交交接单、执行会话领取、做完交汇报单，程序检查格式、建工作区、重跑测试；验收和退回只有你能做。说明见 [交接单流程](docs/handoff-flow.md)。

```bash
project-inbox handoff-template
project-inbox submit-handoff --file 交接单.yaml --author pi
project-inbox claim TASK-ID --agent codex
project-inbox submit-report --file 汇报单.yaml --agent codex
project-inbox accept TASK-ID
project-inbox reject TASK-ID --comment "意见"
project-inbox cancel TASK-ID
project-inbox workspace scan
project-inbox workspace clean          # 预演；加 --apply 才执行
```

```bash
project-inbox projects
project-inbox list --project newn8nbase --status todo
project-inbox search "上传"
project-inbox show TASK-ID
project-inbox handoff TASK-ID
project-inbox handoff TASK-ID-1 TASK-ID-2
project-inbox handoff --mode research TASK-ID
project-inbox update TASK-ID --changes '{"priority":"P1"}'
project-inbox done TASK-ID
project-inbox archive TASK-ID
project-inbox check
project-inbox refresh
```

`create` 支持标题、`--project`、`--type`、`--priority`、`--description`、`--expected-behavior`、`--reproduction-steps` 和重复的 `--tag`。直接用 CLI 创建时应先查询重复项。

## 项目来源与数据

检查以下根目录内最近三个月有 Git 提交或本地文件改动的项目，沿用配置的最大目录深度与排除规则：

- `/Users/wangyichuan/Desktop/wangcodemac/`
- `/Users/wangyichuan/localissue/`
- `/Users/wangyichuan/selfapp/`

刷新纳入全部符合条件的项目，不再限制前十个；保留已有项目、ID 及人工字段，较早的项目不自动删除。Git 项目按本地各分支的提交时间，以及暂存、未暂存、未跟踪文件的修改时间筛选；忽略 Git 排除文件和常见生成目录。无 Git 但有项目清单的目录按本地文件修改时间筛选，同一顶层项目的组件合并登记。本目录也可作为关联项目。无需读取 Agent 会话或连接远程仓库。

“三个月”按配置时区向前推三个自然月；完整口径、边界和刷新命令见 [项目发现](docs/project-discovery.md)。

项目注册表在 `config/projects.yaml`，范围和时区在 `config/settings.yaml`。任务唯一来源为 `backlog/tasks/`，已收起任务位于 `backlog/completed/` 或 `backlog/archive/tasks/`；归档仍可通过 Project Inbox 查询。旧 `data/` 保留但不再写入，切换前实际任务数为 0。

## 安装与维护

当前使用 Python 3.13.13、MCP SDK 2.3.0、PyYAML 6.0.3，以及 Backlog.md **1.53.0-project-inbox.9**（归档编号保留、中文界面、单条/多选 Agent 交接、只读调研交接、合并部署勾选项、卡片标签显示修复、待验收状态与流程体验）。Python / npm / 上游源码依赖均有锁定信息。

当前机器已安装完成。重建和跨机器安装见 [Backlog 接入说明](docs/backlog-integration.md)；不要改用未经补丁的官方二进制写入当前库。

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m scripts.verify_install
```

安装验证的写操作只发生在临时目录，正式库仅检查完整性。看板、MCP、CLI 共用数据，无需同步。备份整个 `backlog/` 和 `config/`；新版不提供旧版 `.history` 自动快照。

常驻服务安装、状态、重启和维护前停止见 [后台服务](docs/background-service.md)。`project-inbox board` 保留为手动前台命令，使用前需先停止后台服务，避免端口冲突。

使用与恢复：[Cherry Studio 接入](docs/cherry-studio-setup.md)、[数据与恢复](docs/storage-and-recovery.md)、[Coding Agent 协作](docs/coding-agent-integration.md)。选型依据见 [开源方案调研](docs/2026-10-14-frontend-research.md)。
