# Backlog.md 接入与维护

2026-10-14 用户选择采用 Backlog.md 底座，本工作区已完成切换。原库实际任务为 0，未执行真实任务迁移、删除或数据库结构变更。切换前文件保留在 `work/legacy-before-backlog-2026-10-14/`，清单为其中的 `manifest.json`。

## 当前形态

Cherry 工作智能体负责理解、分类与工具调用；Project Inbox Python MCP 负责项目发现、匹配、查重与接口兼容；任务写入转交 Backlog 原生 CLI。Backlog Web、CLI 和 MCP 适配层共用 `backlog/` 中的 Markdown，不维护第二套任务库。

原有十个 MCP 工具名保持不变。Cherry 使用 Project Inbox MCP，未叠加另一套 Backlog MCP，避免重复工具与项目规则不一致。`bin/backlog` 是锁定本工作区与补丁运行时的原生命令入口；日常优先用 `project-inbox`。

看板为 http://127.0.0.1:6420/board，只监听本机回环地址。看板现由 launchd 用户后台服务常驻，登录后自动启动、退出后自动恢复；双击 `bin/打开看板.command` 只打开页面。MCP 独立通过 stdio 工作。维护时须用 launchctl bootout 停止服务，不能只杀进程；详见 [后台服务](background-service.md)。未配置 Docker 或局域网访问。

## 字段和目录

| Project Inbox / MCP | Backlog / 看板 |
| --- | --- |
| inbox | Inbox |
| todo | To Do |
| in_progress | In Progress |
| blocked | Blocked |
| done | Done |
| archived | completed 或 archive 中的记录，适配层只读 |
| P0–P3 | 页面大写，Markdown 原生小写 |
| project_id | 注册表稳定 ID，对应原生 project 的项目显示名称 |
| tags / commits | labels / references 中的 commit: 引用 |
| created_at / updated_at | 原生 UTC 分钟时间，MCP 转为配置时区的带偏移时间 |

新任务编号为原生 `TASK-1` 形式，跨项目共享；不再生成 UUID。支持读取原生子任务编号。未关联项目默认进入 Inbox；已关联项目默认 To Do。项目与状态是两个字段，后续可独立修改。

`backlog/tasks/` 为活动任务；Done 仍属于活动任务。助手归档 Done 时调用原生 complete，移入 `backlog/completed/`；其他状态调用原生 archive，移入 `backlog/archive/tasks/`。两类收起记录仍可通过 Project Inbox 搜索和读取，不自动恢复、修改或永久删除。看板的原生归档界面与 MCP 搜索范围不完全相同。

中文问题描述、期望行为、复现步骤放在原生 DESCRIPTION 区块，补充说明放在 NOTES 区块。页面可直接编辑这些内容，保留上游区块标记即可；自行删改中文小节结构后，适配层可能把整个描述视为 description，原文仍在 content 中。

`refresh_projects` 把注册表显示名称合并到 Backlog 项目选项，保留原生已有选项；不在十个业务仓库逐一 init。新增到 Backlog 的自由项目名不会自动创建本机项目路径或注册表 ID；名称建议通过注册表维护，避免同名和随意重命名。

## 编号兼容补丁

原版 1.53.0 的编号分配不计入 archive，实测会把已归档的 TASK-1 分配给新任务。Project Inbox 要求旧 Issue 引用稳定，因此仅在原生编号分配中把归档 ID 加入占用集合；看板沿用上游布局，中文化通过独立源码补丁实现。

- 上游：[MrLesk/Backlog.md v1.53.0](https://github.com/MrLesk/Backlog.md/releases/tag/v1.53.0)，MIT；commit `fd20f71493fb4eda44b021aa89f257956f17fb71`。
- 编号补丁：`patches/backlog-1.53.0-retain-archived-ids.patch`；中文补丁：`patches/backlog-1.53.0-zh-cn.patch`。
- 源码、归档与补丁校验：`patches/backlog-source.json`；运行时版本 `1.53.0-project-inbox.2`。
- 构建产物：`vendor/backlog-runtime/backlog`，许可证同目录；源码在 `vendor/backlog-source/`。
- 回归测试先在原版复现编号冲突，再在补丁运行时通过；原生 CLI 与适配层并发创建也已验证。

不要直接用 `npx backlog.md` 或 node_modules 中的原版二进制写入正式库；升级须重新核对该行为和适配测试。

## 重建步骤

以下命令针对当前 macOS Apple Silicon 安装，先按 [后台服务](background-service.md) bootout 本库看板并关闭 MCP，避免服务自动重启干扰维护。依赖下载需要网络；日常运行不需要联网安装包。

```bash
cd /Users/wangyichuan/selfapp/ProjectInbox
/Users/wangyichuan/.local/bin/uv sync --locked
npm ci --ignore-scripts --no-audit --no-fund
.venv/bin/python -m scripts.build_backlog
.venv/bin/python -m pytest -q
.venv/bin/python -m scripts.verify_install
```

构建入口为 `scripts/build_backlog.py`；输入是 package-lock、上游 bun.lock、源码归档和补丁清单；输出为上述运行时与许可证。脚本使用锁定的原生 Bun 1.4.3，核对源码归档及补丁后文件哈希，拒绝自动接受变化；源码缓存缺失时通过 gh 下载官方固定 commit。Node 安装禁用生命周期脚本，Bun 源码依赖安装同样禁用脚本。当前构建记录在 `work/verification/backlog-build.json`。

安装路径被 launcher 和 Cherry 配置固定。移动安装目录后，需重新安装 Python editable 包、重建 bin 启动器与 Cherry 参数，不只移动任务目录。

## 配置与回滚

保持 `backlog/config.yml` 的 filesystem_only=true、auto_commit=false、remote_operations=false、check_active_branches=false。不设置任务或全局 on_status_change 回调，不开启自动编码。适配层拒绝这些模式变更；浏览器本身拥有本机用户的文件权限，并非沙箱。不要随意改动既定五个状态、类型、优先级与任务目录。

回滚先停止本库写入，完整备份当前 `backlog/` 与 `config/`。旧实现文件和空任务库在切换前快照中可复核，但旧实现不能读取新的 Backlog 格式；不能简单恢复旧代码并宣称新任务仍可访问。只要新库已产生真实任务，先列清单并设计导出/迁移，再由用户确认涉及覆盖的步骤。本次未覆盖旧任务或删除快照。

已验证主流程及自动化测试；浏览器拖拽和 Archive Task 原生确认流程未完成端到端验收。归档在真实 MCP / CLI 与回归测试中已验证，日常可由助手执行。详细证据见 [部署结果](deployment-report.md)。

## 中文界面（2026-10-15）

导航、看板列、类型与筛选、任务详情、常用按钮、列表、设置及相对日期等已中文化。Project Inbox / Backlog.md、任务编号、项目名称和用户正文保留原文；中文仅用于显示，不翻译表单 value、持久化状态或类型。

中文补丁覆盖 37 个上游界面文件，新增的显示标签函数没有网络请求或外部翻译依赖。构建脚本校验每个补丁文件的前后 SHA，拒绝覆盖未知源码；从未改动的源码回放补丁也已校验通过。编译先写 .building，验证版本后原子替换运行时，不直接覆盖正在运行的二进制；看板需重启才能显示新版本。回滚旧运行时也应先停止本看板，保留当前任务库。

## Agent 交接按钮

运行时 `1.53.0-project-inbox.4` 的 `patches/backlog-1.53.0-agent-handoff.patch` 包含单条和多选交接，在中文补丁之后应用，涉及任务详情、看板、卡片、共用复制 Hook、浏览器 API 客户端及本机服务。构建清单分别校验补丁前后 SHA（含新增文件），支持从原始源码重放与已打补丁源码重复构建。

任务详情提供「先调研」和「复制给 Agent」。本机只读 `GET /api/tasks/:id/handoff` 使用固定 Python 入口和参数数组调用 `server.cli handoff`，`mode=research` 生成只读调研指令，默认 `mode=execute` 生成执行指令；不经过 Shell、不执行业务项目代码。现有监听地址、鉴权与状态回调配置保持原值。安装目录需保留 `.venv` 与 Python 模块。CLI 的 `handoff` 输出 JSON `{text: ...}`，从最新任务和项目注册表生成指令，不新增任务字段或存储副本。用法和失败处理见 [Coding Agent 协作](coding-agent-integration.md)。

多选交接在一次读取中完成全库完整性检查，再按所选编号提取任务，避免每个编号重复扫描全库而触发看板请求超时。选择顺序、去重及缺失或归档项整批拒绝的规则保持一致。该逻辑位于 Python 模块 `server/handoff.py`，下一次复制请求即加载生效，无需重建或重启看板。

多选复用看板现有选择状态，增加可见复选框，通过只读 `GET /api/tasks/handoff?id=TASK-ID-1&id=TASK-ID-2` 调用同一个生成入口；单条接口兼容保留。CLI 支持多个位置参数，按首次出现的顺序去重，按项目分组后生成一段指令；校验失败不返回部分清单。生成不修改任务，实际执行时由 Agent 按编号逐条更新。

前端测试入口：`node_modules/@oven/bun-darwin-aarch64/bin/bun test tests/web-localization.test.ts`。五项测试覆盖中文列、类型标签、自定义类型回退、筛选回调、详情与批量移动选项的原生值保留。类型检查入口为同一 Bun 运行 `vendor/backlog-source/node_modules/typescript/bin/tsc --noEmit -p vendor/backlog-source/tsconfig.json`。实际 Cherry 记录后已在中文详情中核对内容；观察记录在 work/verification/zh-ui.json。

部分第三方编辑器帮助和复杂异常信息可能仍保留上游原文；这次不改变数据格式或翻译用户输入。

## 卡片类型标签显示（2026-10-15）

运行时 `1.53.0-project-inbox.5` 修复了窄卡片上“缺陷”被压缩为“缺”的显示问题：顶部元信息允许换行，任务类型标签不参与宽度压缩。项目名称继续使用现有省略和悬停提示。修改包含在 Agent 交接补丁内，并更新构建清单中卡片文件的输出 SHA。类型和任务状态的持久化值不变。

已在编译后的本机看板验证五种类型、180/230/280/360 像素卡片宽度，共 20 个场景，类型标签无裁切、卡片无横向溢出。旧运行时备份在 `work/card-label-fix/backlog-before-fix`；回滚前停止本库看板，再恢复运行时与对应补丁、版本清单，不回退任务库。

## 待验收状态（2026-10-18）

运行时 `1.53.0-project-inbox.6` 新增状态 `Review`（CLI / MCP 值 `review`，看板显示「待验收」），列顺序为 Inbox、To Do、In Progress、Review、Blocked、Done；Done 仍为最后一列，看板据此判定完成。待验收列使用紫色计数标签和紫色边框，列表视图状态标签同为紫色。补丁 `patches/backlog-1.53.0-review-status.patch` 在 Agent 交接补丁之后应用，涉及 `TaskColumn.tsx`、`TaskList.tsx`、`labels-zh.ts`，构建清单 `review_files_sha256` 校验前后 SHA。

已有任务文件格式不变。新建任务库会自动带 Review；已有任务库需在 `backlog/config.yml` 的 `statuses` 中 In Progress 之后加入 `Review`，否则设为 review 会被 Backlog 拒绝，其余功能不受影响。CLI 的 `done` 不强制要求先经过 review，是否可完成由 Agent 按验收约定判断。

回滚：停止看板服务，从 `work/backup-2026-10-18-review/project.tar.gz` 恢复 `vendor/backlog-runtime/backlog`、`patches/`、`scripts/build_backlog.py`、`server/` 与 `vendor/backlog-source/src/web` 中三个文件；若已有任务处于 Review，先改回其他状态再从 config 移除 Review。

## 完成后直接合并部署勾选项

运行时 `1.53.0-project-inbox.8` 在任务详情和「已选 N 项」工具栏增加「先调研」按钮。它通过 `mode=research` 生成只读调研指令，调研结果要求回填原任务 notes 并进入待验收；「复制给 Agent」继续生成执行指令。原有「完成后直接合并部署」勾选项只对执行模式生效，默认不勾选。研究交接补丁为 `patches/backlog-1.53.0-research-handoff.patch`，构建清单的 `research_files_sha256` 校验从中文、执行交接和待验收补丁之后的源码输入，防止覆盖未知版本。

## 流程体验（2026-11-16）

运行时 `1.53.0-project-inbox.9` 让看板直接回答「哪张卡在等我、我该做什么」。补丁 `patches/backlog-1.53.0-flow-ux.patch` 在调研交接补丁之后应用，只改界面，不新增接口，不改任务文件格式；构建清单 `ux_files_sha256` 校验 9 个文件的前后 SHA（含 2 个新增文件）。

- 任务详情：带「交接单」标签的卡在待验收、受阻、收件箱且「待确认」时，顶部出现「下一步」提示。待验收给出 `project-inbox accept TASK-N` 和 `project-inbox reject TASK-N --comment "…"`，受阻给出 `project-inbox cancel TASK-N --reason "…"` 并说明答复后怎样回到待办，每条命令可一键复制；剪贴板被拒绝时不提示成功，而是替你选中整条命令，按复制键即可。验收、退回、取消仍然只在命令行里执行。其余状态不显示提示。
- 任务详情：待验收和受阻的卡，「完成总结」（汇报单）排在最前面，不用再翻过参考链接、修改的文件等空分区。其他状态顺序不变。
- 看板：列的最小宽度由 256px 改为 204px。1440 宽、侧栏展开时收件箱到受阻五列不用横向滚动，收起侧栏六列全部显示；宽屏照常拉伸。
- 卡片：受阻的卡直接显示汇报单里的受阻原因（最多三行）；「待确认」标签用琥珀色；待验收的卡也显示验收进度（例如 3/4）。验收进度从卡片右上角挪到底部日期旁，窄卡片上不再把编号、类型、项目挤成一条窄列。
- 汇报单：受阻原因单独成段（`server/contract.py`），此前在详情里会和「改了什么」接成同一段。
- 补齐漏翻的英文：已完成列底部的「收起旧任务」、收起对话框、列表页的「显示 N / M 条」和验收进度的悬停提示。

界面测试入口：`node_modules/@oven/bun-darwin-aarch64/bin/bun test tests/`，新增 `tests/web-flow-ux.test.tsx` 4 项。回滚：停止看板服务，从 `patches/backlog-source.json` 移除 `ux_patch`、`ux_files_sha256` 并把 `runtime_version` 改回 `.8`，在 `scripts/build_backlog.py` 去掉对应一段后重建；不需要回退任务库。
