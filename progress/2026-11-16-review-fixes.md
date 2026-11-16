# 2026-11-16 代码审查问题修复

## What was done

通读 `server/`、`scripts/`、补丁和测试后，在隔离目录用合成仓库逐条复现，修复以下 13 个问题（每条都有回归测试，且确认在修复前的代码上会失败）：

1. 新建任务后取错编号：标题里提到别的任务（「修复 TASK-2 引入的回归」）时，程序把新卡的编号认成 TASK-2。`submit_handoff` 会把交接单、实施计划和备注写到那张无关的卡上，新卡留在待办却没有交接单；`create_task` 报「任务不存在」但卡其实已建；`reject` 生成返工卡时同样受影响。现在只认回显里「Task TASK-N - 」这一行和文件名。
2. `workspace clean --apply` 会删掉返工卡正在用的工作区：返工卡沿用原卡的文件夹，名字是原卡编号，扫描把它算到已归档的原卡头上。现在先按领取记录认工作区属于哪张还没收尾的卡。
3. `refresh` 能把注册表写坏，之后所有命令（包括 `refresh` 自己）都报错：下划线开头的目录名生成不合法的项目 ID；不同位置的同名目录生成重复的显示名称。现在 ID 去掉开头的下划线，重名的显示名称自动加上级目录名，写入前先用读取时的同一套检查过一遍。
4. `accept` 会把汇报之后才加到任务分支上的提交一起合并，这些提交没有经过重跑测试等核验。现在拒绝并说明两条出路。
5. `check` 对每张正常待验收的卡都误报「可能在看板上被拖动过」：汇报通过后领取记录本来就保持到验收。
6. 工作区文件夹被手动删掉后再领取，程序把不存在的路径当成「沿用已有」发给 agent，汇报时才报「工作区不存在」，放回重领也一样。现在清掉 git 里的残留登记，按任务分支重建。
7. `server/handoff.py` 在 f-string 表达式里用了反斜杠，Python 3.11 上是语法错误，而 `pyproject.toml` 声明支持 3.11。
8. 测试命令自己已经正常结束，但留下占着输出的后台进程时，会一直等到超时并判为失败。现在输出写临时文件，只等测试命令自己退出（上一轮的 `DRAIN_SECONDS` 限时读取不再需要）。
9. 任务分支只是快进或变基到最新主分支时，主分支的提交被算成 agent 的「新提交」：没有自己的提交也能通过，核验结果里的提交数也虚高。
10. 交接单 / 汇报单里的畸形值（`kind` 写成列表、YAML 里的数字键）抛 `TypeError`，命令行是一屏 traceback，而不是可照着改的退回理由。
11. 手工把 `config/projects.yaml` 等 YAML 改坏后，所有命令都是 traceback。现在报「config/projects.yaml 不是合法的 YAML」。
12. notes 超过 2 万字后（程序追加的备注不受限制），连只改状态或优先级的 `update` 也被拒绝。现在更新时只检查这次要改的字段。`update --changes '{"task_id": …}'` 不再抛 `TypeError`。
13. 清理时子任务文件夹 `task-5.2` 被认成 TASK-5。

另外修了一个和代码无关的测试问题：`test_board_copy_gives_short_claim_text_for_handoff_cards` 断言指令短于 1500 字，但指令里三次带上安装目录和临时目录的绝对路径，安装路径长一点就失败。现在只量指令本身。

## Testing

- 修复前基线：`.venv/bin/python -m pytest -q` 90 passed、1 failed（上面那个随路径长短变化的断言）。
- 修复后：`.venv/bin/python -m pytest -q` 104 passed（新增 13 个回归测试）。
- 把 `server/` 暂时还原成修复前的代码再跑新增测试：13 个全部失败，说明它们确实覆盖了问题。
- Python 3.11：修复前 `server/handoff.py` 编译报 `f-string expression part cannot include a backslash`，修复后 `server/`、`scripts/` 全部通过编译；另建 3.11.15 环境跑完整测试，104 passed。
- 前端 `bun test tests/`：17 pass（本轮没有改前端和看板补丁）。

## Notes

行为变化（文档 `docs/handoff-flow.md`、`docs/project-discovery.md` 已同步）：

- `accept` 在任务分支比汇报时多出提交时会拒绝合并。
- 汇报时「新提交」不再包含主分支上已有的提交。
- 测试命令留下的后台进程不再影响结果；超时后仍会停掉整个进程组。

审查中看到、但这次没有改的地方（需要你来决定取舍）：

- 验收、退回、取消只是「不进 MCP」。没有 MCP 的 agent 用的是同一个命令行，程序层面拦不住它运行 `accept`，目前靠领取包里的规矩约束。
- 完成但还没合并的卡（`done` / `accept --no-merge`）可以被 agent 通过 MCP 归档，之后 `accept` 补做合并会被拒绝。
- 一次领取多张卡时，中途某张改状态失败会直接报错，前面已经领到的卡不会出现在返回结果里。
- 看板上同时勾选带交接单和不带交接单的卡时，全部走旧的长指令，带交接单的卡会绕过领取流程。
- `server/activity.py` 只剩测试在用；`README.md` 里的测试数量（60 项）已过时。

Changed files：`server/task_manager.py`、`server/workspace.py`、`server/flow.py`、`server/checks.py`、`server/project_registry.py`、`server/contract.py`、`server/storage.py`、`server/handoff.py`、`tests/test_flow.py`、`tests/test_workspace.py`、`tests/test_contract.py`、`tests/test_boundaries.py`、`docs/handoff-flow.md`、`docs/project-discovery.md`、本日志。

Rollback：revert 本次提交即可；没有改动数据格式，`work/` 下已有的交接单、领取、汇报记录都兼容。
