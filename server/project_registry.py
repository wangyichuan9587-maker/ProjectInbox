"""只读取白名单项目元信息，不运行业务仓库内的代码。"""
import hashlib
import json
import calendar
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import yaml
from .storage import Storage

EXCLUDE = {'.git', 'node_modules', '.next', 'dist', 'build', '.venv', 'venv', '__pycache__', '.cache', '.Trash', 'data', 'datasets', 'secrets', '.ssh', '.aws', '.codex', '.agents', '.claude'}
GENERATED = {'vendor', 'coverage', 'target', 'DerivedData', 'Pods', 'Carthage', 'logs', 'work', 'backups'}
PROJECT_FILES = {'package.json', 'pyproject.toml', 'Cargo.toml', 'go.mod', 'Package.swift', 'requirements.txt', 'Makefile'}
ID_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,79}\Z')


def git(path, *args, raw=False):
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/var/empty', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    try:
        result = subprocess.run(['/usr/bin/git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(path), *args], capture_output=True, text=True, timeout=5, env=env)
        return (result.stdout if raw else result.stdout.strip()) if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def three_months_ago(now):
    month = now.year * 12 + now.month - 1 - 3
    year, month = divmod(month, 12)
    month += 1
    return now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))


def project_paths(settings):
    """只在配置根目录与深度内发现项目，跳过链接、隐藏及生成目录。"""
    depth = min(8, int(settings.get('max_depth', 4)))
    seen = set()

    def walk(path, level, inside_project=False, owner=None):
        if path.is_symlink() or not path.is_dir():
            return
        try:
            entries = list(path.iterdir())
        except OSError:
            return
        is_git = (path/'.git').exists() and not (path/'.git').is_symlink()
        is_project = is_git or not inside_project and any(
            p.name in PROJECT_FILES or p.suffix in {'.xcodeproj', '.csproj', '.sln'} for p in entries if not p.is_symlink())
        candidate = path if is_git else owner or path
        if level > 0 and is_project and candidate not in seen:
            seen.add(candidate)
            yield candidate, is_git
        if level < depth:
            for entry in sorted(entries):
                if not entry.name.startswith('.') and entry.name not in EXCLUDE | GENERATED:
                    yield from walk(entry, level + 1, inside_project or is_project, owner or entry)

    for root in settings.get('scan_roots', []):
        yield from walk(Path(root).expanduser().absolute(), 0)


def local_modified(path, is_git, now):
    if is_git:
        # 包括暂存、未暂存、未跟踪文件；不读取文件内容，不统计被忽略的生成物。
        names = git(path, 'ls-files', '-z', '--modified', '--others', '--exclude-standard', raw=True)
        names += git(path, 'diff', '--cached', '--name-only', '--no-ext-diff', '--no-textconv', '-z', raw=True)
        files = (path/name for name in set(names.split('\0')) if name)
    else:
        def source_files():
            for directory, dirs, names in os.walk(path, followlinks=False):
                current = Path(directory)
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in EXCLUDE | GENERATED and not (current/d).is_symlink() and not (current/d/'.git').exists()]
                for name in names:
                    if not name.startswith('.'):
                        yield current/name
        files = source_files()
    latest = None
    for file in files:
        try:
            parts = file.relative_to(path).parts
            if any(p in EXCLUDE | GENERATED or not is_git and p.startswith('.') for p in parts):
                continue
            if any(parent.is_symlink() for parent in [file, *file.parents] if parent.is_relative_to(path)):
                continue
            # 删除没有文件时间，使用其最近仍存在的父目录时间作近似证据。
            while not file.exists() and file != path:
                file = file.parent
            stamp = datetime.fromtimestamp(file.stat().st_mtime, timezone.utc)
            if stamp <= now:
                latest = max(latest, stamp) if latest else stamp
        except (OSError, ValueError, OverflowError):
            continue
    return latest


def remote_id(raw):
    """只保存去掉凭证、query 和 fragment 的远程辅助标识。"""
    if not raw:
        return ''
    if '://' in raw:
        try:
            u = urlsplit(raw)
            if not u.hostname:
                return ''
            return (u.hostname + u.path).removesuffix('.git').rstrip('/').casefold()
        except ValueError:
            return ''
    match = re.fullmatch(r'(?:[^@/]+@)?([\w.-]+):([\w./-]+)', raw)
    return (match[1]+'/'+match[2]).removesuffix('.git').casefold() if match else ''


def metadata(path):
    for filename in ['package.json', 'pyproject.toml', 'README.md', 'readme.md', 'README', 'README.rst']:
        f = path / filename
        if f.is_symlink() or not f.is_file() or f.stat().st_size > 65536:
            continue
        try:
            with f.open('rb') as stream:
                raw = stream.read(65537)
            if len(raw)>65536:
                continue
            text = raw.decode('utf-8')
            if filename == 'package.json':
                package = json.loads(text)
                value = package.get('description', '') if isinstance(package, dict) else ''
            elif filename == 'pyproject.toml':
                value = tomllib.loads(text).get('project', {}).get('description', '')
            else:
                value = next((line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith(('#', '[', '!', '<', '`', '|', '-', '*'))), '')
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
        except (OSError, UnicodeError, ValueError):
            continue
    return ''


def check_projects(projects):
    """注册表的格式检查；读取和刷新写入前共用，保证刷新不会写出一份自己读不回来的注册表。"""
    seen = set()
    for p in projects:
        if not isinstance(p, dict) or not isinstance(p.get('id'), str) or not ID_RE.fullmatch(p['id']) or p['id'] in seen:
            raise ValueError('项目 ID 无效或重复')
        for field in ['aliases', 'keywords', 'worktrees']:
            if not isinstance(p.get(field, []), list) or not all(isinstance(x, str) for x in p.get(field, [])):
                raise ValueError('项目列表字段格式错误')
        if not isinstance(p.get('name'), str) or not isinstance(p.get('description', ''), str):
            raise ValueError('项目名称或简介格式错误')
        seen.add(p['id'])
    return projects


def unique_name(name, parent, digest, taken):
    """看板里项目显示名称必须唯一。不同位置的同名目录（两个 web、两个 server）
    依次加上上级目录名、短哈希来区分；已登记项目的名称从不改动，任务靠它关联项目。"""
    for candidate in [name, f'{parent}-{name}' if parent else '', f'{name}-{digest[:6]}', f'{name}-{digest}']:
        if candidate and candidate.casefold() not in taken:
            return candidate
    raise ValueError(f'项目 {name} 的显示名称无法去重，请人工核对注册表')


class Registry:
    def __init__(self, root):
        self.store = Storage(root)
        self.root = self.store.root

    def settings(self):
        value = self.store.yaml('config/settings.yaml', {})
        if not isinstance(value, dict):
            raise ValueError('settings.yaml 必须是映射')
        return value

    def list_projects(self):
        value = self.store.yaml('config/projects.yaml', {'projects': []})
        if not isinstance(value, dict) or not isinstance(value.get('projects'), list):
            raise ValueError('项目注册表格式错误')
        return check_projects(value['projects'])

    def allowed(self, path):
        roots = [Path(p).expanduser().resolve() for p in self.settings().get('scan_roots', [])]
        raw = Path(path).expanduser().absolute()
        if not raw.is_dir():
            return False
        # 不沿符号链接越过授权目录，也不进入生成物与敏感目录。
        for root in roots:
            if raw.is_relative_to(root):
                parts = raw.relative_to(root).parts
                current = root
                for part in parts:
                    current = current/part
                    if part in EXCLUDE or part.startswith('.') or current.is_symlink():
                        break
                else:
                    return len(parts) <= min(8, int(self.settings().get('max_depth', 4)))
        return False

    def discover(self):
        settings = self.settings()
        now = datetime.now(ZoneInfo(settings.get('timezone', 'America/Los_Angeles')))
        cutoff = three_months_ago(now).astimezone(timezone.utc)
        self.discovery_report = {'sources': [dict(source=s, available=True, records=0) for s in ['git', 'local_files']], 'ranking': 'latest_project_modification', 'cutoff': cutoff.isoformat(), 'selected': []}
        candidates = {}
        for raw, is_git in project_paths(settings):
            if not self.allowed(raw):
                continue
            top = git(raw, 'rev-parse', '--show-toplevel') if is_git else str(raw)
            if not top or not self.allowed(top):
                continue
            path = str(Path(top).resolve())
            if path in candidates:
                continue
            changes = []
            if is_git:
                stamps = git(raw, 'log', '--all', f'--since-as-filter={cutoff.isoformat()}', '--format=%ct').splitlines()
                times = [datetime.fromtimestamp(int(s), timezone.utc) for s in stamps if s.isdigit()]
                times = [t for t in times if cutoff <= t <= now]
                if times:
                    changes.append(('git', 'commit_time', max(times)))
            modified = local_modified(raw, is_git, now)
            if modified and cutoff <= modified <= now:
                changes.append(('local_files', 'file_mtime', modified))
            if not changes:
                continue
            common = git(raw, 'rev-parse', '--path-format=absolute', '--git-common-dir') if is_git else path
            if not common:
                continue
            remote = remote_id(git(raw, 'config', '--local', '--get', 'remote.origin.url')) if is_git else ''
            candidates[path] = dict(path=path, common_dir=str(Path(common).resolve()), remote=remote, last_active=max(c[2] for c in changes).isoformat(), activity_sources=[c[0] for c in changes], time_bases=[c[1] for c in changes])
        for source in self.discovery_report['sources']:
            source['records'] = sum(source['source'] in r['activity_sources'] for r in candidates.values())
        groups = []
        for record in candidates.values():
            common, remote = record['common_dir'], record['remote']
            related = [g for g in groups if common in g['common_dirs'] or remote and remote in g['remotes']]
            group = {'records': [record], 'common_dirs': [common], 'remotes': [remote] if remote else []}
            for previous in related:
                for key in group:
                    group[key].extend(previous[key])
                groups.remove(previous)
            groups.append(group)
        groups.sort(key=lambda g: min(r['path'] for r in g['records']))
        groups.sort(key=lambda g: max(r['last_active'] for r in g['records']), reverse=True)
        selected = groups
        for group in selected:
            # 仅对已入选项目提取简介。
            for record in group['records']:
                record['description'] = metadata(Path(record['path']))
        self.discovery_report['selected'] = [dict(paths=[r['path'] for r in g['records']], last_active=max(r['last_active'] for r in g['records'])) for g in selected]
        self.discovery_report['eligible_projects'] = len(groups)
        return selected

    def refresh_projects(self):
        with self.store.lock():
            old = self.list_projects()
            result, used = [], set()
            taken = {p['name'].casefold() for p in old}
            for group in self.discover():
                records = group['records']
                paths = [r['path'] for r in records]
                matches = [p for p in old if p['id'] not in used and (set([p.get('path', '')]+p.get('worktrees', [])) & set(paths) or set(p.get('common_dirs', [])) & set(group['common_dirs']) or set(p.get('remotes', [])) & set(group['remotes']))]
                if len(matches) > 1:
                    raise ValueError('多个已有项目对应同一仓库；请人工核对注册表后刷新')
                existing = matches[0] if matches else None
                records.sort(key=lambda r: (not Path(r['path'], '.git').is_dir(), len(Path(r['path']).parts), r['path']))
                main = next((r for r in records if existing and r['path'] == existing.get('path')), records[0])
                if existing:
                    project = dict(existing)
                    used.add(project['id'])
                else:
                    # ID 必须以字母或数字开头：_sandbox 这类目录名要把开头的下划线也去掉
                    slug = re.sub('[^a-z0-9_-]+', '-', Path(main['path']).name.lower()).strip('-_')[:55] or 'project'
                    key = group['remotes'][0] if group['remotes'] else main['common_dir']
                    digest = hashlib.sha256(key.encode()).hexdigest()
                    name = unique_name(Path(main['path']).name, Path(main['path']).parent.name, digest, taken)
                    taken.add(name.casefold())
                    project = dict(id=slug+'-'+digest[:10], name=name, aliases=[], keywords=[], description=main['description'], enabled=True)
                known_paths = [project.get('path', '')]+project.get('worktrees', [])
                paths = sorted(set(paths + [p for p in known_paths if p and self.allowed(p)]))
                project.update(path=main['path'], worktrees=sorted(set(paths)-{main['path']}), common_dirs=sorted(set(group['common_dirs'])), remotes=sorted(set(group['remotes'])), missing=False, last_active=max(r['last_active'] for r in records), activity_sources=sorted({s for r in records for s in r['activity_sources']}), time_bases=sorted({s for r in records for s in r['time_bases']}))
                result.append(project)
            for p in old:
                if p['id'] not in used and not any(x['id']==p['id'] for x in result):
                    result.append(dict(p, missing=not any(self.allowed(x) for x in [p.get('path', '')]+p.get('worktrees', []))))
            check_projects(result)
            self.store.write('config/projects.yaml', yaml.safe_dump({'projects': result}, allow_unicode=True, sort_keys=False), replace=True)
            return result

    def resolve(self, value):
        if not isinstance(value, str) or len(value)>100 or '/' in value or '\\' in value or '..' in value:
            raise ValueError('无效项目标识')
        matches = [p for p in self.list_projects() if value.casefold() in [str(x).casefold() for x in [p['id'], p['name']]+p.get('aliases', [])]]
        if len(matches) != 1:
            raise ValueError('项目不存在或标识有歧义，请使用 list_projects 返回的 ID')
        return matches[0]['id']

    def match_project(self, text):
        if not isinstance(text, str) or len(text) > 20000:
            raise ValueError('描述长度超限')
        text = text.casefold()
        candidates = []
        for p in self.list_projects():
            if not p.get('enabled', True) or p.get('missing', False):
                continue
            names = [p['id'], p['name']]+p.get('aliases', [])
            exact = [n for n in names if n and re.search(r'(?<![a-z0-9_])'+re.escape(n.casefold())+r'(?![a-z0-9_])', text)]
            keywords = [k for k in p.get('keywords', []) if k and k.casefold() in text]
            words = re.findall(r'[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}', p.get('description', '').casefold())
            description = [w for w in words if w in text]
            score = 100 if exact else min(85, 50+15*len(keywords)) if keywords else 25 if description else 0
            if score:
                level = 'exact' if exact else 'high' if len(keywords)>=2 else 'fuzzy'
                candidates.append(dict(id=p['id'], name=p['name'], level=level, score=score, reasons={'names':exact,'keywords':keywords,'description_terms':description[:5]}))
        if any(p['level']=='exact' for p in candidates):
            candidates = [p for p in candidates if p['level']=='exact']
        candidates.sort(key=lambda p:(-p['score'],p['id']))
        return dict(candidates=candidates, ambiguous=len(candidates)>1, level=candidates[0]['level'] if candidates else 'unrecognized')
