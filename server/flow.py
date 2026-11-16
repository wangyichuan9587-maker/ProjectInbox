"""任务状态机：所有「门」都在这里。

agent 能走的门：submit_handoff（交接单）、claim_tasks（领取）、submit_report（汇报）、release_task（放回）。
只有用户能走的门：accept（验收通过）、reject（退回）、cancel（取消），只在命令行里，不进 MCP。
每扇门进门前先检查；记录存在 work/ 下，卡片内容仍全部经 Backlog.md 写入。
"""
from datetime import datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo
import yaml
from .backlog import STATES
from .contract import (ContractError, acceptance_lines, render_plan, render_summary, report_template, validate_handoff, validate_report)
from . import checks
from . import workspace as ws
from .task_manager import TaskManager

AGENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.\-]{0,39}\Z')
HANDOFF_LABEL = '交接单'
WAITING_LABEL = '待确认'
REJECTED_LABEL = '已退回'
CANCELLED_LABEL = '已取消'
FLOW_LABELS = {HANDOFF_LABEL, WAITING_LABEL, REJECTED_LABEL, CANCELLED_LABEL}
PRIORITY_ORDER = {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}
CLAIM_RULES = [
    '只在 workspace 这个文件夹里改代码，不要自己另建 worktree、不要复制项目文件夹。',
    '按交接单的 scope 做，dont 里的事不要做；needs_approval 里的事先问用户。',
    '改完在 workspace 里 git commit（程序只认任务分支上的提交）。',
    '截图存到 evidence_dir，文件名写进汇报单。',
    '做完用 submit_report 交汇报单；被拒收就按提示改完再交。做不了就 release_task 放回并写原因。',
    '不要合并、推送、部署；验收和合并由用户来做。',
]


def _id_key(task_id):
    return [int(n) for n in task_id[5:].split('.')]


class Flow:
    def __init__(self, root):
        self.tasks = TaskManager(root)
        self.store = self.tasks.store
        self.root = self.tasks.root
        self.registry = self.tasks.registry

    # ---------- 记录 ----------
    def _now(self):
        return datetime.now(ZoneInfo(self.registry.settings().get('timezone', 'America/Los_Angeles'))).isoformat(timespec='seconds')

    def _load(self, kind, task_id):
        return self.store.yaml(f'work/{kind}/{task_id}.yaml', None)

    def _save(self, kind, task_id, data):
        self.store.write(f'work/{kind}/{task_id}.yaml', yaml.safe_dump(data, allow_unicode=True, sort_keys=False), replace=True)

    def evidence_dir(self, task_id):
        return self.store.mkdir(f'work/evidence/{task_id}')

    def handoff(self, task_id):
        return self._load('handoffs', task_id)

    def claim(self, task_id):
        return self._load('claims', task_id)

    def _agent(self, agent):
        if not isinstance(agent, str) or not AGENT.fullmatch(agent):
            raise ValueError('agent 名字只能用字母、数字、点、横线、下划线，最多 40 个字符，例如 codex、claude-code')
        return agent

    def _edit(self, task_id, *args):
        self.tasks.backend.run('task', 'edit', task_id, '--plain', *args)

    def _note(self, task_id, text):
        self._edit(task_id, '--append-notes', f'[{self._now()}] {text}')

    def _project(self, project_id):
        project = next((p for p in self.registry.list_projects() if p['id'] == project_id), None)
        if project is None:
            raise ValueError(f'项目 {project_id} 不在注册表里')
        return project

    # ---------- 交接单 ----------
    def submit_handoff(self, data, author='agent'):
        author = self._agent(author)
        handoff = validate_handoff(data, self.registry.list_projects())
        project = self._project(handoff['project'])
        waiting = bool(handoff['open_questions'])
        status = 'inbox' if waiting else 'todo'
        description = '**为什么做：** ' + handoff['why'] + '\n\n**做完能看到：** ' + handoff['goal']
        with self.store.lock():
            if handoff.get('task_id'):
                task_id = handoff['task_id']
                task = self.tasks.get_task(task_id, _locked=True)
                if task['status'] not in ['inbox', 'todo', 'blocked']:
                    raise ContractError([f'{task_id} 现在是 {task["status"]}，只能给收件箱、待办或受阻的卡补交接单。'])
                claim = self.claim(task_id)
                if claim and claim.get('active'):
                    raise ContractError([f'{task_id} 已被 {claim["agent"]} 领取，不能再改交接单。'])
                args = ['--status', STATES[status], '--project', project['name']] + [x for line in acceptance_lines(handoff) for x in ['--acceptance-criteria', line]]
                self._edit(task_id, '--title', handoff['title'], '--description', description, *args)
                self._edit(task_id, '--add-label', HANDOFF_LABEL + (',' + WAITING_LABEL if waiting else ''))
                if not waiting and WAITING_LABEL in task['tags']:
                    self._edit(task_id, '--remove-label', WAITING_LABEL)
            else:
                draft = dict(title=handoff['title'], type='feature', status=status, priority='P2', tags=[HANDOFF_LABEL] + ([WAITING_LABEL] if waiting else []), description=description)
                self.tasks._validate(dict(draft, notes='', expected_behavior='', reproduction_steps='', commits=[]))
                extra = [x for line in acceptance_lines(handoff) for x in ['--ac', line]]
                task_id = self.tasks._create(draft, project, extra)
            submitted_at = self._now()
            self._edit(task_id, '--plan', render_plan(handoff, author, submitted_at))
            record = dict(handoff, task_id=task_id, author=author, submitted_at=submitted_at)
            self._save('handoffs', task_id, record)
            self._note(task_id, f'交接单由 {author} 提交' + ('，有待确认问题，留在收件箱。' if waiting else '，进入待办，可以领取。'))
            task = self.tasks.get_task(task_id, _locked=True)
        message = f'{task_id} 已建好。' + (f'还有 {len(handoff["open_questions"])} 个待确认问题，卡片留在收件箱并标了「待确认」；确认后带上 task_id 重新提交交接单即可。' if waiting else '已进入待办，执行会话可以领取。')
        return dict(task_id=task_id, status=task['status'], waiting_for_confirmation=waiting, open_questions=handoff['open_questions'], message=message)

    # ---------- 领取 ----------
    def _claimable_reason(self, task):
        if task['status'] == 'archived':
            return '已归档'
        if task['status'] == 'in_progress':
            claim = self.claim(task['id'])
            return f'已被 {claim["agent"]} 于 {claim["claimed_at"]} 领取' if claim and claim.get('active') else '已在进行中'
        if task['status'] != 'todo':
            return f'状态是 {task["status"]}，只有待办（todo）里的卡能领取'
        handoff = self.handoff(task['id'])
        if not handoff:
            return '没有合格的交接单，先用 submit_handoff 补交接单（带 task_id）'
        if handoff.get('open_questions'):
            return '交接单还有待确认问题'
        return ''

    def claim_tasks(self, agent, task_ids=None, project_id=None, limit=1):
        agent = self._agent(agent)
        if not isinstance(limit, int) or not 1 <= limit <= 5:
            raise ValueError('一次最多领 5 张卡')
        if task_ids is not None:
            task_ids = list(dict.fromkeys([task_ids] if isinstance(task_ids, str) else task_ids))
            for task_id in task_ids:
                self.tasks._id(task_id)
            if not task_ids:
                raise ValueError('task_ids 不能为空；想按项目领就只传 project_id')
        project = self.tasks._project(project_id) if project_id else None
        claimed, refused = [], []
        with self.store.lock():
            tasks = {t['id']: t for t in self.tasks.healthy_tasks()}
            if task_ids is None:
                pool = [t for t in tasks.values() if (not project or t['project'] == project['id']) and not self._claimable_reason(t)]
                pool.sort(key=lambda t: (PRIORITY_ORDER.get(t['priority'], 9), _id_key(t['id'])))
                chosen = [t['id'] for t in pool[:limit]]
                if not chosen:
                    return dict(claimed=[], refused=[], message='没有可领取的卡（待办里、带合格交接单、没有待确认问题）。')
            else:
                chosen = task_ids
            for task_id in chosen:
                task = tasks.get(task_id)
                reason = '任务不存在' if task is None else self._claimable_reason(task)
                if reason:
                    refused.append(dict(task_id=task_id, reason=reason))
                    continue
                handoff = self.handoff(task_id)
                try:
                    proj = self._project(handoff['project'])
                    if proj.get('missing') or not proj.get('enabled', True):
                        raise ValueError('项目已停用或目录不存在')
                    previous = self.claim(task_id) or {}
                    inherit = handoff.get('inherit') or (previous if previous.get('workspace') else None)
                    space = ws.ensure_worktree(proj['path'], task_id, inherit)
                except (ValueError, OSError) as error:
                    refused.append(dict(task_id=task_id, reason='建工作区失败：' + str(error)[:300]))
                    continue
                now = self._now()
                record = dict(task_id=task_id, agent=agent, claimed_at=now, active=True, project=proj['id'], **{k: space[k] for k in ['repo', 'workspace', 'branch', 'base_branch', 'base_commit']}, reported_head=previous.get('reported_head', ''), history=previous.get('history', []) + [dict(event='claim', agent=agent, at=now)])
                # 先改卡片状态再记领取：状态改失败时卡片仍在待办、也没有领取记录，不会出现两个会话同时领到
                self._edit(task_id, '--status', STATES['in_progress'], '--assignee', '@' + agent)
                self._save('claims', task_id, record)
                self._note(task_id, f'{agent} 领取。工作区 {space["workspace"]}（分支 {space["branch"]}，{"沿用已有" if space["reused"] else "新建"}）。')
                claimed.append(dict(task_id=task_id, title=task['title'], project=proj['id'], workspace=space['workspace'], branch=space['branch'], base_branch=space['base_branch'], evidence_dir=str(self.evidence_dir(task_id)), handoff={k: v for k, v in handoff.items() if k not in ['inherit']}, rules=CLAIM_RULES, report_template=report_template(handoff, task_id)['blank']))
        return dict(claimed=claimed, refused=refused, message=f'领到 {len(claimed)} 张卡' + (f'，{len(refused)} 张没领到' if refused else '') + '。')

    def _active_claim(self, task_id, agent=None):
        task = self.tasks.get_task(task_id, _locked=True)
        claim = self.claim(task_id)
        if task['status'] != 'in_progress' or not claim or not claim.get('active'):
            raise ValueError(f'{task_id} 不在进行中，或不是通过 claim_tasks 领取的')
        if agent and claim['agent'] != agent:
            raise ValueError(f'{task_id} 是 {claim["agent"]} 领取的，不是 {agent}')
        return task, claim

    def release_task(self, task_id, reason, agent=None):
        self.tasks._id(task_id)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('放回必须写原因（不超过 2000 字）')
        with self.store.lock():
            _, claim = self._active_claim(task_id, agent)
            now = self._now()
            claim.update(active=False, history=claim.get('history', []) + [dict(event='release', agent=claim['agent'], at=now, reason=reason.strip())])
            self._save('claims', task_id, claim)
            self._edit(task_id, '--status', STATES['todo'], '--assignee', '')
            self._note(task_id, f'{claim["agent"]} 放回待办，原因：{reason.strip()}。工作区保留，下次领取沿用。')
        return dict(task_id=task_id, status='todo', workspace=claim['workspace'])

    # ---------- 汇报 ----------
    def report_template(self, task_id):
        self.tasks._id(task_id)
        handoff = self.handoff(task_id)
        if not handoff:
            raise ValueError(f'{task_id} 没有交接单')
        return report_template(handoff, task_id)

    def submit_report(self, data, agent=None):
        if not isinstance(data, dict):
            raise ContractError(['汇报单必须是一个对象（YAML 或 JSON 的键值表）。'], '汇报单')
        task_id = str(data.get('task') or '').strip().upper()
        self.tasks._id(task_id)
        with self.store.lock():
            task, claim = self._active_claim(task_id, agent)
            handoff = self.handoff(task_id)
            report = validate_report(data, handoff, task_id)
        evidence_dir = self.evidence_dir(task_id)
        facts, problems, test = {}, [], None
        if report['outcome'] == 'done':
            problems += checks.evidence(evidence_dir, report['screenshots'], handoff['verify']['screenshots'])
            git_problems, facts = checks.commits(claim['workspace'], claim['base_commit'], claim.get('reported_head', ''), claim['branch'], claim.get('base_branch', ''))
            problems += git_problems
            if not git_problems:
                timeout = int(self.registry.settings().get('verify_timeout_seconds', 900))
                passed, output, seconds = checks.run_tests(claim['workspace'], handoff['verify']['tests'], timeout)
                test = dict(command=handoff['verify']['tests'], passed=passed, seconds=seconds, output=output)
                if passed is False:
                    problems.append(f'测试没过（{handoff["verify"]["tests"]}），报错见 test_output。')
        now = self._now()
        with self.store.lock():
            task, current = self._active_claim(task_id, agent)
            if current.get('claimed_at') != claim.get('claimed_at'):
                raise ValueError(f'{task_id} 在核验期间被重新领取，请重新汇报')
            if problems:
                self._note(task_id, '汇报被拒收：' + '；'.join(problems))
                return dict(task_id=task_id, accepted=False, status='in_progress', problems=problems, test_output=(test or {}).get('output', ''))
            verified = []
            if report['outcome'] == 'done':
                verified.append(f"任务分支 {claim['branch']} 上有 {len(facts['commits'])} 个新提交，工作区没有未提交的改动（{facts['head'][:8]}）。")
                if test['passed'] is None:
                    verified.append('项目没有测试（交接单写的 tests: none），程序没有重跑测试。')
                else:
                    verified.append(f"程序在工作区重跑 `{test['command']}` 通过，用时 {test['seconds']} 秒。")
                if report['screenshots']:
                    verified.append(f"{len(report['screenshots'])} 张截图都在 {evidence_dir}。")
            status = 'review' if report['outcome'] == 'done' else 'blocked'
            summary = render_summary(report, verified)
            args = ['--status', STATES[status], '--final-summary', summary]
            if report['outcome'] == 'done':
                lines = [a['text'] for a in task['acceptance']]
                for ident, item in report['acceptance'].items():
                    if item['result'] == '未测':
                        continue
                    index = next((a['index'] for a in task['acceptance'] if a['text'].startswith(f'[{ident}]')), None)
                    if index:
                        args += ['--check-ac', str(index)]
            self._edit(task_id, *args)
            record = dict(report, submitted_at=now, agent=claim['agent'], verified=verified, test=test, head=facts.get('head', ''))
            self._save('reports', task_id, record)
            current.update(reported_head=facts.get('head', current.get('reported_head', '')), history=current.get('history', []) + [dict(event='report', outcome=report['outcome'], at=now)])
            if status == 'blocked':
                # 受阻后领取结束：用户补交接单（带 task_id）回到待办后可重新领取，工作区保留沿用
                current['active'] = False
            self._save('claims', task_id, current)
            self._note(task_id, f"{claim['agent']} 交了汇报单（{report['outcome']}），" + ('核验通过，进入待验收。' if status == 'review' else '标为受阻：' + report['blocked_reason']))
        return dict(task_id=task_id, accepted=True, status=status, verified=verified)

    # ---------- 只有用户能走的门 ----------
    def accept(self, task_id, merge=True):
        self.tasks._id(task_id)
        with self.store.lock():
            task = self.tasks.get_task(task_id, _locked=True)
            claim = self.claim(task_id)
            # 用 done 标完成但没合并的卡，之后仍可用 accept 补做合并
            merge_later = merge and task['status'] == 'done' and claim and claim.get('workspace') and any(h.get('event') == 'accept' and not h.get('merged') for h in claim.get('history', [])) and not any(h.get('event') == 'accept' and h.get('merged') for h in claim.get('history', []))
            if task['status'] != 'review' and not merge_later:
                raise ValueError(f'{task_id} 现在是 {task["status"]}，只有待验收（review）的卡能验收通过')
            result = dict(task_id=task_id, status='done', merged=False, workspace_removed=False)
            if claim and claim.get('workspace') and merge:
                facts = ws.state(claim['workspace'], claim['base_commit'])
                if facts['exists'] and facts['dirty']:
                    raise ValueError(f'工作区 {claim["workspace"]} 里有没提交的改动，先处理再验收')
                if facts['exists'] and facts['branch'] != claim['branch']:
                    raise ValueError(f'工作区 {claim["workspace"]} 现在在 {facts["branch"] or "（不在任何分支上）"}，不是任务分支 {claim["branch"]}，先切回再验收')
                # 程序核验（重跑测试、查提交）的是汇报那一刻的提交；之后加到任务分支上的提交没人查过，不能跟着一起合并
                reported, tip = claim.get('reported_head'), ws.branch_head(claim['repo'], claim['branch'])
                if reported and tip and tip != reported:
                    raise ValueError(f'任务分支 {claim["branch"]} 在汇报之后又有了新提交（汇报时 {reported[:8]}，现在 {tip[:8]}），这些提交没有经过程序核验。可以 reject 退回，让 agent 重新汇报；确认无误想直接用，就自己合并后运行 project-inbox accept {task_id} --no-merge')
                result['merged_head'] = ws.merge_fast_forward(claim['repo'], claim['branch'], claim['base_branch'])
                result['merged'] = True
                # 合并已经完成，删工作区失败不能让卡片停在待验收：照常标完成，把原因写进结果和备注
                try:
                    result['workspace_removed'] = ws.remove_worktree(claim['repo'], claim['workspace'])
                except ValueError as error:
                    result['warning'] = str(error)
            now = self._now()
            if claim:
                claim.update(active=False, history=claim.get('history', []) + [dict(event='accept', at=now, merged=result['merged'])])
                self._save('claims', task_id, claim)
            if task['status'] != 'done':
                self._edit(task_id, '--status', STATES['done'])
            if not result['merged']:
                merged_text = '未做合并。'
            elif not result.get('warning'):
                merged_text = f"已快进合并到 {claim['base_branch']}（{result['merged_head'][:8]}），工作区已删除，分支 {claim['branch']} 保留。"
            else:
                merged_text = f"已快进合并到 {claim['base_branch']}（{result['merged_head'][:8]}），工作区未删除：{result['warning']}"
            self._note(task_id, '用户验收通过。' + merged_text)
        return result

    def reject(self, task_id, comment):
        self.tasks._id(task_id)
        if not isinstance(comment, str) or not comment.strip() or len(comment) > 2000:
            raise ValueError('退回必须写一句意见（不超过 2000 字）')
        comment = comment.strip()
        with self.store.lock():
            task = self.tasks.get_task(task_id, _locked=True)
            if task['status'] not in ['review', 'blocked']:
                raise ValueError(f'{task_id} 现在是 {task["status"]}，只有待验收或受阻的卡能退回')
            handoff = self.handoff(task_id)
            if not handoff:
                raise ValueError(f'{task_id} 没有交接单，没法生成返工卡；请直接在卡片里写意见')
            claim = self.claim(task_id) or {}
            project = self._project(handoff['project'])
            title = handoff['title'] if handoff['title'].startswith('返工：') else '返工：' + handoff['title']
            rework = dict(handoff, title=title[:200], decisions=handoff.get('decisions', []) + ['退回意见：' + comment], rework_of=task_id)
            rework.pop('task_id', None)
            if claim.get('workspace'):
                rework['inherit'] = {k: claim[k] for k in ['workspace', 'branch', 'base_branch', 'base_commit']}
            draft = dict(title=rework['title'], type=task['type'], status='todo', priority=task['priority'], tags=[HANDOFF_LABEL], description=f'**返工自 {task_id}。** 退回意见：{comment}\n\n**为什么做：** {handoff["why"]}\n\n**做完能看到：** {handoff["goal"]}')
            extra = [x for line in acceptance_lines(handoff) for x in ['--ac', line]]
            new_id = self.tasks._create(draft, project, extra)
            now = self._now()
            self._edit(new_id, '--plan', render_plan(rework, 'ProjectInbox（退回生成）', now))
            self._save('handoffs', new_id, dict(rework, task_id=new_id, author='projectinbox', submitted_at=now))
            if claim:
                self._save('claims', new_id, dict(task_id=new_id, agent='', claimed_at='', active=False, project=project['id'], repo=claim.get('repo'), workspace=claim.get('workspace'), branch=claim.get('branch'), base_branch=claim.get('base_branch'), base_commit=claim.get('base_commit'), reported_head=claim.get('reported_head', ''), history=[dict(event='inherit', from_task=task_id, at=now)]))
                claim.update(active=False, transferred_to=new_id, history=claim.get('history', []) + [dict(event='reject', at=now, comment=comment)])
                self._save('claims', task_id, claim)
            self._note(new_id, f'由 {task_id} 退回生成。退回意见：{comment}' + (f'。领取时沿用工作区 {claim["workspace"]}。' if claim.get('workspace') else ''))
            self._note(task_id, f'用户退回：{comment}。返工卡 {new_id}。')
            self._edit(task_id, '--add-label', REJECTED_LABEL)
            self.tasks.backend.run('task', 'archive', task_id)
        return dict(task_id=task_id, status='archived', rework_task=new_id, workspace=claim.get('workspace', ''))

    def cancel(self, task_id, reason=''):
        self.tasks._id(task_id)
        with self.store.lock():
            task = self.tasks.get_task(task_id, _locked=True)
            if task['status'] in ['archived', 'done']:
                raise ValueError(f'{task_id} 已经是 {task["status"]}')
            claim = self.claim(task_id)
            removed = False
            if claim and claim.get('workspace') and Path(claim['workspace']).exists():
                removed = ws.remove_worktree(claim['repo'], claim['workspace'])
            if claim:
                claim.update(active=False, history=claim.get('history', []) + [dict(event='cancel', at=self._now(), reason=reason)])
                self._save('claims', task_id, claim)
            self._note(task_id, '用户取消' + (f'：{reason}' if reason else '') + '。' + (f'工作区已删除，分支 {claim["branch"]} 保留。' if removed else ''))
            self._edit(task_id, '--add-label', CANCELLED_LABEL)
            self.tasks.backend.run('task', 'archive', task_id)
        return dict(task_id=task_id, status='archived', workspace_removed=removed, branch_kept=claim.get('branch') if claim else None)

    # ---------- agent 改卡片的收紧版 ----------
    def agent_update(self, task_id, changes):
        if not isinstance(changes, dict):
            raise ValueError('changes 必须是对象')
        if 'status' in changes:
            raise ValueError('状态不能用 update_task 改：领取用 claim_tasks，汇报用 submit_report，放回用 release_task；验收和退回只有用户能做。')
        if 'notes' in changes:
            current = self.tasks.get_task(task_id)['notes']
            notes = changes['notes']
            if not isinstance(notes, str) or not notes.startswith(current):
                raise ValueError('notes 只能往后追加：请先读取任务，把原有 notes 原样保留在前面，再接上新内容。')
        if self.handoff(task_id):
            # 交接单卡片的项目和流程标签由程序维护，agent 改了会让领取和 check 判断失准
            if 'project_id' in changes:
                raise ValueError('带交接单的卡不能改项目：项目由交接单决定，要改请重新提交交接单。')
            if 'tags' in changes and isinstance(changes['tags'], list):
                current = set(self.tasks.get_task(task_id)['tags']) & FLOW_LABELS
                if set(changes['tags']) & FLOW_LABELS != current:
                    raise ValueError('「' + '、'.join(sorted(FLOW_LABELS)) + '」这几个标签由程序维护，不能增删；其他标签可以改。')
        return self.tasks.update_task(task_id, **changes)

    def agent_archive(self, task_id):
        """MCP 的 archive_task：带交接单的卡只能由用户 accept / reject / cancel 收尾。"""
        if self.handoff(task_id):
            task = self.tasks.get_task(task_id)
            if task['status'] not in ['done', 'archived']:
                raise ValueError(f'{task_id} 带交接单、现在是 {task["status"]}，不能由 agent 归档；不做了请 release_task 放回并写原因，取消由用户来做。')
        return self.tasks.archive_task(task_id)

    def guard_cli_status(self, task_id, changes):
        """命令行 update 也不能绕过状态机。"""
        if 'status' not in changes:
            return
        if changes['status'] == 'done':
            raise ValueError('不能用 update 标完成：验收通过用 project-inbox accept，没有工作区的旧卡用 project-inbox done')
        claim = self.claim(task_id)
        if claim and claim.get('active'):
            raise ValueError(f'{task_id} 正由 {claim["agent"]} 领取中，状态只能通过 submit-report / release 推进')

    def done(self, task_id):
        """命令行 done：有工作区的卡等同 accept --no-merge，只标完成、不合并，合并仍要用户明确运行 accept。"""
        claim = self.claim(task_id)
        if claim and claim.get('workspace'):
            result = self.accept(task_id, merge=False)
            result['hint'] = f"只标了完成，没有合并：改动还在分支 {claim['branch']}（工作区 {claim['workspace']}）。要合并请运行 project-inbox accept {task_id}。"
            return result
        return self.tasks.update_task(task_id, status='done')

    # ---------- 工作区清理（第 3 步） ----------
    def workspace_scan(self):
        with self.store.lock():
            tasks = self.tasks.healthy_tasks()
        # 还没收尾的卡正在用哪个工作区，以领取记录为准：返工卡沿用原卡的文件夹，名字里是原卡的编号
        owners = {}
        for task in tasks:
            claim = None if task['status'] in ['done', 'archived'] else self.claim(task['id'])
            if claim and claim.get('workspace'):
                owners[str(Path(claim['workspace']).resolve())] = task['id']
        return ws.scan(self.registry.list_projects(), tasks, owners)

    def workspace_clean(self, apply=False):
        rows = self.workspace_scan()
        actions = ws.clean(rows, self.store.safe('work/trash'), apply=apply)
        kept = [r for r in rows if r['action'] == 'keep']
        return dict(applied=apply, actions=actions, kept=kept, message=('已处理 ' if apply else '预演，没有改动任何文件。将处理 ') + f'{len(actions)} 个，保留 {len(kept)} 个' + ('' if apply else '；确认无误后加 --apply 真正执行') + '。')

    def consistency(self):
        """project-inbox check 用：列出卡片状态和交接单/领取/汇报记录对不上的地方。"""
        warnings = []
        with self.store.lock():
            tasks = self.tasks.healthy_tasks()
        for task in tasks:
            claim = self.claim(task['id'])
            if task['status'] == 'in_progress' and HANDOFF_LABEL in task['tags'] and not (claim and claim.get('active')):
                warnings.append(f"{task['id']} 在进行中，但不是通过 claim_tasks 领取的")
            if task['status'] == 'review' and HANDOFF_LABEL in task['tags'] and not self._load('reports', task['id']):
                warnings.append(f"{task['id']} 在待验收，但没有汇报单")
            # 汇报通过后卡片在待验收，领取记录要到 accept / reject 才结束：这是正常状态，不算对不上
            if claim and claim.get('active') and task['status'] not in ['in_progress', 'review']:
                warnings.append(f"{task['id']} 有进行中的领取记录，但卡片状态是 {task['status']}（可能在看板上被拖动过）")
        return warnings
