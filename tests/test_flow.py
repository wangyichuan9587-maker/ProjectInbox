"""完整闭环：交接单 → 领取（程序建工作区）→ 汇报（程序核验）→ 验收 / 退回 / 取消。全部用合成仓库。"""
import copy
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import pytest
import yaml
from tests.test_system import system, repo, git
from server.contract import EXAMPLE_HANDOFF, ContractError
from server.flow import Flow
from server.handoff import agent_handoff


def handoff(**changes):
    data = copy.deepcopy(EXAMPLE_HANDOFF)
    data.update(project='editr', verify={'tests': 'test -f feature.txt', 'screenshots': True})
    data.update(changes)
    return data


def report(task_id, **changes):
    data = dict(task=task_id, outcome='done', summary='加了 feature.txt。', acceptance={'A1': {'result': '实际操作验证'}, 'A2': {'result': '实际操作验证', 'screenshot': 'after.png'}, 'A3': {'result': '未测', 'note': '合成仓库没有界面'}, 'A4': '仅自动测试'}, screenshots=['after.png'], where_to_see='合成仓库根目录', deviations='无', visual_summary='flowchart LR\n  领取 --> 提交 --> 汇报')
    data.update(changes)
    return data


def commit(path, name='feature.txt', text='ok\n'):
    (Path(path) / name).write_text(text)
    git(path, 'add', name)
    git(path, '-c', 'user.name=Agent', '-c', 'user.email=agent@example.invalid', 'commit', '-q', '-m', 'agent change ' + name)


@pytest.fixture
def flow(system):
    manager, _, scan = system
    repo(scan / 'editr')
    return Flow(manager.root), scan / 'editr'


def shot(flow, task_id, name='after.png'):
    (flow.evidence_dir(task_id) / name).write_bytes(b'png')


def test_handoff_creates_ready_card_with_acceptance_boxes(flow):
    f, _ = flow
    result = f.submit_handoff(handoff(), 'claude-code')
    task = f.tasks.get_task(result['task_id'])
    assert task['status'] == 'todo' and '交接单' in task['tags'] and task['project'] == 'editr'
    assert [a['text'][:4] for a in task['acceptance']] == ['[A1]', '[A2]', '[A3]', '[A4]'] and not any(a['checked'] for a in task['acceptance'])
    assert '这次不做' in task['plan'] and 'claude-code' in task['plan']
    saved = yaml.safe_load((f.root / f"work/handoffs/{task['id']}.yaml").read_text())
    assert saved['author'] == 'claude-code' and saved['scope']['dont']


def test_handoff_refused_and_open_questions_wait_in_inbox(flow):
    f, _ = flow
    bad = handoff(scope={'do': ['x'], 'dont': []})
    with pytest.raises(ContractError, match='scope.dont'):
        f.submit_handoff(bad, 'codex')
    assert f.tasks.search_tasks() == []
    waiting = f.submit_handoff(handoff(open_questions=['颜色用橙色还是红色？']), 'codex')
    task = f.tasks.get_task(waiting['task_id'])
    assert task['status'] == 'inbox' and '待确认' in task['tags'] and waiting['waiting_for_confirmation']
    refused = f.claim_tasks('codex', [task['id']])
    assert not refused['claimed'] and '只有待办' in refused['refused'][0]['reason']
    again = f.submit_handoff(handoff(task_id=task['id'], open_questions=[]), 'codex')
    task = f.tasks.get_task(again['task_id'])
    assert task['id'] == waiting['task_id'] and task['status'] == 'todo' and '待确认' not in task['tags']


def test_cli_submit_handoff_uses_same_check(flow, tmp_path):
    f, _ = flow
    path = tmp_path / 'h.yaml'
    path.write_text(yaml.safe_dump(handoff(acceptance=EXAMPLE_HANDOFF['acceptance'][:2]), allow_unicode=True))
    base = [sys.executable, '-m', 'server.cli', '--home', str(f.root)]
    bad = subprocess.run(base + ['submit-handoff', '--file', str(path), '--author', 'pi'], capture_output=True, text=True)
    assert bad.returncode == 2 and '至少 3 条' in json.loads(bad.stderr)['error']
    path.write_text(yaml.safe_dump(handoff(), allow_unicode=True))
    good = subprocess.run(base + ['submit-handoff', '--file', str(path), '--author', 'pi'], capture_output=True, text=True)
    assert good.returncode == 0 and json.loads(good.stdout)['status'] == 'todo'


def test_claim_builds_workspace_and_blocks_double_claim(flow):
    f, project = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    plain = f.tasks.create_task(project_id='editr', title='没有交接单的旧卡')
    result = f.claim_tasks('codex', project_id='editr', limit=3)
    assert [c['task_id'] for c in result['claimed']] == [task_id]
    pack = result['claimed'][0]
    workspace = project / '.worktrees' / task_id.lower()
    assert pack['workspace'] == str(workspace.resolve()) and workspace.is_dir()
    assert git(workspace, 'symbolic-ref', '--short', 'HEAD') == task_id.lower()
    assert '/.worktrees/' in (project / '.git/info/exclude').read_text()
    assert git(project, 'status', '--porcelain') == ''
    assert pack['handoff']['acceptance'] and pack['rules'] and set(pack['report_template']['acceptance']) == {'A1', 'A2', 'A3', 'A4'}
    task = f.tasks.get_task(task_id)
    assert task['status'] == 'in_progress' and task['assignee'] == ['@codex'] and 'codex 领取' in task['notes']
    second = f.claim_tasks('claude-code', [task_id, plain['id']])
    reasons = {r['task_id']: r['reason'] for r in second['refused']}
    assert '已被 codex' in reasons[task_id] and '没有合格的交接单' in reasons[plain['id']]
    released = f.release_task(task_id, '缺少数据库权限', 'codex')
    assert released['status'] == 'todo' and workspace.is_dir()
    again = f.claim_tasks('claude-code', [task_id])['claimed'][0]
    assert again['workspace'] == str(workspace.resolve())
    with pytest.raises(ValueError, match='codex'):
        f.release_task(task_id, '不是我领的', 'codex')


def test_report_is_checked_by_program_then_review(flow):
    f, _ = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    workspace = Path(f.claim_tasks('codex', [task_id])['claimed'][0]['workspace'])
    with pytest.raises(ContractError, match='漏了 A2'):
        bad = report(task_id)
        del bad['acceptance']['A2']
        f.submit_report(bad, 'codex')
    first = f.submit_report(report(task_id), 'codex')
    assert not first['accepted'] and any('没有新提交' in p for p in first['problems']) and any('after.png' in p for p in first['problems'])
    commit(workspace, 'other.txt')
    (workspace / 'scratch.txt').write_text('not committed')
    shot(f, task_id)
    second = f.submit_report(report(task_id), 'codex')
    assert not second['accepted'] and any('没提交的改动' in p for p in second['problems'])
    (workspace / 'scratch.txt').unlink()
    third = f.submit_report(report(task_id), 'codex')
    assert not third['accepted'] and any('测试没过' in p for p in third['problems'])
    assert f.tasks.get_task(task_id)['status'] == 'in_progress' and '汇报被拒收' in f.tasks.get_task(task_id)['notes']
    commit(workspace)
    ok = f.submit_report(report(task_id), 'codex')
    assert ok['accepted'] and ok['status'] == 'review'
    task = f.tasks.get_task(task_id)
    assert task['status'] == 'review'
    assert [a['checked'] for a in task['acceptance']] == [True, True, False, True]
    assert '```mermaid' in task['final_summary'] and '程序在工作区重跑' in task['final_summary']
    assert yaml.safe_load((f.root / f'work/reports/{task_id}.yaml').read_text())['test']['passed'] is True


def test_blocked_report_skips_checks(flow):
    f, _ = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    f.claim_tasks('codex', [task_id])
    result = f.submit_report(dict(task=task_id, outcome='blocked', blocked_reason='需要你给测试账号', summary='做到一半', acceptance={a: {'result': '未测', 'note': '受阻'} for a in ['A1', 'A2', 'A3', 'A4']}, where_to_see='无', deviations='无'), 'codex')
    assert result['status'] == 'blocked' and '需要你给测试账号' in f.tasks.get_task(task_id)['final_summary']


def finished(f, workspace_root=None):
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    workspace = Path(f.claim_tasks('codex', [task_id])['claimed'][0]['workspace'])
    commit(workspace)
    shot(f, task_id)
    assert f.submit_report(report(task_id), 'codex')['accepted']
    return task_id, workspace


def test_accept_fast_forwards_and_removes_workspace(flow):
    f, project = flow
    with pytest.raises(ValueError, match='只有待验收'):
        f.accept(f.submit_handoff(handoff(), 'x')['task_id'])
    task_id, workspace = finished(f)
    result = f.accept(task_id)
    assert result['merged'] and result['workspace_removed'] and not workspace.exists()
    assert (project / 'feature.txt').read_text() == 'ok\n'
    assert git(project, 'rev-parse', '--verify', 'refs/heads/' + task_id.lower())
    assert f.tasks.get_task(task_id)['status'] == 'done'


def test_accept_refuses_when_main_moved(flow):
    f, project = flow
    task_id, workspace = finished(f)
    commit(project, 'main-only.txt')
    with pytest.raises(ValueError, match='没法快进合并'):
        f.accept(task_id)
    assert workspace.is_dir() and f.tasks.get_task(task_id)['status'] == 'review'


def test_reject_creates_rework_card_in_same_workspace(flow):
    f, _ = flow
    task_id, workspace = finished(f)
    result = f.reject(task_id, '橙色太刺眼，换成浅一点的')
    old = f.tasks.get_task(task_id)
    assert old['status'] == 'archived' and '已退回' in old['tags']
    new = f.tasks.get_task(result['rework_task'])
    assert new['status'] == 'todo' and new['title'].startswith('返工：') and '橙色太刺眼' in new['plan']
    pack = f.claim_tasks('claude-code', [new['id']])['claimed'][0]
    assert pack['workspace'] == str(workspace.resolve())
    shot(f, new['id'])
    same = f.submit_report(report(new['id']), 'claude-code')
    assert not same['accepted'] and any('返工后没有新的提交' in p for p in same['problems'])
    commit(workspace, 'feature.txt', 'lighter\n')
    assert f.submit_report(report(new['id']), 'claude-code')['accepted']


def test_cancel_removes_clean_workspace_keeps_branch(flow):
    f, project = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    workspace = Path(f.claim_tasks('codex', [task_id])['claimed'][0]['workspace'])
    (workspace / 'wip.txt').write_text('unsaved')
    with pytest.raises(ValueError, match='没提交的改动'):
        f.cancel(task_id, '不做了')
    assert workspace.is_dir() and f.tasks.get_task(task_id)['status'] == 'in_progress'
    (workspace / 'wip.txt').unlink()
    result = f.cancel(task_id, '不做了')
    assert result['workspace_removed'] and not workspace.exists() and result['branch_kept'] == task_id.lower()
    assert f.tasks.get_task(task_id)['status'] == 'archived'


def test_agents_cannot_move_status_or_overwrite_notes(flow):
    f, _ = flow
    task = f.tasks.create_task(project_id='editr', title='旧卡')
    f.tasks.update_task(task['id'], notes='第一条')
    with pytest.raises(ValueError, match='状态不能用 update_task'):
        f.agent_update(task['id'], {'status': 'done'})
    with pytest.raises(ValueError, match='只能往后追加'):
        f.agent_update(task['id'], {'notes': '覆盖'})
    assert f.agent_update(task['id'], {'notes': '第一条\n\n第二条'})['notes'].endswith('第二条')
    base = [sys.executable, '-m', 'server.cli', '--home', str(f.root)]
    done = subprocess.run(base + ['update', task['id'], '--changes', '{"status":"done"}'], capture_output=True, text=True)
    assert done.returncode == 2 and 'accept' in json.loads(done.stderr)['error']
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    f.claim_tasks('codex', [task_id])
    moved = subprocess.run(base + ['update', task_id, '--changes', '{"status":"review"}'], capture_output=True, text=True)
    assert moved.returncode == 2 and '领取中' in json.loads(moved.stderr)['error']
    assert subprocess.run(base + ['done', task_id], capture_output=True, text=True).returncode == 2
    research = subprocess.run(base + ['update', task['id'], '--changes', '{"status":"review"}'], capture_output=True, text=True)
    assert research.returncode == 0


def test_board_copy_gives_short_claim_text_for_handoff_cards(flow):
    f, _ = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    text = agent_handoff(f.root, task_id)['text']
    assert 'claim ' + task_id in text and 'submit-report' in text and 'git worktree' not in text
    # 指令里三次带上安装目录和 --home 的绝对路径：只量指令本身，结果不随安装位置和临时目录的长短变化
    cli = shlex.join([str(Path(__file__).resolve().parents[1] / 'bin/project-inbox'), '--home', str(f.root)])
    assert text.count(cli) == 3 and len(text.replace(cli, 'project-inbox')) < 1000
    legacy = f.tasks.create_task(project_id='editr', title='旧卡')
    assert 'git worktree' in agent_handoff(f.root, legacy['id'])['text']


def test_check_reports_inconsistent_cards(flow):
    f, _ = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    f.claim_tasks('codex', [task_id])
    f.tasks.update_task(task_id, status='todo')
    assert any('看板上被拖动' in w for w in f.consistency())


def test_install_skill_writes_claude_skill_without_overwriting(tmp_path):
    from scripts.install_skills import install
    home = tmp_path / 'user'
    first = install(['claude', 'codex'], home=tmp_path / 'inbox', user_home=home)
    skill = home / '.claude/skills/handoff/SKILL.md'
    assert [r['action'] for r in first] == ['已写入', '已写入']
    text = skill.read_text()
    assert text.startswith('---\nname: handoff') and 'submit_handoff' in text and '{{CLI}}' not in text and str((tmp_path / 'inbox').resolve()) in text
    assert not (home / '.codex/prompts/handoff.md').read_text().startswith('---')
    assert install(['claude'], home=tmp_path / 'inbox', user_home=home)[0]['action'] == '已是最新'
    skill.write_text('my own skill')
    assert '未覆盖' in install(['claude'], home=tmp_path / 'inbox', user_home=home)[0]['action'] and skill.read_text() == 'my own skill'


# ---------- 评审修复的回归测试 ----------

def test_report_refused_when_commits_are_on_another_branch(flow):
    f, _ = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    workspace = Path(f.claim_tasks('codex', [task_id])['claimed'][0]['workspace'])
    git(workspace, 'switch', '-q', '-c', 'side')
    commit(workspace)
    shot(f, task_id)
    result = f.submit_report(report(task_id), 'codex')
    assert not result['accepted'] and any('不是任务分支' in p for p in result['problems'])
    git(workspace, 'switch', '-q', task_id.lower())
    git(workspace, 'merge', '-q', '--ff-only', 'side')
    assert f.submit_report(report(task_id), 'codex')['accepted']
    git(workspace, 'switch', '-q', 'side')
    with pytest.raises(ValueError, match='不是任务分支'):
        f.accept(task_id)


def test_blocked_card_can_be_handed_off_and_claimed_again(flow):
    f, _ = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    workspace = f.claim_tasks('codex', [task_id])['claimed'][0]['workspace']
    f.submit_report(dict(task=task_id, outcome='blocked', blocked_reason='需要测试账号', summary='做到一半', acceptance={a: {'result': '未测', 'note': '受阻'} for a in ['A1', 'A2', 'A3', 'A4']}, where_to_see='无', deviations='无'), 'codex')
    assert not f.claim(task_id)['active'] and f.consistency() == []
    again = f.submit_handoff(handoff(task_id=task_id, decisions=['测试账号已给']), 'claude-code')
    assert again['status'] == 'todo'
    pack = f.claim_tasks('claude-code', [task_id])['claimed'][0]
    assert pack['workspace'] == workspace


def test_agent_cannot_archive_or_relabel_handoff_cards(flow):
    f, _ = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    f.claim_tasks('codex', [task_id])
    with pytest.raises(ValueError, match='不能由 agent 归档'):
        f.agent_archive(task_id)
    with pytest.raises(ValueError, match='由程序维护'):
        f.agent_update(task_id, {'tags': []})
    with pytest.raises(ValueError, match='不能改项目'):
        f.agent_update(task_id, {'project_id': None})
    assert f.agent_update(task_id, {'tags': ['交接单', '界面']})['tags'] == ['交接单', '界面']
    plain = f.tasks.create_task(project_id='editr', title='旧卡')
    assert f.agent_archive(plain['id'])['status'] == 'archived'


def test_accept_finishes_even_if_workspace_cannot_be_removed(flow, monkeypatch):
    f, project = flow
    task_id, workspace = finished(f)

    def refuse(repo, path):
        raise ValueError('工作区被锁定')
    monkeypatch.setattr('server.flow.ws.remove_worktree', refuse)
    result = f.accept(task_id)
    assert result['merged'] and not result['workspace_removed'] and '锁定' in result['warning']
    assert (project / 'feature.txt').exists() and f.tasks.get_task(task_id)['status'] == 'done'


def test_failed_status_edit_leaves_no_claim(flow, monkeypatch):
    f, _ = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    original = f._edit

    def broken(tid, *args):
        if '--status' in args:
            raise ValueError('Backlog 写入失败')
        return original(tid, *args)
    monkeypatch.setattr(f, '_edit', broken)
    with pytest.raises(ValueError, match='Backlog 写入失败'):
        f.claim_tasks('codex', [task_id])
    assert f.claim(task_id) is None and f.tasks.get_task(task_id)['status'] == 'todo'


def test_done_marks_without_merging_then_accept_can_merge(flow):
    f, project = flow
    task_id, workspace = finished(f)
    base = [sys.executable, '-m', 'server.cli', '--home', str(f.root)]
    done = subprocess.run(base + ['done', task_id], capture_output=True, text=True)
    assert done.returncode == 0 and not json.loads(done.stdout)['merged']
    assert f.tasks.get_task(task_id)['status'] == 'done' and not (project / 'feature.txt').exists() and workspace.is_dir()
    result = f.accept(task_id)
    assert result['merged'] and (project / 'feature.txt').exists()
    with pytest.raises(ValueError, match='只有待验收'):
        f.accept(task_id)


def test_test_timeout_does_not_hang_on_detached_background_process(tmp_path, monkeypatch):
    from server import checks
    command = "perl -MPOSIX -e 'setsid(); sleep 5' & sleep 30"
    passed, output, seconds = checks.run_tests(tmp_path, command, 1)
    assert passed is False and '没跑完' in output and seconds < 5


# ---------- 第二轮评审修复的回归测试 ----------

def test_title_mentioning_another_task_gets_its_own_card(flow):
    from server.task_manager import created_id
    f, _ = flow
    other = f.tasks.create_task(project_id='editr', title='无关的旧卡')
    assert f.tasks.create_task(title=f"跟进 {other['id']} 的问题")['id'] != other['id']
    result = f.submit_handoff(handoff(title=f"修复 {other['id']} 引入的回归"), 'claude-code')
    assert result['task_id'] != other['id'] and result['status'] == 'todo'
    assert f.handoff(result['task_id'])['title'].startswith('修复') and '交接单' in f.tasks.get_task(result['task_id'])['plan']
    untouched = f.tasks.get_task(other['id'])
    assert f.handoff(other['id']) is None and untouched['plan'] == '' and untouched['notes'] == ''
    workspace = Path(f.claim_tasks('codex', [result['task_id']])['claimed'][0]['workspace'])
    commit(workspace)
    shot(f, result['task_id'])
    assert f.submit_report(report(result['task_id']), 'codex')['accepted']
    rework = f.reject(result['task_id'], '再改改')['rework_task']
    assert rework not in [other['id'], result['task_id']] and f.handoff(rework)['rework_of'] == result['task_id']
    assert f.handoff(other['id']) is None and f.claim(other['id']) is None
    echoed = 'File: /x/backlog/tasks/task-7 - 见-TASK-2.md\n\nTask TASK-7 - 见 TASK-2\n====\n\nDescription:\n----\nTask TASK-9 - 假的标题行\n'
    assert created_id(echoed) == 'TASK-7' and created_id('File: /x/backlog/tasks/task-3.1 - 见-TASK-2.md\n') == 'TASK-3.1'
    with pytest.raises(ValueError, match='未从 Backlog'):
        created_id('Created something mentioning TASK-2')


def test_check_is_quiet_for_cards_waiting_for_acceptance(flow):
    f, _ = flow
    task_id, _ = finished(f)
    assert f.tasks.get_task(task_id)['status'] == 'review' and f.claim(task_id)['active']
    assert f.consistency() == []
    f.tasks.update_task(task_id, status='todo')
    assert any('看板上被拖动' in w for w in f.consistency())


def test_claim_rebuilds_workspace_deleted_by_hand(flow):
    f, _ = flow
    task_id = f.submit_handoff(handoff(), 'claude-code')['task_id']
    workspace = Path(f.claim_tasks('codex', [task_id])['claimed'][0]['workspace'])
    commit(workspace)
    f.release_task(task_id, '先放回', 'codex')
    shutil.rmtree(workspace)
    pack = f.claim_tasks('claude-code', [task_id])['claimed'][0]
    assert pack['workspace'] == str(workspace) and (workspace / 'feature.txt').read_text() == 'ok\n'
    assert git(workspace, 'symbolic-ref', '--short', 'HEAD') == task_id.lower()
    shot(f, task_id)
    assert f.submit_report(report(task_id), 'claude-code')['accepted']


def test_accept_refuses_commits_added_after_report(flow):
    f, project = flow
    task_id, workspace = finished(f)
    commit(workspace, 'later.txt', '汇报之后才加的\n')
    with pytest.raises(ValueError, match='汇报之后又有了新提交'):
        f.accept(task_id)
    assert f.tasks.get_task(task_id)['status'] == 'review' and not (project / 'feature.txt').exists()
    rework = f.reject(task_id, '把后加的提交也核验一遍')['rework_task']
    f.claim_tasks('codex', [rework])
    shot(f, rework)
    assert f.submit_report(report(rework), 'codex')['accepted']
    assert f.accept(rework)['merged'] and (project / 'later.txt').exists()


def test_commits_already_on_main_do_not_count_as_new(flow):
    f, project = flow
    task_id = f.submit_handoff(handoff(verify={'tests': 'none', 'screenshots': False}), 'claude-code')['task_id']
    pack = f.claim_tasks('codex', [task_id])['claimed'][0]
    workspace = Path(pack['workspace'])
    commit(project, 'main-only.txt')
    git(workspace, 'merge', '-q', '--ff-only', pack['base_branch'])
    plain = report(task_id, screenshots=[])
    plain['acceptance']['A2'] = {'result': '实际操作验证'}
    refused = f.submit_report(plain, 'codex')
    assert not refused['accepted'] and any('没有新提交' in p for p in refused['problems'])
    commit(workspace)
    ok = f.submit_report(plain, 'codex')
    assert ok['accepted'] and '1 个新提交' in ok['verified'][0]


def test_clean_keeps_workspace_still_used_by_rework_card(flow):
    f, _ = flow
    task_id, workspace = finished(f)
    rework = f.reject(task_id, '再改改')['rework_task']
    f.claim_tasks('claude-code', [rework])
    row = next(r for r in f.workspace_scan() if r['path'] == str(workspace))
    assert row['task'] == rework and row['action'] == 'keep' and '还没完成' in row['reason']
    assert f.workspace_clean(apply=True)['actions'] == [] and workspace.is_dir()
    f.cancel(rework, '不做了')
    assert not workspace.exists()


def test_finished_test_command_is_not_failed_by_leftover_background_process(tmp_path):
    from server import checks
    passed, output, seconds = checks.run_tests(tmp_path, '(sleep 6 &) ; echo tests-ok', 3)
    assert passed is True and 'tests-ok' in output and seconds < 3
    passed, output, _ = checks.run_tests(tmp_path, 'echo 第一行; echo boom >&2; exit 3', 3)
    assert passed is False and '第一行' in output and 'boom' in output


def test_update_only_checks_the_fields_being_changed(flow):
    f, _ = flow
    task = f.tasks.create_task(project_id='editr', title='记录很长的旧卡')
    for _ in range(5):
        f._edit(task['id'], '--append-notes', '程序追加的记录' * 700)
    assert len(f.tasks.get_task(task['id'])['notes']) > 20000
    assert f.tasks.update_task(task['id'], status='review')['status'] == 'review'
    with pytest.raises(ValueError, match='notes'):
        f.tasks.update_task(task['id'], notes='x' * 20001)
    base = [sys.executable, '-m', 'server.cli', '--home', str(f.root)]
    odd = subprocess.run(base + ['update', task['id'], '--changes', '{"task_id":"TASK-9"}'], capture_output=True, text=True)
    assert odd.returncode == 2 and '禁止修改字段' in json.loads(odd.stderr)['error']
