"""汇报时程序自己做的核验：截图在不在、提交在不在对的分支、测试是不是真过了。"""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from . import workspace as ws

OUTPUT_TAIL = 4000


def evidence(evidence_dir, names, required):
    problems = []
    folder = Path(evidence_dir)
    for name in names:
        path = folder / name
        if path.is_symlink() or not path.is_file():
            problems.append(f'截图 {name} 不在 {folder} 里。')
        elif path.stat().st_size == 0:
            problems.append(f'截图 {name} 是空文件。')
    if required and not names:
        problems.append(f'交接单要求截图，但 {folder} 里一张都没有登记。')
    return problems


def commits(workspace, base_commit, previous_head='', branch='', base_branch=''):
    """任务分支上要有新提交，工作区不能有没提交的改动。"""
    facts = ws.state(workspace, base_commit, base_branch)
    problems = []
    if not facts['exists']:
        return [f'工作区 {workspace} 不存在：代码必须在程序分配的工作区里改。'], facts
    if branch and facts['branch'] != branch:
        # 提交在别的分支上时，验收合并的任务分支其实没有这些改动
        problems.append(f'工作区现在在 {facts["branch"] or "（不在任何分支上）"}，不是任务分支 {branch}：切回 {branch} 并把提交放在它上面再汇报。')
    elif not facts['commits']:
        problems.append('任务分支上没有新提交：先在工作区里 git commit，再汇报。')
    elif previous_head and facts['head'] == previous_head:
        problems.append('返工后没有新的提交：分支还停在上次汇报的位置。')
    if facts['dirty']:
        shown = '；'.join(facts['dirty'][:5])
        problems.append(f'工作区里还有 {len(facts["dirty"])} 处没提交的改动（{shown}）：提交或撤销后再汇报。')
    return problems, facts


def _tail(log):
    """输出文件的末尾。用 pread 读，不挪动还在写的后台进程共用的文件位置。"""
    size = os.fstat(log.fileno()).st_size
    window = min(size, OUTPUT_TAIL * 4)
    return os.pread(log.fileno(), window, size - window).decode('utf-8', errors='replace')[-OUTPUT_TAIL:]


def run_tests(workspace, command, timeout):
    """在工作区里限时重跑交接单写的测试命令，返回 (是否通过, 输出末尾, 用时秒数)。

    输出写进临时文件而不是管道，只等测试命令自己退出：它留下的后台进程（预览服务之类）
    就算一直占着输出，也不会让已经跑完的测试被判成超时，更不会卡住汇报。
    """
    if command.strip().lower() == 'none':
        return None, '交接单写明项目没有测试（tests: none），跳过。', 0
    shell = os.environ.get('SHELL') or '/bin/bash'
    if not Path(shell).is_absolute() or not Path(shell).exists():
        shell = '/bin/bash' if Path('/bin/bash').exists() else '/bin/sh'
    started = time.monotonic()
    with tempfile.TemporaryFile() as log:
        try:
            proc = subprocess.Popen([shell, '-lc', command], cwd=workspace, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as error:
            return False, f'测试命令启动失败：{error}', 0
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
            return False, f'测试超过 {timeout} 秒没跑完，已停止。\n' + _tail(log), round(time.monotonic() - started)
        return proc.returncode == 0, _tail(log), round(time.monotonic() - started)
