"""限定目录内的原子文件存储；所有调用方共享同一把进程锁。"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import tempfile
import yaml

MAX_FILE = 256 * 1024


class Storage:
    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        if self.root.is_symlink():
            raise ValueError('安装根目录不能是符号链接')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = self.root.resolve()

    def safe(self, relative):
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('路径超出 ProjectInbox')
        p = self.root
        for part in relative.parts:
            p = p / part
            if p.is_symlink():
                raise ValueError('不允许符号链接')
        if not p.resolve().is_relative_to(self.root):
            raise ValueError('路径超出 ProjectInbox')
        return p

    def mkdir(self, relative):
        p = self.safe(relative)
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.safe(relative)

    @contextmanager
    def lock(self):
        path = self.safe('.inbox.lock')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def read(self, relative, limit=MAX_FILE):
        p = self.safe(relative)
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise ValueError('只允许普通文件')
            raw = f.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('文件超过大小限制')
        return raw.decode('utf-8')

    def write(self, relative, content, *, replace=False):
        raw = content.encode('utf-8')
        if len(raw) > MAX_FILE:
            raise ValueError('文件超过大小限制')
        p = self.safe(relative)
        self.mkdir(Path(relative).parent)
        fd, name = tempfile.mkstemp(prefix='.pending-', dir=p.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            self.safe(relative)
            if replace:
                os.replace(name, p)
            else:
                os.link(name, p)  # 目标已存在时失败，绝不覆盖新建任务。
            directory = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def yaml(self, relative, default):
        if not self.safe(relative).exists():
            return default
        try:
            result = yaml.safe_load(self.read(relative))
        except yaml.YAMLError as error:
            # 命令行和 MCP 只把 ValueError 当成可读的错误；不转的话手工改坏一个配置就是一屏 traceback
            raise ValueError(f'{relative} 不是合法的 YAML，请修正后重试：' + ' '.join(str(error).split())[:300]) from error
        return default if result is None else result
