from pathlib import Path
import subprocess
import sys

import pytest
import yaml
from tests.test_system import repo
from tests.test_activity import setup_activity, add_activity


def test_tied_modification_deterministic_and_missing_status(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    stamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    monkeypatch.setenv('GIT_AUTHOR_DATE',stamp)
    monkeypatch.setenv('GIT_COMMITTER_DATE',stamp)
    from server.project_registry import Registry
    scan=tmp_path/'scan';scan.mkdir();root=tmp_path/'app';agent=setup_activity(root,scan)
    for name in ['zeta','alpha']:
        path=repo(scan/name);add_activity(root,path)
    (agent/'pi').rmdir()
    r=Registry(root)
    found=r.refresh_projects()
    assert [p['name'] for p in found]==['alpha','zeta']
    assert [s['source'] for s in r.discovery_report['sources']]==['git','local_files']
    # 目录不再存在才标 missing；不依赖是否还有活动记录。
    (scan/'alpha').rename(tmp_path/'removed')
    found=r.refresh_projects()
    assert next(p for p in found if p['name']=='alpha')['missing']
    assert next(p for p in found if p['name']=='zeta')['missing'] is False


def test_atomic_failure_keeps_original(tmp_path,monkeypatch):
    from server.storage import Storage
    store=Storage(tmp_path/'app')
    store.write('data/task.md','original')
    def fail(*args): raise OSError('synthetic disk error')
    monkeypatch.setattr('server.storage.os.replace',fail)
    with pytest.raises(OSError):store.write('data/task.md','new',replace=True)
    assert store.read('data/task.md')=='original'
    assert not list(store.safe('data').glob('.pending-*'))
    with pytest.raises(FileExistsError):store.write('data/task.md','must not replace')
    assert store.read('data/task.md')=='original'


def test_external_launch_minimal_path(tmp_path):
    from server.verify import verify_roundtrip
    import asyncio
    previous=Path.cwd()
    try:
        import os
        os.chdir(tmp_path)
        result=asyncio.run(verify_roundtrip(tmp_path/'app'))
        assert result['ok']
    finally:
        os.chdir(previous)


def test_read_only_activity_database(tmp_path):
    from server.project_registry import Registry
    scan=tmp_path/'scan';scan.mkdir();root=tmp_path/'app';agent=setup_activity(root,scan)
    path=repo(scan/'alpha');add_activity(root,path)
    db=agent/'codex.sqlite';before=db.read_bytes()
    Registry(root).refresh_projects()
    assert db.read_bytes()==before


def test_invalid_update_and_unknown_project_leave_task_unchanged(tmp_path):
    from server.task_manager import TaskManager
    manager=TaskManager(tmp_path/'app');t=manager.create_task()
    before=Path(t['path']).read_bytes()
    for changes in [{'id':'TASK-000001'}, {'status':'invalid'}, {'project_id':'../../bad'}]:
        with pytest.raises(ValueError):manager.update_task(t['id'],**changes)
    assert Path(t['path']).read_bytes()==before
    assert not list((manager.root/'data/.history').rglob('*.md'))


def test_multi_agent_threads_preserve_updates(tmp_path):
    from server.task_manager import TaskManager
    from concurrent.futures import ThreadPoolExecutor
    root=tmp_path/'app';m=TaskManager(root);task=m.create_task()
    def update(changes):
        return TaskManager(root).update_task(task['id'],**changes)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(update,[{'description':'synthetic change'},{'priority':'P1'}]))
    result=m.get_task(task['id'])
    assert result['description']=='synthetic change' and result['priority']=='P1'
    assert m.check_integrity()['ok']


def test_malformed_update_returns_validation_error(tmp_path):
    from server.task_manager import TaskManager
    m=TaskManager(tmp_path/'app');t=m.create_task()
    with pytest.raises(ValueError):m.update_task(t['id'],status=[])


def test_metadata_size_is_bounded_even_if_file_grows(tmp_path):
    # 所有元信息读取应有硬性字节上限，而不是只依赖预先 stat。
    from server.project_registry import metadata
    p=tmp_path/'project';p.mkdir()
    (p/'README.md').write_text('x'*70000)
    assert metadata(p)==''


def test_refresh_never_writes_a_registry_it_cannot_read(tmp_path):
    # 下划线开头的目录名、不同位置的同名目录，都曾让刷新写出一份之后所有命令都读不了的注册表。
    from server.project_registry import Registry, ID_RE
    from server.task_manager import TaskManager
    scan=tmp_path/'scan';scan.mkdir();root=tmp_path/'app';setup_activity(root,scan)
    for path in [scan/'_sandbox',scan/'groupa'/'web',scan/'groupb'/'web']:
        path.parent.mkdir(exist_ok=True);repo(path)
    r=Registry(root)
    found=r.refresh_projects()
    assert len(found)==3 and all(ID_RE.fullmatch(p['id']) for p in found)
    assert len({p['name'].casefold() for p in found})==3
    # 两个 web 谁先登记取决于提交时间，不固定：先到的保留原名，后到的加上上级目录名。
    webs={p['name']:p for p in found if Path(p['path']).name=='web'}
    renamed=next(p for name,p in webs.items() if name!='web')
    assert set(webs)=={'web',Path(renamed['path']).parent.name+'-web'}
    assert [p['name'] for p in r.list_projects()]==[p['name'] for p in found]
    assert TaskManager(root).create_task(project_id=renamed['name'],title='同名项目也能建卡')['project']==renamed['id']
    again=Registry(root).refresh_projects()
    assert sorted((p['id'],p['name']) for p in again)==sorted((p['id'],p['name']) for p in found)


def test_broken_yaml_config_is_a_readable_error(tmp_path):
    from server.project_registry import Registry
    root=tmp_path/'app';(root/'config').mkdir(parents=True)
    (root/'config/projects.yaml').write_text('projects:\n- id: editr\n  name: editr\n   aliases: [oops\n')
    with pytest.raises(ValueError,match='config/projects.yaml 不是合法的 YAML'):Registry(root).list_projects()
    shown=subprocess.run([sys.executable,'-m','server.cli','--home',str(root),'projects'],capture_output=True,text=True)
    assert shown.returncode==2 and 'Traceback' not in shown.stderr and '不是合法的 YAML' in shown.stderr
    (root/'config/projects.yaml').write_text(yaml.safe_dump({'projects':[{'id':123,'name':'数字 ID'}]}))
    with pytest.raises(ValueError,match='项目 ID 无效'):Registry(root).list_projects()


def test_sources_stay_python_311_compatible():
    # pyproject 声明支持 3.11；f-string 的表达式里带反斜杠要 3.12 才合法，在 3.11 上整个模块导入不了。
    import io, tokenize
    if not hasattr(tokenize,'FSTRING_START'):return  # 3.11 自己会在导入时报语法错误
    root=Path(__file__).resolve().parents[1]
    offenders=[]
    for path in sorted([*(root/'server').glob('*.py'),*(root/'scripts').glob('*.py')]):
        depth=0
        for token in tokenize.generate_tokens(io.StringIO(path.read_text(encoding='utf-8')).readline):
            if token.type==tokenize.FSTRING_START:depth+=1
            elif token.type==tokenize.FSTRING_END:depth-=1
            elif depth and '\\' in token.string and (token.type!=tokenize.FSTRING_MIDDLE or depth>1):
                offenders.append(f'{path.name}:{token.start[0]}')
    assert not offenders,offenders
