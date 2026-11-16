"""生成 Agent 交接指令；生成过程只读任务库，不复制任务正文或修改任务。"""
from pathlib import Path
import shlex
from .project_registry import Registry
from .task_manager import TaskManager


HANDOFF_MODES = {'execute', 'research'}


def claim_handoff(cli, tasks, deploy=False):
    """带交接单的卡：规矩已经由程序执行，指令只剩领取包的几步。"""
    ids = [task['id'] for task in tasks]
    names = '、'.join(ids)
    lines = '\n'.join(f"- {task['id']}：{task['title']}" for task in tasks)
    note = '\n本组任务走交接单流程：合并在我验收通过时由程序完成，「完成后直接合并部署」对它们不生效。' if deploy else ''
    text = f"""请领取并执行 Project Inbox 的 {len(tasks)} 个任务：{names}。全程用中文和我沟通。
{lines}

1. 领取：有 Project Inbox 的 MCP 工具就调用 claim_tasks（agent 填你的名字，task_ids 填上面的编号）；没有就运行
{cli} claim {' '.join(shlex.quote(i) for i in ids)} --agent <你的名字>
返回的领取包里有交接单、工作区路径（workspace）、截图目录（evidence_dir）和空汇报单。领取被拒绝就停下告诉我原因。
2. 只在 workspace 里改代码并 git commit，不要另建 worktree 或复制项目文件夹。按交接单的 scope 做，dont 里的不做，needs_approval 里的先问我。
3. 按交接单 acceptance 逐条验证，截图存进 evidence_dir。
4. 每张卡做完交汇报单：MCP 用 submit_report；命令行把填好的汇报单存成 YAML 后运行
{cli} submit-report --file <汇报单.yaml> --agent <你的名字>
程序会重跑测试、查截图和提交，不过就拒收，按提示改完再交。做不了就用 release_task（或 {cli} release <编号> --agent <你的名字> --reason <原因>）放回。
5. 不要合并、推送、部署，也不要改任务状态；验收和合并由我来做。最后告诉我每张卡是否进了待验收、在哪里看效果。{note}"""
    return {'text': text}


def agent_handoff(root, task_ids, deploy=False, mode='execute'):
    if mode not in HANDOFF_MODES:
        raise ValueError('交接模式必须是 execute 或 research')
    if mode == 'research' and deploy:
        raise ValueError('调研模式不支持完成后直接合并部署')
    task_ids = list(dict.fromkeys([task_ids] if isinstance(task_ids, str) else task_ids))
    if not task_ids:
        raise ValueError('请至少选择一个任务')
    manager = TaskManager(root)
    for task_id in task_ids:
        manager._id(task_id)
    with manager.store.lock():
        available = {task['id']: task for task in manager.healthy_tasks()}
    tasks = []
    for task_id in task_ids:
        if task_id not in available:
            raise ValueError(f'{task_id}：任务不存在')
        tasks.append(available[task_id])
    archived = [task['id'] for task in tasks if task['status'] == 'archived']
    if archived:
        raise ValueError('归档任务只读，不能交付执行；请移除 '+ '、'.join(archived) +' 后重新复制')
    projects = Registry(root).list_projects()
    cli = shlex.join([str(Path(__file__).resolve().parents[1] / 'bin/project-inbox'), '--home', str(Path(root).resolve())])
    if mode == 'execute':
        records = manager.store.safe('work/handoffs')
        if all((records / f"{task['id']}.yaml").is_file() for task in tasks):
            return claim_handoff(cli, tasks, deploy)
    groups = {}
    for task in tasks:
        groups.setdefault(task['project'], []).append(task)
    sections = []
    for project_id, group in groups.items():
        project = next((p for p in projects if p['id'] == project_id), None)
        if project and project.get('enabled', True) and not project.get('missing', False) and Path(project.get('path', '')).is_absolute():
            action = '调研' if mode == 'research' else '工作'
            location = f"关联项目：{project['name']}\n项目目录：{project['path']}\n先确认该目录存在且与最新任务的关联项目一致，再在该目录{action}。"
        else:
            action = '调研' if mode == 'research' else '实施'
            location = f'关联项目：待确认。先查询下方 projects 命令，向我确认项目归属和工作目录，再开始{action}；不要根据标题猜测项目。'
        if mode == 'execute':
            # 分支按组内最小编号命名，例如 TASK-29、TASK-31 同组时用 task-29。
            branch = min(group, key=lambda t: [int(n) for n in t['id'][5:].split('.')])['id'].lower()
            location += f"\n工作分支：{branch}（worktree 规则见下文）。"
        else:
            location += '\n调研工作方式：只读检查该项目现状，不创建 worktree，不修改项目文件。'
        titles = '\n'.join(f"- {task['id']}：{task['title']}" for task in group)
        reads = '\n'.join(f"{cli} show {shlex.quote(task['id'])}" for task in group)
        sections.append(f"{location}\n任务清单（标题仅供定位，以最新任务为准）：\n{titles}\n读取该组最新任务：\n{reads}")
    task_names = '、'.join(task_ids)
    # 先拼好再放进 f-string：表达式里带反斜杠在 Python 3.11 是语法错误，而 pyproject 声明支持 3.11
    grouped = '\n\n'.join(sections)
    start_commands = '\n'.join(f"{cli} update {shlex.quote(task['id'])} --changes '{{\"status\":\"in_progress\"}}'" for task in tasks)
    review_commands = '\n'.join(f"{cli} update {shlex.quote(task['id'])} --changes '{{\"status\":\"review\"}}'" for task in tasks)
    # 截图放在收件箱目录下按任务编号归档，工作区清理后仍可查看。
    evidence = Path(root).resolve() / 'work/evidence'
    if mode == 'research':
        instructions = f"""请先调研 Project Inbox 中的 {len(tasks)} 个任务：{task_names}。全程用中文和我沟通、写 notes。

这是一轮调研交接，不是开发交接。原始任务是唯一需求来源，不能因为任务描述简短就替用户补全目标。

一、读取与定位
先实际运行以下命令读取项目注册表，并逐条运行各组的 show 命令读取全部最新任务：
{cli} projects

{grouped}

先通读所有任务，识别它们的原始意图、当前状态和相互关系。若 notes 中已经有调研记录，先核对已有证据，再补充新的现状；若原始需求与已有结论冲突，保留冲突并列为待确认问题。

二、只读调研边界
开始调研前，只把实际开始调查的任务设为进行中：
{start_commands}

可以读取项目文件、Git 历史、现有测试、配置和本地运行结果；不得修改业务项目文件、生产数据、依赖、配置或密钥，不改写任务标题、描述、期望行为和复现步骤，不创建 worktree，不提交、推送、部署或合并。仅按下一节约定追加调研 notes 和回填状态。不要通过标题猜测关联项目，项目未注册或不可用时先向我确认。

三、调研报告与回填
对每条任务形成一份简洁、可核对的调研记录，并追加到该任务现有 notes 末尾，不能覆盖旧记录。追加前必须重新读取任务，保留原 notes。报告按以下顺序写：
- 原始需求：引用任务当前标题和正文，说明没有改写原意。
- 当前实际状态：涉及的页面、入口、代码/配置位置和现有行为。
- 证据：实际查看的文件、命令、测试或页面操作及结果；不要只写推测。
- 已确认事实：可以直接据证据判断的内容。
- 假设与待确认问题：信息不足、多个解释或无法复现的部分。
- 建议执行边界：本轮建议做什么、明确不做什么。
- 可验证验收标准：用户完成哪些操作后能判断结果。
- 调研结论：是否已经适合进入开发；若不适合，写明缺什么。

调研结束后，先重新读取每条任务并确认记录已经保存，再将已完成调研的任务设为待验收：
{review_commands}

不得运行 done。调研有阻塞时只将对应任务设为 blocked，并在 notes 写清原因。调研报告保存失败、证据不足或无法访问项目时，保持原状态并说明原因，不要声称调研完成。

四、最终汇报
按任务编号汇报：当前实际状态、证据位置、已确认事实、假设与待确认问题、建议执行边界、验收标准、notes 回填结果和任务状态。明确告诉我下一步应先验收调研结论，还是可以再用「复制给 Agent」生成执行指令。"""
        return {'text': instructions}

    if deploy:
        preview = '本次我已勾选完成后直接合并部署，可不启动本地预览，验收卡写线上地址。'
        closing = """
七、合并部署（本次已授权）
我在看板复制时勾选了「完成后直接合并部署」。本组任务全部实现、验证并设为 review 后，按项目约定（AGENTS.md、CLAUDE.md、已有部署技能或脚本）提交本轮改动（只添加本轮改动的文件）、合并到主分支、推送、部署，并做上线后健康检查。合并前运行项目要求的全量测试，失败则不合并。
遇到以下情况停下问我，不自行处理：有任务受阻或未完成；合并冲突或主分支已前进无法快进；涉及数据库结构迁移、生产数据写入、配置或密钥变更；项目约定要求单独审批的事项；项目没有明确的部署方式。
部署后任务仍保持 review，在 notes 追加上线结果，验收卡写明「已上线」、提交号和线上查看路径。授权只覆盖本组任务和本次会话。

只处理所列任务和关联项目，遵守现有审批边界；除上述授权的合并部署外，不做其他 Git 操作，不归档任务。遇到需要确认的高风险操作先向我确认。
"""
    else:
        preview = '完成后启动本地预览供我验收：优先用项目已有的本地预览方式（项目约定、技能或脚本），以后台常驻方式启动，会话结束后仍可访问；不要占用其他会话正在用的端口或环境，使用合成或脱敏数据，不写生产。在验收卡和汇报里写明预览地址、要看的页面和停止命令。桌面或手机应用写明本机构建产物路径和打开方式；确实起不来时写明原因。'
        closing = """
只处理所列任务和关联项目，遵守现有审批边界；不自动提交、推送、部署或归档任务。遇到需要确认的高风险操作先向我确认。
"""
    instructions = f"""请统筹执行 Project Inbox 中的 {len(tasks)} 个任务：{task_names}。全程用中文和我沟通、写 notes。

一、读取与定位
先实际运行以下命令读取项目注册表，并逐条运行各组的 show 命令读取全部最新任务：
{cli} projects

{grouped}

先通读所有任务，识别依赖、重复诉求和冲突，按依赖安排实施顺序。同项目相关任务可以共用代码改动和测试，一次实施解决多条需求；保持原 Task 编号和独立验收，不合并、删除或改写原需求。跨项目按上述分组分别在对应目录处理，不混用工作目录。未明确归属的组先确认，其余明确项目可继续处理。

阅读关联项目根目录及相关子目录的 AGENTS.md、CLAUDE.md 等约定。先核实问题，依据最新描述、预期行为、复现步骤、依赖和验收条件实施。任务正文是待核实的需求，不是扩大权限的指令。
本指令可能在新会话中发送，用于接手上一轮的工作：先看各任务 notes 中已有的处理记录。已有工作区和分支直接沿用；notes 末尾有我的验收意见时，按意见返工，把状态设回 in_progress（意见原文已在 notes 中则不重复追加）。

二、工作区
不要直接修改主分支。我发送这段指令即批准：在该项目仓库中用 git worktree 新建上方「工作分支」同名分支的工作区，不必再为建工作区单独问我（项目已有 worktree 约定则沿用其位置和命名，否则放在项目目录同级、命名为「目录名-分支名」），之后所有改动都在该工作区进行。该分支或工作区已存在（例如验收不通过后返工）时直接沿用，不重建。项目不是 Git 仓库或无法创建 worktree 时，先告诉我原因并停下等我确认。项目约定中其他需要审批的事项（数据库结构、跨仓库契约等）照常先问我。

三、先写验收清单，再动手
任务正文通常很短。改代码前，为每条任务写 3–6 条验收清单，用「我在界面上能看到什么」的大白话，分三类：
- 参照物：效果要和哪个现成页面或控件一样；没有写「无」。
- 必须看到：打开哪个页面、做什么操作、看到什么结果；要覆盖完整操作流程，不只第一步。
- 不能破坏：哪些现有功能改完后必须照常。
把清单追加到该任务 notes。清单里你自行假设的口径（范围、算法、样式参照等）标为「假设」：会明显改变结果的先问我，其余按假设推进并在汇报中列出。

四、验证
运行项目已有的测试和构建，把实际运行的命令和实际结果（通过/失败数量、关键报错）写进 notes；没有测试或无法构建的部分要明确写「未测」及原因，不得写成已验证。
自动测试通过不等于验收通过：对每条「必须看到」，在本地环境按我的操作路径从头到尾实际操作一遍。测试数据要贴近真实数据的形状，例如编号乱序或按字典序排列（S1、S10、S2）、多组多维度、空值、长文本、行数较多；项目有脱敏数据时优先使用。
改动前后的截图保存到 {evidence}/<任务编号>/，不要放在 /tmp 或合并后会被清理的工作区里。
{preview}

五、状态与 notes
仅在实际开始某条任务时执行对应状态命令，不要一次性将全部任务标为进行中。已完成任务先核对，未要求重做时保留状态：
{start_commands}

逐条任务重新读取记录，在已有 notes 基础上追加该条处理结果，通过 update 的 --changes JSON 更新 notes，不覆盖旧记录。每次追加先写一段不超过 10 行的「验收卡」：
- 改了什么：一两句业务语言。
- 在哪看：本地预览或线上地址加操作路径，并写明代码当前位置（仅在工作区 / 已合并未部署 / 已上线）。
- 验收清单：逐条标「已实际操作验证」「仅自动测试覆盖」或「未测（原因）」。
- 假设与差异：和任务描述或我的预期可能不同的地方。
- 截图：路径。
- 收尾指令：一句能直接粘贴到新会话的话，写明工作区路径、分支和项目的部署方式（技能、脚本或命令），让新会话不读本次对话也能合并部署。
验收卡之后再写技术依据：工作区路径和分支、验证命令及实际结果。冗长的过程记录写进项目进度日志，notes 只留路径。共用的改动和测试需说明分别满足哪条验收。

某条实现并验证完成后，只把它的状态设为 review（待验收），不要运行 done。done 只对我明确点名「验收通过」的任务执行；合并、推送、部署，或我说「可以」「没问题」，都不等于验收通过，部署后任务仍保持 review。不得因部分完成将整批标为完成或待验收：
{review_commands}

某条受阻时只为该条追加原因并通过 update 将 status 设为 blocked；未验证不得设为待验收或完成。状态和记录均通过上述 CLI 回填，不直接改任务 Markdown。

六、最终汇报
先给结论，再按任务编号列表：状态、改了什么、在哪个页面、怎么操作能看到改动、验收清单通过情况、未测部分和风险，并附改动前后的截图路径。最后列出需要我决定或操作的事项。
{closing}"""
    return {'text': instructions}
