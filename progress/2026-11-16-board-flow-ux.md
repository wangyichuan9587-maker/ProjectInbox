# 2026-11-16 看板流程体验

## What was done

用合成数据（15 张卡，覆盖六个状态和交接单流程的各个分支）在隔离任务库里跑起看板，按「打开看板 → 找到要我处理的卡 → 知道下一步做什么」走了一遍，改了以下几处。全部做成新的一层界面补丁 `patches/backlog-1.53.0-flow-ux.patch`，运行时版本 `1.53.0-project-inbox.9`。

1. 待验收的卡，汇报单排在详情最底部，要翻过参考链接、修改的文件、完成标准、实施计划、补充说明、评论才能看到。现在待验收和受阻的卡把汇报单放在最前面。
2. 看板只显示状态，验收、退回、取消要自己记命令。现在待验收、受阻、待确认的卡在详情顶部给出「下一步」和可一键复制的命令。没有新增接口，这三件事仍然只在命令行里执行。
3. 六列最小共需 1616px，1440 宽的屏幕上受阻和已完成两列在屏幕外，而受阻正是要人处理的一列。列最小宽度改为 204px 后，侧栏展开时收件箱到受阻五列全部可见，收起侧栏六列全部可见。
4. 受阻的卡在看板上看不出卡在哪里。现在卡片上直接显示受阻原因。
5. 「待确认」和其他标签一样是灰色。现在用琥珀色。
6. 验收进度只在进行中显示。现在待验收也显示，一眼能看出还有几条没被勾上。
7. 列变窄后，卡片右上角的验收进度和优先级会把编号、类型、项目挤成一条窄列（项目名被截断）。验收进度挪到了卡片底部日期旁。
8. 受阻汇报里，受阻原因和「改了什么」在详情里接成了同一段。`render_summary` 在受阻原因后补了空行。
9. 漏翻的英文：「Clean Up Old Tasks」、收起对话框里的 7 处、列表页的「Showing N of M tasks」、验收进度的悬停提示。

## Testing

- `bun test tests/`：21 pass（原 17 项，新增 `tests/web-flow-ux.test.tsx` 4 项）。
- `tsc --noEmit -p vendor/backlog-source/tsconfig.json`：通过。
- `.venv/bin/python -m pytest -q`：90 passed、1 failed。失败的是 `test_board_copy_gives_short_claim_text_for_handoff_cards`，它在 main 上就随安装路径长短变化，和本次改动无关。
- 构建脚本三条路径都跑过：删掉 `vendor/backlog-source` 从上游归档重放全部六层补丁；在 `.8` 的源码上只应用新的一层；在已打完补丁的源码上重复构建。三次都通过 SHA 校验，重放结果和手工修改的文件逐字节一致。
- 用编译后的运行时在浏览器里实际看过：1440 宽侧栏展开和收起、浅色和深色主题、待验收 / 受阻 / 待确认三种卡的详情。复制按钮在这个内嵌浏览器里实际走的是「剪贴板被拒绝」分支（按钮显示「已选中，请手动复制」并选中整条命令）；复制成功的分支由界面测试覆盖，机制和已有的「复制给 Agent」相同。

## Notes

没有做、留给你决定的：

- 看板上直接点「验收通过」「退回」的按钮。这需要新增会改 git 的接口，和「只有用户能走的门只在命令行里」的现有约定有关，没有擅自加。
- 「全部任务」列表页没有项目和类型两列，却有用不上的排序号和里程碑；对多项目的收件箱来说项目列更有用。
- 详情页右上角最醒目的橙色按钮是很少用到的「转为草稿」。
- 补充说明里的「分支 task-10」会被自动识别成任务链接。

Changed files：`patches/backlog-1.53.0-flow-ux.patch`（新增）、`patches/backlog-source.json`、`scripts/build_backlog.py`、`server/task_manager.py`（运行时版本号）、`server/contract.py`、`tests/web-flow-ux.test.tsx`（新增）、`tests/test_contract.py`、`README.md`、`docs/backlog-integration.md`、`docs/handoff-flow.md`、本日志。

Rollback：revert 本次提交后重新运行 `scripts/build_backlog.py`（需要先删掉 `vendor/backlog-source`，或在其中反向应用新补丁）；任务库不用动。
