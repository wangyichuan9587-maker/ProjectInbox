"""清理旧文件夹：先看后删，默认只预演；只动干净且任务已完成的，复制品挪进回收站而不是删除。"""
import json
from pathlib import Path
import subprocess
import sys
from tests.test_system import system, repo, git
from server.flow import Flow


def by_name(rows):
    return {Path(r['path']).name: r for r in rows}


def test_scan_and_clean_only_touch_finished_clean_folders(system):
    manager, _, scan = system
    project = repo(scan / 'editr')
    done = manager.create_task(project_id='editr', title='已完成的任务')
    manager.update_task(done['id'], status='done')
    open_task = manager.create_task(project_id='editr', title='还在做的任务')
    done_n, open_n = done['id'][5:], open_task['id'][5:]
    git(project, 'worktree', 'add', '-q', '-b', f'task-{done_n}', str(scan / f'editr-task-{done_n}'))
    git(project, 'worktree', 'add', '-q', '-b', f'task-{open_n}', str(scan / f'editr-task-{open_n}'))
    git(project, 'worktree', 'add', '-q', '-b', 'feat-dirty', str(scan / f'editr-dirty-task-{done_n}'))
    (scan / f'editr-dirty-task-{done_n}' / 'wip.txt').write_text('unsaved')
    subprocess.run(['/usr/bin/git', 'clone', '-q', str(project), str(scan / f'editr copy task-{done_n}')], check=True)
    plain = scan / f'editr-old-task-{done_n}'
    plain.mkdir()
    (plain / 'x.txt').write_text('no git here')
    (scan / 'unrelated').mkdir()

    f = Flow(manager.root)
    rows = by_name(f.workspace_scan())
    assert 'unrelated' not in rows and 'editr' not in rows
    assert rows[f'editr-task-{done_n}']['action'] == 'remove' and rows[f'editr-task-{done_n}']['kind'] == 'worktree'
    assert rows[f'editr-task-{open_n}']['action'] == 'keep' and '还没完成' in rows[f'editr-task-{open_n}']['reason']
    assert rows[f'editr-dirty-task-{done_n}']['action'] == 'keep' and rows[f'editr-dirty-task-{done_n}']['dirty']
    assert rows[f'editr copy task-{done_n}']['action'] == 'trash' and rows[f'editr copy task-{done_n}']['kind'] == 'clone'
    assert rows[f'editr-old-task-{done_n}']['action'] == 'keep' and '不是 git 目录' in rows[f'editr-old-task-{done_n}']['reason']

    preview = f.workspace_clean()
    assert not preview['applied'] and len(preview['actions']) == 2
    assert all((scan / name).exists() for name in rows)

    base = [sys.executable, '-m', 'server.cli', '--home', str(manager.root), 'workspace']
    listed = json.loads(subprocess.run(base + ['scan'], capture_output=True, text=True, check=True).stdout)
    assert len(listed['folders']) == len(rows)
    applied = json.loads(subprocess.run(base + ['clean', '--apply'], capture_output=True, text=True, check=True).stdout)
    assert applied['applied'] and all(a['applied'] for a in applied['actions'])
    assert not (scan / f'editr-task-{done_n}').exists()
    assert git(project, 'rev-parse', '--verify', f'refs/heads/task-{done_n}')
    moved = next(a for a in applied['actions'] if a['action'] == 'trash')['moved_to']
    assert Path(moved).is_dir() and Path(moved).parent == manager.root / 'work/trash'
    for name in [f'editr-task-{open_n}', f'editr-dirty-task-{done_n}', f'editr-old-task-{done_n}', 'unrelated']:
        assert (scan / name).exists()


def test_clone_with_commits_missing_from_main_is_kept(system):
    manager, _, scan = system
    project = repo(scan / 'editr')
    done = manager.create_task(project_id='editr', title='已完成')
    manager.update_task(done['id'], status='done')
    copy = scan / f"editr-{done['id'].lower()}"
    subprocess.run(['/usr/bin/git', 'clone', '-q', str(project), str(copy)], check=True)
    git(copy, '-c', 'user.name=A', '-c', 'user.email=a@example.invalid', 'commit', '-q', '--allow-empty', '-m', 'only in copy')
    row = by_name(Flow(manager.root).workspace_scan())[copy.name]
    assert row['action'] == 'keep' and row['unmerged'] and '主仓库里没有的提交' in row['reason']


def test_folder_names_map_to_subtask_ids():
    from server.workspace import _task_id
    assert _task_id('editr-task-5.2') == 'TASK-5.2' and _task_id('task-5.10.3') == 'TASK-5.10.3'
    assert _task_id('editr-task-5') == 'TASK-5' and _task_id('editr-task-5.backup') == 'TASK-5'
    assert _task_id('mytask-5') is None and _task_id('editr copy', 'task_12') == 'TASK-12'
