# 2026-11-17 - 交接单认领流程：按最近 9 个认领会话的问题修 5 处

## What was done

排查了 2026-11-16 到 17 日 newn8nbase 的认领会话（TASK-113/124/125/126/127/128/130/132/133/142），按用户确认的 5 步修改：

1. **交交接单时就查测试命令**：`verify.tests` 引号外出现中文或全角标点即退回（TASK-124、126 都是把说明写进命令，到汇报时才被拒收，只能放回、重交、再领）。字段提示也改了。
2. **任务分支已整个并进主分支时汇报照样能交**：原逻辑把主分支上已有的提交都不算新提交，用户先合并部署后汇报就永远被判「没有新提交」（TASK-130 卡在进行中）。现在这种情况改认任务分支 reflog 里 commit / cherry-pick / revert / am 出来的提交；单纯快进或变基到主分支不留这类记录，原防护不变。用真实 TASK-130 工作区核对：能认出那 2 个提交。
3. **验收通过一次合并**：`done` 对有工作区的卡等同 `accept`（标完成 + 快进合并 + 删工作区），合并不了时卡片留在待验收、报错附带 `accept --no-merge` 的用法。经用户同意，同步改了全局 `~/.claude/CLAUDE.md` 的验收约定（done 会合并、被拒时转告；带交接单的卡验收不通过用 reject）。
4. **认领可沿用会话自己的 worktree**：`claim --here <会话目录>`（MCP `claim_tasks` 的 `here`）。只接受同一仓库里干净的附属 worktree，项目主目录不行；一次只领一张；已有工作区的卡照旧沿用并在领取包 `note` 里说明。验收、取消时不删会话的文件夹。看板「复制给 Agent」的领取指令加了这句说明。`/claim` 技能收进 `prompts/claim-skill.md`，`scripts/install_skills.py` 改为两个技能都装（`--skill` 可只装一个），已用 `--force` 覆盖安装到 `~/.claude/skills/claim/SKILL.md`。
5. **收尾**：新增 `project-inbox note TASK-N --text`，只往备注末尾追加（TASK-132 会话曾用 update 整段改写 notes）；`bin/project-inbox`、`bin/project-inbox-mcp`、`bin/backlog` 经软链接调用时先解析到脚本本体再定位项目目录。

旧工作区清理只做了预演（`workspace clean`，未加 `--apply`），等用户确认范围。

## Testing

- 后端 `.venv/bin/python -m pytest -q`：109 passed。新增/改写：说明文字混进测试命令被退回、引号内中文放行；任务分支已并入主分支后汇报通过并能 accept；done 一次合并；done 合并被拒时卡片留在待验收、`--no-merge` 仍可用；`--here` 领取→提交→汇报→done 全流程且不删会话文件夹；`--here` 拒绝项目主目录和有未提交改动的文件夹；note 只追加；技能安装两份。
- 软链接实测：直接软链接和相对软链接调用 `project-inbox` 都能正常列出任务。
- 前端 `bun test tests/` 未跑：本机没有可用的 bun（`node_modules/bun` 未完成 postinstall）。本轮没有改看板补丁和前端代码，只改了后端生成的领取指令文字。
- 正在运行的 MCP 服务要重启后才认得 `here` 参数；命令行即时生效。

## Notes

- Changed files：`server/contract.py`、`server/workspace.py`、`server/flow.py`、`server/cli.py`、`server/main.py`、`server/handoff.py`、`bin/project-inbox`、`bin/project-inbox-mcp`、`bin/backlog`、`scripts/install_skills.py`、`prompts/claim-skill.md`（新）、`tests/test_contract.py`、`tests/test_flow.py`、`docs/handoff-flow.md`、`README.md`；仓库外：`~/.claude/CLAUDE.md`（验收约定两处）、`~/.claude/skills/claim/SKILL.md`（覆盖安装，旧版备份在会话临时目录）。
- Rollback：仓库内 `git checkout -- <上述文件>` 并删 `prompts/claim-skill.md`；全局规则把第 2 条改回「运行 project-inbox done 标记完成，并在 notes 追加"用户验收通过"」并删去 reject 那一行。
- 未修：主分支在验收前动过时仍不能快进，需要 reject 让 agent 更新后重新汇报（TASK-142 的情形）。

## 2026-11-17 - 续：按用户选择只清 newn8nbase 的 5 个旧文件夹

### What was done

- 先核对：5 个文件夹都没有未提交改动，各自 HEAD 都已在 main 上；主目录在 main，另有 2 个其他任务的未跟踪文件（未触碰）。
- TASK-128、TASK-132：运行 `project-inbox accept`（main 已含这些提交，没有产生新提交，main 仍为 c1e98b8a），程序删除工作区，领取记录改为已合并。
- TASK-126：汇报后分支又有提交，accept 会拒绝，改用 `git worktree remove .worktrees/task-126`，并用 `project-inbox note` 在卡片上记了一笔。
- Claude 会话文件夹 `.claude/worktrees/task-132-claim-5423c5`、`task-133-pending-confirmation-cedcf7`：`git worktree remove`（未用 --force）。
- 分支 task-126 / 128 / 132 全部保留；TASK-130 的两个文件夹未动；其他项目的 5 个旧文件夹未动。

### Testing

- `git worktree list` 只剩 TASK-130 的两个；`git branch --list` 三个任务分支仍在。

### Notes

- Rollback：需要时 `git worktree add .worktrees/task-126 task-126`（128、132 同理）即可恢复工作区。

## 2026-11-17 - 续：清理其他 4 个项目里 5 个旧文件夹

### What was done

- 用户要求清理 lab-workspace、wangsnapgene、mail-assistant、daily-workflow-mac 的旧文件夹。先逐个核对：4 个工作区都没有未提交改动，HEAD 都已在各自 main 上；daily-workflow-mac 的 `ui-optimization` 文件夹早已不存在，只剩 git 登记，且被一个 7 月的 Claude 会话锁着（pid 24484，进程已不存在）。
- `project-inbox workspace clean --apply`：用 git 移除 lab-workspace-task-69（TASK-69）、wangsnapgene 的 inbox-task3-5（TASK-3）与 task-48（TASK-48）、mail-assistant-task-70（TASK-70）。
- 锁住的登记 prune 不会清，另行 `git worktree unlock` + `git worktree prune`（daily-workflow-mac）。

### Testing

- 4 个文件夹已不存在；ui-optimization 的登记已从 `git worktree list` 消失。
- 分支 task-69、inbox-task3-5、task-48、task-70、worktree-ui-optimization 全部保留。

### Notes

- Rollback：`git -C <项目> worktree add <原路径> <分支>` 可恢复任一工作区。
- 发现：`workspace clean` 不处理被锁住的过期登记（只报已处理），本轮没有改这一点。
