"""程序管理的任务工作区：领取时建在项目里的 .worktrees/task-编号，验收或取消时收尾。

所有 git 调用都关掉钩子和全局配置，只在已注册项目的仓库里执行；
这里从不使用 --force，也从不永久删除文件夹：有没提交的改动时 git 会拒绝移除。
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess

WORKTREES_DIR = '.worktrees'
GIT_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/var/empty', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
# 子任务编号（task-5.2）要整段认出来，否则会被当成 TASK-5
TASK_IN_NAME = re.compile(r'(?<![0-9a-z])task[-_ ]?([1-9][0-9]*(?:\.[1-9][0-9]*)*)(?![0-9])', re.I)


def git(cwd, *args, timeout=60, check=True):
    try:
        result = subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-C', str(cwd), *args], capture_output=True, text=True, timeout=timeout, env=GIT_ENV)
    except subprocess.TimeoutExpired as error:
        raise ValueError('git 操作超时：git ' + ' '.join(args[:2])) from error
    except OSError as error:
        raise ValueError('无法运行 git，请确认已安装') from error
    if check and result.returncode:
        raise ValueError('git ' + ' '.join(args[:2]) + ' 失败：' + (result.stderr or result.stdout).strip()[:500])
    return result.stdout.strip() if check else result


def branch_name(task_id):
    return task_id.lower()


def repo_root(project_path):
    path = Path(project_path)
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise ValueError(f'项目目录不存在或不可用：{project_path}')
    result = git(path, 'rev-parse', '--show-toplevel', check=False)
    if result.returncode:
        raise ValueError(f'项目目录不是 git 仓库，程序没法建工作区：{project_path}')
    return Path(result.stdout.strip()).resolve()


def _exclude(repo):
    """把 .worktrees/ 写进该仓库的 .git/info/exclude：只在本机生效，不改项目里的任何文件。"""
    common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    exclude = common / 'info/exclude'
    exclude.parent.mkdir(parents=True, exist_ok=True)
    line = '/' + WORKTREES_DIR + '/'
    current = exclude.read_text() if exclude.exists() else ''
    if line not in current.splitlines():
        with exclude.open('a') as f:
            f.write(('' if not current or current.endswith('\n') else '\n') + line + '\n')


def worktrees(repo):
    """git worktree list 的结果，第一条是主工作目录。"""
    entries, current = [], {}
    for line in git(repo, 'worktree', 'list', '--porcelain').splitlines() + ['']:
        if not line:
            if current:
                entries.append(current)
            current = {}
            continue
        key, _, value = line.partition(' ')
        current[key] = value or True
    return [dict(path=str(Path(e['worktree']).resolve()), branch=str(e.get('branch', '')).removeprefix('refs/heads/'), head=e.get('HEAD', ''), prunable=bool(e.get('prunable'))) for e in entries]


def current_branch(repo):
    result = git(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
    if result.returncode:
        raise ValueError('项目主目录当前不在任何分支上（detached HEAD），请先切回主分支')
    return result.stdout.strip()


def ensure_worktree(project_path, task_id, inherit=None):
    """建或沿用工作区。inherit 是返工时从原任务接过来的工作区记录。"""
    repo = repo_root(project_path)
    _exclude(repo)
    if inherit:
        workspace, branch = Path(inherit['workspace']), inherit['branch']
        base_branch, base_commit = inherit['base_branch'], inherit['base_commit']
    else:
        workspace = repo / WORKTREES_DIR / branch_name(task_id)
        branch = branch_name(task_id)
        base_branch = current_branch(repo)
        base_commit = git(repo, 'rev-parse', 'HEAD')
    known = {w['path']: w for w in worktrees(repo)}
    if str(workspace.resolve()) in known and not workspace.is_dir():
        # 文件夹被手动删掉、git 里还留着登记：不能当成「沿用已有」把一个不存在的路径发给 agent。
        # 清掉登记后往下走，按任务分支重建（提交都还在分支上）。
        git(repo, 'worktree', 'prune')
        known = {w['path']: w for w in worktrees(repo)}
    if str(workspace.resolve()) in known:
        existing = known[str(workspace.resolve())]
        if existing['branch'] != branch:
            raise ValueError(f'{workspace} 已存在但分支是 {existing["branch"] or "（无）"}，不是 {branch}；请人工核对')
        return dict(repo=str(repo), workspace=str(workspace.resolve()), branch=branch, base_branch=base_branch, base_commit=base_commit, reused=True)
    if workspace.exists():
        raise ValueError(f'{workspace} 已存在但不是这个仓库的工作区；请先运行 project-inbox workspace scan 核对')
    workspace.parent.mkdir(parents=True, exist_ok=True)
    exists = git(repo, 'rev-parse', '--verify', '--quiet', 'refs/heads/' + branch, check=False).returncode == 0
    if exists:
        git(repo, 'worktree', 'add', str(workspace), branch)
        if not inherit:
            base_commit = git(repo, 'merge-base', base_branch, branch)
    else:
        git(repo, 'worktree', 'add', '-b', branch, str(workspace), base_commit)
    return dict(repo=str(repo), workspace=str(workspace.resolve()), branch=branch, base_branch=base_branch, base_commit=base_commit, reused=exists)


def branch_head(repo, branch):
    """任务分支当前指向的提交；分支不存在时返回空字符串。"""
    result = git(repo, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}^{{commit}}', check=False)
    return result.stdout.strip() if result.returncode == 0 else ''


def state(workspace, base_commit, base_branch=''):
    """工作区的事实：当前提交、base 之后的新提交、没提交的改动。程序自己读，不信 agent 写的。

    给了 base_branch 时，主分支上已有的提交不算「新提交」：任务分支只是快进或变基到最新主分支，
    并不代表 agent 提交了改动。
    """
    path = Path(workspace)
    if not path.is_dir():
        return dict(exists=False, head='', branch='', commits=[], dirty=[])
    head = git(path, 'rev-parse', 'HEAD')
    current = git(path, 'symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
    branch = current.stdout.strip() if current.returncode == 0 else ''
    known = [base_commit] if base_commit else []
    if known and base_branch and branch_head(path, base_branch):
        known.append('refs/heads/' + base_branch)
    log = git(path, 'log', '--format=%h %s', 'HEAD', '--not', *known) if known else ''
    dirty = [line for line in git(path, 'status', '--porcelain', '--untracked-files=all').splitlines() if line.strip()]
    return dict(exists=True, head=head, branch=branch, commits=[l for l in log.splitlines() if l.strip()], dirty=dirty)


def merge_fast_forward(repo, branch, base_branch):
    """在项目主目录把任务分支快进合并到主分支；主分支动过就不硬合。"""
    if current_branch(repo) != base_branch:
        raise ValueError(f'项目主目录现在在 {current_branch(repo)} 分支上，不是 {base_branch}；请先切回 {base_branch} 再验收')
    result = git(repo, 'merge', '--ff-only', '--no-edit', branch, check=False)
    if result.returncode:
        raise ValueError(f'{base_branch} 在这期间有了新提交（或主目录有冲突的未提交改动），没法快进合并 {branch}。可以 reject 退回，让 agent 先把工作区更新到最新的 {base_branch} 再汇报。git 原话：' + (result.stderr or result.stdout).strip()[:300])
    return git(repo, 'rev-parse', 'HEAD')


def remove_worktree(repo, workspace):
    """用 git 正常移除工作区；有没提交的改动时 git 会拒绝，分支保留。"""
    path = Path(workspace)
    if not path.exists():
        git(repo, 'worktree', 'prune', check=False)
        return False
    result = git(repo, 'worktree', 'remove', str(path), check=False)
    if result.returncode:
        raise ValueError(f'工作区 {path} 有没提交的改动或无法移除，已原样保留。git 原话：' + (result.stderr or result.stdout).strip()[:300])
    return True


# ---------- 第 3 步：扫描和清理旧文件夹 ----------

def _task_id(*names):
    for name in names:
        match = TASK_IN_NAME.search(name or '')
        if match:
            return 'TASK-' + match[1]
    return None


def _modified(path, is_git):
    stamps = [path.stat().st_mtime]
    if is_git:
        result = git(path, 'log', '-1', '--format=%ct', check=False)
        if result.returncode == 0 and result.stdout.strip().isdigit():
            stamps.append(int(result.stdout.strip()))
    return datetime.fromtimestamp(max(stamps), timezone.utc).isoformat(timespec='seconds')


def _unmerged(path, main_repo):
    """复制品里有、主仓库里没有的提交：挪走前要提醒。"""
    heads = git(path, 'for-each-ref', '--format=%(objectname)', 'refs/heads', check=False)
    if heads.returncode:
        return []
    missing = []
    for sha in heads.stdout.split():
        if git(main_repo, 'cat-file', '-e', sha + '^{commit}', check=False).returncode:
            missing.append(sha[:8])
    return missing


def scan(projects, tasks, owners=None):
    """列出每个已注册项目旁边多出来的工作区和复制品，不改动任何东西。

    owners 是「工作区路径 → 正在用它的任务编号」（来自领取记录）。返工卡沿用原卡的工作区，
    文件夹名还是原卡的编号；只看名字会把它算到已归档的原卡头上，当成可以清理。
    """
    status = {t['id']: t['status'] for t in tasks}
    owners = owners or {}
    rows, seen = [], set()
    for project in projects:
        raw = project.get('path', '')
        if not raw or project.get('missing') or not Path(raw).is_dir():
            continue
        try:
            repo = repo_root(raw)
            listed = worktrees(repo)
        except ValueError:
            continue
        main = Path(listed[0]['path'])
        candidates = {}
        for w in listed[1:]:
            candidates[w['path']] = dict(kind='managed' if Path(w['path']).parent == main / WORKTREES_DIR else 'worktree', branch=w['branch'], prunable=w['prunable'])
        siblings = [Path(p) for p in project.get('worktrees', [])]
        prefix = main.name.casefold()
        try:
            siblings += [p for p in main.parent.iterdir() if p != main and p.is_dir() and not p.is_symlink() and p.name.casefold().startswith(prefix) and p.name[len(prefix):len(prefix) + 1] in ['-', '_', ' ', '.', '(']]
        except OSError:
            pass
        for p in siblings:
            key = str(p.resolve()) if p.exists() else str(p)
            if key in candidates or key == str(main):
                continue
            if not p.exists():
                continue
            dotgit = p / '.git'
            candidates[key] = dict(kind='worktree' if dotgit.is_file() else 'clone' if dotgit.is_dir() else 'copy', branch='', prunable=False)
        for key, info in sorted(candidates.items()):
            if key in seen:
                continue
            seen.add(key)
            path = Path(key)
            row = dict(project=project['id'], path=key, kind=info['kind'], branch=info['branch'], task=None, task_status='unknown', dirty=None, unmerged=[], modified=None, action='keep', reason='', repo=str(main))
            if not path.exists():
                row.update(action='prune', reason='文件夹已不存在，只剩 git 里的登记，可以安全清掉登记')
                rows.append(row)
                continue
            is_git = info['kind'] != 'copy'
            if is_git and not info['branch']:
                result = git(path, 'symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
                row['branch'] = result.stdout.strip() if result.returncode == 0 else ''
            row['task'] = owners.get(key) or _task_id(path.name, row['branch'])
            row['task_status'] = status.get(row['task'], 'unknown') if row['task'] else 'unknown'
            row['modified'] = _modified(path, is_git)
            if is_git:
                result = git(path, 'status', '--porcelain', '--untracked-files=normal', check=False)
                row['dirty'] = bool(result.stdout.strip()) if result.returncode == 0 else None
                if info['kind'] == 'clone':
                    row['unmerged'] = _unmerged(path, main)
            if not is_git:
                row['reason'] = '不是 git 目录，没法判断里面有没有没保存的改动，不动'
            elif row['dirty'] is None:
                row['reason'] = '读不出 git 状态，不动'
            elif row['dirty']:
                row['reason'] = '有没提交的改动，不动'
            elif row['unmerged']:
                row['reason'] = '有主仓库里没有的提交（' + '、'.join(row['unmerged'][:3]) + '），不动'
            elif not row['task']:
                row['reason'] = '名字里看不出对应哪个任务，不动'
            elif row['task_status'] not in ['done', 'archived']:
                row['reason'] = f"对应任务 {row['task']} 状态是 {row['task_status']}，还没完成，不动"
            else:
                row['action'] = 'trash' if info['kind'] == 'clone' else 'remove'
                row['reason'] = '干净，且任务已完成或归档' + ('；会挪进 work/trash/，不永久删除' if row['action'] == 'trash' else '；用 git 移除工作区，分支保留')
            rows.append(row)
    return rows


def clean(rows, trash_dir, apply=False):
    """只处理 scan 标了 remove / trash / prune 的行。apply=False 时只说会做什么。"""
    done = []
    for row in rows:
        if row['action'] == 'keep':
            continue
        result = dict(row, applied=False)
        if apply:
            try:
                if row['action'] == 'remove':
                    remove_worktree(row['repo'], row['path'])
                elif row['action'] == 'prune':
                    git(row['repo'], 'worktree', 'prune')
                else:
                    trash = Path(trash_dir)
                    trash.mkdir(parents=True, exist_ok=True)
                    target = trash / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + Path(row['path']).name)
                    shutil.move(row['path'], target)
                    result['moved_to'] = str(target)
                result['applied'] = True
            except (ValueError, OSError) as error:
                result['error'] = str(error)[:300]
        done.append(result)
    return done
