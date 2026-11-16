"""交接单 handoff/v1 与汇报单 report/v1 的固定格式。

这里只做检查和排版，不读写任何文件：MCP 工具和命令行都调用同一组函数，
所以不管哪个 agent 来交，缺字段都会被同样地退回。
"""
from difflib import get_close_matches
import re

HANDOFF_VERSION = 'handoff/v1'
REPORT_VERSION = 'report/v1'
KINDS = ['参照物', '必须看到', '不能破坏']
RESULTS = ['实际操作验证', '仅自动测试', '未测']
ACCEPTANCE_ID = re.compile(r'A[1-9][0-9]?\Z')
SCREENSHOT_NAME = re.compile(r'[A-Za-z0-9一-鿿][A-Za-z0-9_.\-一-鿿]{0,99}\Z')
MAX_TEXT = 4000
MAX_ITEM = 500
MAX_ITEMS = 20

HANDOFF_FIELDS = {
    'project': '哪个项目：已注册项目的 ID 或名称（list_projects 可查）',
    'title': '一行标题',
    'why': '为什么要做，一到三句',
    'goal': '做完后用户能看到什么',
    'scope': '{do: [这次要做的事，至少 1 条], dont: [这次不做的事，至少 1 条]}',
    'current_state': '{investigated: true/false, notes: 讨论时读过的代码和现在的行为；没读代码就 investigated: false}',
    'acceptance': '3 到 6 条验收清单：[{id: A1, kind: 参照物/必须看到/不能破坏, text: ...}]，至少 1 条必须看到、1 条不能破坏',
    'verify': '{tests: 测试命令（项目真没有测试就写 none）, screenshots: true/false}',
    'open_questions': '还没定的问题；不为空时卡片留在收件箱并标「待确认」，不会被领走',
    'decisions': '（可选）讨论中已经拍板的事',
    'needs_approval': '（可选）必须先问用户的事，例如数据库改动',
    'task_id': '（可选）把交接单补到已有卡片上，例如 TASK-7；不填就新建卡片',
}
REQUIRED_HANDOFF = ['project', 'title', 'why', 'goal', 'scope', 'current_state', 'acceptance', 'verify']

REPORT_FIELDS = {
    'task': '任务编号，例如 TASK-12',
    'outcome': 'done（做完了）或 blocked（卡住了）',
    'blocked_reason': 'outcome 是 blocked 时必填：卡在哪里、需要用户做什么',
    'summary': '改了什么，三句以内的大白话',
    'acceptance': '交接单里每条验收都要有结果：{A1: {result: 实际操作验证/仅自动测试/未测, note: ..., screenshot: ...}}；未测必须写 note 说明原因',
    'screenshots': '截图文件名列表，文件放在 work/evidence/<任务编号>/ 下',
    'visual_summary': '一张 Mermaid 流程图（flowchart/graph 开头），画出改动影响了哪条路径',
    'where_to_see': '预览地址和操作路径',
    'deviations': '和交接单不一样的地方；没有就写「无」',
}
REQUIRED_REPORT = ['task', 'outcome', 'summary', 'acceptance', 'where_to_see', 'deviations']

EXAMPLE_HANDOFF = {
    'project': 'projectinbox',
    'title': '看板卡片显示谁在做、做了多久',
    'why': '同时开好几个 agent 时，分不清哪张卡是谁在做、是不是卡住了。',
    'goal': '打开看板，进行中的卡片上直接看到负责的 agent 和已用时间。',
    'scope': {
        'do': ['进行中的卡片右下角显示 agent 名字和「已做 X 分钟」', '超过 2 小时没汇报的卡片显示橙色「可能卡住」'],
        'dont': ['不做自动重启 agent', '不改其他列卡片的样子'],
    },
    'current_state': {'investigated': True, 'notes': '卡片在看板补丁的 TaskCard.tsx 里；领取时间目前没有记录。'},
    'acceptance': [
        {'id': 'A1', 'kind': '参照物', 'text': '样式和卡片上现有的优先级小标签一样'},
        {'id': 'A2', 'kind': '必须看到', 'text': '领取 TASK-3 后刷新看板，卡片显示「Codex · 已做 1 分钟」'},
        {'id': 'A3', 'kind': '必须看到', 'text': '把领取时间改成 3 小时前，卡片出现橙色「可能卡住」'},
        {'id': 'A4', 'kind': '不能破坏', 'text': '勾选多张卡片「复制给 Agent」照常能用'},
    ],
    'decisions': ['超过 60 分钟显示成「X 小时」'],
    'open_questions': [],
    'verify': {'tests': 'uv run pytest && bun test', 'screenshots': True},
    'needs_approval': [],
}

EXAMPLE_REPORT = {
    'task': 'TASK-12',
    'outcome': 'done',
    'summary': '进行中的卡片右下角新增「谁在做、做了多久」小标签，超过 2 小时变橙色。',
    'acceptance': {
        'A1': {'result': '实际操作验证'},
        'A2': {'result': '实际操作验证', 'screenshot': 'a2-after.png'},
        'A3': {'result': '实际操作验证', 'screenshot': 'a3-stale.png'},
        'A4': {'result': '仅自动测试', 'note': '批量复制的测试通过'},
    },
    'screenshots': ['a2-before.png', 'a2-after.png', 'a3-stale.png'],
    'where_to_see': 'http://localhost:6420 → 任务看板 → 进行中一列',
    'deviations': '时间每分钟刷新一次，不是秒表。',
    'visual_summary': 'flowchart LR\n  领取 -->|记下 agent 和时间| 任务数据 --> 卡片右下角标签\n  任务数据 -->|超过 2 小时| 橙色提醒',
}


class ContractError(ValueError):
    """交接单或汇报单不合格；problems 是逐条可照着改的退回理由。"""

    def __init__(self, problems, kind='交接单'):
        self.problems = list(problems)
        super().__init__(f'{kind}被退回，请按以下提示补齐后重新提交：\n' + '\n'.join('- ' + p for p in self.problems))


def _blank(value):
    return value is None or isinstance(value, str) and not value.strip()


def _text(problems, data, key, limit=MAX_TEXT, single_line=False, label=None):
    label = label or key
    value = data.get(key)
    if _blank(value):
        problems.append(f'缺少 {label}：{HANDOFF_FIELDS.get(key) or REPORT_FIELDS.get(key) or "不能为空"}。')
        return ''
    if not isinstance(value, str):
        problems.append(f'{label} 必须是文字。')
        return ''
    value = value.strip()
    if '\x00' in value or len(value) > limit:
        problems.append(f'{label} 太长（上限 {limit} 字）。')
    if single_line and ('\n' in value or '\r' in value):
        problems.append(f'{label} 必须是一行。')
    return value


def _list(problems, value, label, minimum=0):
    if value is None:
        value = []
    if isinstance(value, str):
        value = [value] if value.strip() else []
    if not isinstance(value, list):
        problems.append(f'{label} 必须是列表。')
        return []
    items = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            problems.append(f'{label} 里每一条都必须是非空文字。')
            return []
        if len(item) > MAX_ITEM or '\x00' in item:
            problems.append(f'{label} 有一条太长（上限 {MAX_ITEM} 字）。')
            return []
        items.append(item.strip())
    if len(items) > MAX_ITEMS:
        problems.append(f'{label} 最多 {MAX_ITEMS} 条。')
    if len(items) < minimum:
        problems.append(f'{label} 至少写 {minimum} 条。')
    return items


def _project(problems, value, projects):
    if _blank(value) or not isinstance(value, str):
        problems.append('缺少 project：写已注册项目的 ID 或名称（list_projects 可查）。')
        return None
    wanted = value.strip().casefold()
    for p in projects:
        names = [p['id'], p.get('name', '')] + list(p.get('aliases', []))
        if wanted in [str(n).casefold() for n in names if n]:
            if p.get('missing') or not p.get('enabled', True):
                problems.append(f'project「{value}」已停用或目录不存在，请先确认项目。')
            return p
    names = sorted({str(n) for p in projects for n in [p['id'], p.get('name', '')] if n})
    close = get_close_matches(value, names, n=3, cutoff=0.3) or names[:5]
    problems.append(f'project「{value}」未注册。候选：{"、".join(close) if close else "（注册表为空，先运行 project-inbox refresh）"}。')
    return None


def validate_handoff(data, projects):
    """检查交接单；合格时返回整理好的字典，不合格抛出 ContractError。"""
    if not isinstance(data, dict):
        raise ContractError(['交接单必须是一个对象（YAML 或 JSON 的键值表）。'])
    problems = []
    unknown = sorted(str(k) for k in set(data) - set(HANDOFF_FIELDS) - {'version'})
    if unknown:
        problems.append('不认识的字段：' + '、'.join(unknown) + '。可用字段见 get_handoff_template。')
    if data.get('version') not in [None, HANDOFF_VERSION]:
        problems.append(f'version 只能是 {HANDOFF_VERSION}。')
    project = _project(problems, data.get('project'), projects)
    out = {'version': HANDOFF_VERSION, 'project': project['id'] if project else None}
    out['title'] = _text(problems, data, 'title', 200, single_line=True)
    out['why'] = _text(problems, data, 'why')
    out['goal'] = _text(problems, data, 'goal')

    scope = data.get('scope')
    if not isinstance(scope, dict):
        problems.append('缺少 scope：写成 {do: [...], dont: [...]}。')
        scope = {}
    elif set(scope) - {'do', 'dont'}:
        problems.append('scope 只能有 do 和 dont 两项。')
    do = _list(problems, scope.get('do'), 'scope.do')
    if not do and isinstance(data.get('scope'), dict):
        problems.append('缺少 scope.do：至少写一条「这次要做」的事。')
    dont = _list(problems, scope.get('dont'), 'scope.dont')
    if not dont and isinstance(data.get('scope'), dict):
        problems.append('缺少 scope.dont：至少写一条「这次不做」的事，防止越做越多。')
    out['scope'] = {'do': do, 'dont': dont}

    state = data.get('current_state')
    if not isinstance(state, dict) or not isinstance(state.get('investigated'), bool):
        problems.append('current_state 必须明确选择：{investigated: true, notes: 读过的代码和现在的行为} 或 {investigated: false}。')
        out['current_state'] = {'investigated': False, 'notes': ''}
    else:
        notes = state.get('notes') or ''
        if not isinstance(notes, str) or len(notes) > MAX_TEXT:
            problems.append('current_state.notes 必须是不超过 4000 字的文字。')
            notes = ''
        if state['investigated'] and not notes.strip():
            problems.append('current_state.investigated 为 true 时，notes 要写读过哪些代码、现在是什么行为。')
        out['current_state'] = {'investigated': state['investigated'], 'notes': notes.strip() or ('' if state['investigated'] else '未调查')}

    acceptance = data.get('acceptance')
    items = []
    if not isinstance(acceptance, list):
        problems.append('缺少 acceptance：写 3 到 6 条验收清单，每条 {id: A1, kind: 必须看到, text: ...}。')
    else:
        if len(acceptance) < 3:
            problems.append(f'acceptance 只有 {len(acceptance)} 条，至少 3 条。')
        if len(acceptance) > 6:
            problems.append(f'acceptance 有 {len(acceptance)} 条，超过 6 条：这张卡太大，请拆成两张分别提交。')
        seen = set()
        for index, item in enumerate(acceptance, 1):
            if not isinstance(item, dict):
                problems.append(f'acceptance 第 {index} 条必须是 {{id, kind, text}}。')
                continue
            ident = str(item.get('id') or f'A{index}').strip().upper()
            kind = item.get('kind')
            body = item.get('text')
            if not ACCEPTANCE_ID.fullmatch(ident):
                problems.append(f'acceptance 第 {index} 条的 id「{ident}」格式不对，用 A1、A2……')
            elif ident in seen:
                problems.append(f'acceptance 编号 {ident} 重复。')
            seen.add(ident)
            if kind not in KINDS:
                problems.append(f'acceptance {ident} 的 kind 只能是：{"、".join(KINDS)}。')
                kind = None
            if _blank(body) or not isinstance(body, str):
                problems.append(f'acceptance {ident} 缺少 text：写清打开哪里、做什么、看到什么。')
                body = ''
            elif len(body) > MAX_ITEM or '\n' in body.strip():
                problems.append(f'acceptance {ident} 的 text 必须是一行、不超过 {MAX_ITEM} 字。')
            items.append({'id': ident, 'kind': kind, 'text': (body or '').strip()})
        kinds = {i['kind'] for i in items}
        if acceptance and '必须看到' not in kinds:
            problems.append('acceptance 里没有「必须看到」：至少写一条，说明打开哪里、做什么、看到什么。')
        if acceptance and '不能破坏' not in kinds:
            problems.append('acceptance 里没有「不能破坏」：至少写一条改完后必须照常的现有功能。')
    out['acceptance'] = items

    verify = data.get('verify')
    if not isinstance(verify, dict):
        problems.append('缺少 verify：写成 {tests: 测试命令, screenshots: true/false}；项目真没有测试就写 tests: none。')
        verify = {}
    elif set(verify) - {'tests', 'screenshots'}:
        problems.append('verify 只能有 tests 和 screenshots 两项。')
    tests = verify.get('tests') if isinstance(verify, dict) else None
    if _blank(tests) or not isinstance(tests, str):
        if isinstance(data.get('verify'), dict):
            problems.append('verify.tests 必须填测试命令；项目真没有测试就写 none（汇报时会被标出来）。')
        tests = ''
    elif len(tests) > 1000 or '\n' in tests.strip() or '\x00' in tests:
        problems.append('verify.tests 必须是一行、不超过 1000 字的命令。')
    screenshots = verify.get('screenshots', False) if isinstance(verify, dict) else False
    if not isinstance(screenshots, bool):
        problems.append('verify.screenshots 只能是 true 或 false。')
        screenshots = False
    out['verify'] = {'tests': (tests or '').strip(), 'screenshots': screenshots}

    out['open_questions'] = _list(problems, data.get('open_questions'), 'open_questions')
    out['decisions'] = _list(problems, data.get('decisions'), 'decisions')
    out['needs_approval'] = _list(problems, data.get('needs_approval'), 'needs_approval')
    task_id = data.get('task_id')
    if task_id is not None:
        if not isinstance(task_id, str) or not re.fullmatch(r'TASK-[1-9][0-9]*(?:\.[1-9][0-9]*)*', task_id.strip().upper()):
            problems.append('task_id 格式不对，例如 TASK-7；新建卡片就不要填。')
        else:
            out['task_id'] = task_id.strip().upper()
    if problems:
        raise ContractError(problems)
    return out


def handoff_template():
    blank = {
        'project': '', 'title': '', 'why': '', 'goal': '',
        'scope': {'do': [''], 'dont': ['']},
        'current_state': {'investigated': False, 'notes': ''},
        'acceptance': [{'id': 'A1', 'kind': '参照物', 'text': ''}, {'id': 'A2', 'kind': '必须看到', 'text': ''}, {'id': 'A3', 'kind': '不能破坏', 'text': ''}],
        'verify': {'tests': '', 'screenshots': False},
        'open_questions': [], 'decisions': [], 'needs_approval': [],
    }
    return {
        'version': HANDOFF_VERSION,
        'fields': HANDOFF_FIELDS,
        'required': REQUIRED_HANDOFF,
        'blank': blank,
        'example': EXAMPLE_HANDOFF,
        'rules': [
            '只根据本次对话填写；没聊到、没定下来的写进 open_questions，不要自己编。',
            'acceptance 用「我在界面上能看到什么」的大白话。',
            '填好后调用 submit_handoff；被退回就按提示补齐再交。',
        ],
    }


def acceptance_lines(handoff):
    """写进 Backlog.md 验收勾选框的文字，编号放在最前面方便汇报时对应。"""
    return [f"[{a['id']}][{a['kind']}] {a['text']}" for a in handoff['acceptance']]


def render_plan(handoff, author, submitted_at):
    lines = [f'### 交接单 {HANDOFF_VERSION}', f'由 {author} 于 {submitted_at} 提交。', '',
             '**为什么做：** ' + handoff['why'], '', '**做完能看到：** ' + handoff['goal'], '', '**这次要做：**']
    lines += ['- ' + x for x in handoff['scope']['do']]
    lines += ['', '**这次不做：**'] + ['- ' + x for x in handoff['scope']['dont']]
    state = handoff['current_state']
    lines += ['', '**现状：** ' + ('已调查。' + state['notes'] if state['investigated'] else '未调查。')]
    if handoff['decisions']:
        lines += ['', '**已拍板：**'] + ['- ' + x for x in handoff['decisions']]
    if handoff['needs_approval']:
        lines += ['', '**必须先问用户：**'] + ['- ' + x for x in handoff['needs_approval']]
    if handoff['open_questions']:
        lines += ['', '**待确认（未确认前不能领取）：**'] + ['- ' + x for x in handoff['open_questions']]
    tests = handoff['verify']['tests']
    lines += ['', '**怎么验证：** 测试命令 `' + tests + '`' + ('；需要截图' if handoff['verify']['screenshots'] else '；不需要截图')]
    return '\n'.join(lines)


def validate_report(data, handoff, task_id):
    """检查汇报单的格子；文件、提交、测试这类事实由 checks.py 另行核验。"""
    if not isinstance(data, dict):
        raise ContractError(['汇报单必须是一个对象（YAML 或 JSON 的键值表）。'], '汇报单')
    problems = []
    unknown = sorted(str(k) for k in set(data) - set(REPORT_FIELDS) - {'version'})
    if unknown:
        problems.append('不认识的字段：' + '、'.join(unknown) + '。可用字段见 get_report_template。')
    if data.get('version') not in [None, REPORT_VERSION]:
        problems.append(f'version 只能是 {REPORT_VERSION}。')
    task = str(data.get('task') or '').strip().upper()
    if task != task_id:
        problems.append(f'task 必须是 {task_id}。')
    outcome = data.get('outcome')
    if outcome not in ['done', 'blocked']:
        problems.append('outcome 只能是 done 或 blocked。')
    out = {'version': REPORT_VERSION, 'task': task_id, 'outcome': outcome}
    if outcome == 'blocked':
        out['blocked_reason'] = _text(problems, data, 'blocked_reason', label='blocked_reason')
    out['summary'] = _text(problems, data, 'summary', 1000)
    out['where_to_see'] = _text(problems, data, 'where_to_see', 1000)
    out['deviations'] = _text(problems, data, 'deviations', 2000)

    results = data.get('acceptance')
    wanted = [a['id'] for a in handoff['acceptance']]
    out['acceptance'] = {}
    if not isinstance(results, dict):
        problems.append('缺少 acceptance：交接单里的 ' + '、'.join(wanted) + ' 每条都要写结果。')
        results = {}
    extra = sorted(set(str(k).upper() for k in results) - set(wanted))
    if extra:
        problems.append('acceptance 里有交接单没有的编号：' + '、'.join(extra) + '。')
    normalized = {str(k).upper(): v for k, v in results.items()}
    for ident in wanted:
        item = normalized.get(ident)
        if item is None:
            problems.append(f'acceptance 漏了 {ident}：每条验收都要有结果（{"、".join(RESULTS)}）。')
            continue
        if isinstance(item, str):
            item = {'result': item}
        if not isinstance(item, dict) or item.get('result') not in RESULTS:
            problems.append(f'acceptance {ident} 的 result 只能是：{"、".join(RESULTS)}。')
            continue
        note = item.get('note') or ''
        shot = item.get('screenshot') or ''
        if not isinstance(note, str) or len(note) > MAX_ITEM or not isinstance(shot, str):
            problems.append(f'acceptance {ident} 的 note、screenshot 必须是短文字。')
            continue
        if item['result'] == '未测' and not note.strip():
            problems.append(f'acceptance {ident} 标了「未测」，note 必须写原因。')
        out['acceptance'][ident] = {'result': item['result'], 'note': note.strip(), 'screenshot': shot.strip()}

    shots = _list(problems, data.get('screenshots'), 'screenshots')
    for name in shots + [x['screenshot'] for x in out['acceptance'].values() if x['screenshot']]:
        if not SCREENSHOT_NAME.fullmatch(name):
            problems.append(f'截图「{name}」只写文件名（不要路径），文件放在 work/evidence/{task_id}/ 下。')
    out['screenshots'] = list(dict.fromkeys(shots + [x['screenshot'] for x in out['acceptance'].values() if x['screenshot']]))
    if outcome == 'done' and handoff['verify']['screenshots'] and not out['screenshots']:
        problems.append('交接单要求截图：screenshots 至少写一个文件名，文件放在 work/evidence/' + task_id + '/ 下。')

    visual = data.get('visual_summary') or ''
    if not isinstance(visual, str) or len(visual) > MAX_TEXT:
        problems.append('visual_summary 必须是不超过 4000 字的 Mermaid 文字。')
        visual = ''
    visual = visual.strip()
    if visual.startswith('```'):
        visual = re.sub(r'^```(?:mermaid)?\s*|\s*```$', '', visual).strip()
    if outcome == 'done':
        if not visual:
            problems.append('缺少 visual_summary：画一张 Mermaid 流程图（以 flowchart 或 graph 开头），说明改动影响了哪条路径。')
        elif not re.match(r'(flowchart|graph|sequenceDiagram|stateDiagram)', visual):
            problems.append('visual_summary 要以 flowchart、graph、sequenceDiagram 或 stateDiagram 开头。')
    out['visual_summary'] = visual
    if problems:
        raise ContractError(problems, '汇报单')
    return out


def report_template(handoff, task_id):
    blank = {
        'task': task_id, 'outcome': 'done', 'summary': '',
        'acceptance': {a['id']: {'result': '', 'note': '', 'screenshot': ''} for a in handoff['acceptance']},
        'screenshots': [], 'visual_summary': 'flowchart LR\n  A --> B', 'where_to_see': '', 'deviations': '无',
    }
    return {'version': REPORT_VERSION, 'fields': REPORT_FIELDS, 'required': REQUIRED_REPORT, 'blank': blank, 'example': EXAMPLE_REPORT}


def render_summary(report, checks, reviewer_note=''):
    """汇报单 + 程序核验结果，写进卡片的「最终总结」。"""
    lines = ['### 汇报单 ' + REPORT_VERSION]
    if report['outcome'] == 'blocked':
        lines += ['**受阻：** ' + report['blocked_reason']]
    lines += ['**改了什么：** ' + report['summary'], '', '**在哪看：** ' + report['where_to_see'], '', '**验收结果：**']
    for ident, item in report['acceptance'].items():
        extra = '；'.join(x for x in [item['note'], ('截图 ' + item['screenshot']) if item['screenshot'] else ''] if x)
        lines.append(f"- {ident}：{item['result']}" + (f'（{extra}）' if extra else ''))
    lines += ['', '**和交接单的差异：** ' + report['deviations']]
    if report['screenshots']:
        lines += ['', '**截图：** ' + '、'.join(report['screenshots'])]
    if checks:
        lines += ['', '**程序核验：**'] + ['- ' + c for c in checks]
    if reviewer_note:
        lines += ['', reviewer_note]
    if report['visual_summary']:
        lines += ['', '```mermaid', report['visual_summary'], '```']
    return '\n'.join(lines)
