"""命令行只转交共享业务层，不另外实现存储。"""
import argparse
import json
import os
from pathlib import Path
import sys
import yaml
from .main import default_root
from .project_registry import Registry
from .task_manager import TaskManager


def read_form(path):
    """读交接单 / 汇报单文件，YAML 或 JSON 都行；- 表示从标准输入读。"""
    raw=sys.stdin.read(256*1024+1) if path=='-' else Path(path).expanduser().read_text(encoding='utf-8')
    if len(raw)>256*1024:raise ValueError('文件超过 256KB')
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise ValueError('文件不是合法的 YAML / JSON：'+str(error)[:300]) from error


def main():
    p=argparse.ArgumentParser(description='本地项目任务收件箱（JSON 输出）')
    p.add_argument('--home',type=Path,default=default_root())
    sub=p.add_subparsers(dest='command',required=True)
    for name in ['list','search']:
        cmd=sub.add_parser(name)
        if name=='search':
            cmd.add_argument('query')
        cmd.add_argument('--project',dest='project_id')
        cmd.add_argument('--type');cmd.add_argument('--status');cmd.add_argument('--priority')
        cmd.add_argument('--limit',type=int,default=50)
    for name in ['show','done','archive']:
        sub.add_parser(name).add_argument('task_id')
    handoff=sub.add_parser('handoff',help='生成一个或多个任务的 Agent 交接指令')
    handoff.add_argument('--mode',choices=['execute','research'],default='execute',help='交接模式：execute 执行，research 只读调研')
    handoff.add_argument('task_ids',nargs='+')
    handoff.add_argument('--deploy',action='store_true',help='授权 Agent 完成后直接合并、推送、部署')
    create=sub.add_parser('create')
    create.add_argument('title',nargs='?',default='待整理任务')
    create.add_argument('--project',dest='project_id');create.add_argument('--type',default='bug')
    create.add_argument('--priority',default='P2');create.add_argument('--description',default='')
    create.add_argument('--expected-behavior',default='');create.add_argument('--reproduction-steps',default='')
    create.add_argument('--tag',dest='tags',action='append')
    note=sub.add_parser('note',help='往卡片备注末尾追加一段，不改原有内容');note.add_argument('task_id');note.add_argument('--text',required=True)
    update=sub.add_parser('update');update.add_argument('task_id');update.add_argument('--changes',required=True,help='JSON 对象，例如 {"status":"done"}')
    for name in ['refresh','projects','check']:
        sub.add_parser(name)
    sub.add_parser('handoff-template',help='交接单 handoff/v1 的空表和样例')
    submit=sub.add_parser('submit-handoff',help='提交交接单（和 MCP submit_handoff 同一个检查）')
    submit.add_argument('--file',required=True,help='YAML 或 JSON 文件，- 表示标准输入');submit.add_argument('--author',required=True,help='你的名字，例如 pi')
    claim=sub.add_parser('claim',help='领取带交接单的待办卡，程序建工作区')
    claim.add_argument('task_ids',nargs='*');claim.add_argument('--agent',required=True)
    claim.add_argument('--project',dest='project_id');claim.add_argument('--limit',type=int,default=1)
    claim.add_argument('--here',help='会话自己所在的 worktree 目录：在那里开任务分支，不另建 .worktrees/（一次只领一张）')
    release=sub.add_parser('release',help='做不了时放回待办')
    release.add_argument('task_id');release.add_argument('--agent',required=True);release.add_argument('--reason',required=True)
    sub.add_parser('report-template',help='某张卡的汇报单空表').add_argument('task_id')
    report=sub.add_parser('submit-report',help='交汇报单，程序当场核验')
    report.add_argument('--file',required=True,help='YAML 或 JSON 文件，- 表示标准输入');report.add_argument('--agent',required=True)
    accept=sub.add_parser('accept',help='（用户）验收通过：快进合并任务分支并删除工作区')
    accept.add_argument('task_id');accept.add_argument('--no-merge',action='store_true',help='只标完成，不合并、不删工作区')
    reject=sub.add_parser('reject',help='（用户）退回：原卡归档留档，生成沿用同一工作区的返工卡')
    reject.add_argument('task_id');reject.add_argument('--comment',required=True)
    cancel=sub.add_parser('cancel',help='（用户）取消：删除干净的工作区，分支保留，卡片归档')
    cancel.add_argument('task_id');cancel.add_argument('--reason',default='')
    space=sub.add_parser('workspace',help='扫描或清理项目旁边多出来的工作区和复制品')
    space.add_argument('action',choices=['scan','clean'])
    space.add_argument('--apply',action='store_true',help='clean 时真正执行；不加只预演')
    board=sub.add_parser('board',help='启动本机 Backlog.md 看板，Ctrl+C 停止')
    board.add_argument('--port',type=int,default=6420)
    board.add_argument('--no-open',action='store_true')
    args=vars(p.parse_args());root=args.pop('home');command=args.pop('command')
    try:
        from .flow import Flow
        from .contract import handoff_template
        flow=Flow(root);tasks=flow.tasks;registry=Registry(root)
        if command in ['list','search']:
            result=tasks.search_tasks(**args)
        elif command=='show': result=tasks.get_task(**args)
        elif command=='handoff':
            from .handoff import agent_handoff
            result=agent_handoff(root,args['task_ids'],args['deploy'],args['mode'])
        elif command=='create': result=tasks.create_task(**args)
        elif command=='done': result=flow.done(args['task_id'])
        elif command=='note': result=flow.note(args['task_id'],args['text'])
        elif command=='handoff-template': result=handoff_template()
        elif command=='submit-handoff': result=flow.submit_handoff(read_form(args['file']),args['author'])
        elif command=='claim': result=flow.claim_tasks(args['agent'],args['task_ids'] or None,args['project_id'],args['limit'],args['here'])
        elif command=='release': result=flow.release_task(args['task_id'],args['reason'],args['agent'])
        elif command=='report-template': result=flow.report_template(args['task_id'])
        elif command=='submit-report': result=flow.submit_report(read_form(args['file']),args['agent'])
        elif command=='accept': result=flow.accept(args['task_id'],merge=not args['no_merge'])
        elif command=='reject': result=flow.reject(args['task_id'],args['comment'])
        elif command=='cancel': result=flow.cancel(args['task_id'],args['reason'])
        elif command=='workspace':
            result={'folders':flow.workspace_scan()} if args['action']=='scan' else flow.workspace_clean(args['apply'])
        elif command=='archive': result=tasks.archive_task(**args)
        elif command=='update':
            changes=json.loads(args['changes'])
            if not isinstance(changes,dict): raise ValueError('changes 必须是 JSON 对象')
            flow.guard_cli_status(args['task_id'],changes)
            result=tasks.update_task(args['task_id'],**changes)
        elif command=='refresh':
            result={'projects':registry.refresh_projects(),'discovery':registry.discovery_report}
            from .backlog import sync_projects
            sync_projects(root,result['projects'])
        elif command=='projects': result=registry.list_projects()
        elif command=='board':
            if not 1024<=args['port']<=65535:raise ValueError('端口需在 1024–65535 之间')
            if not tasks.check_integrity()['ok']:raise ValueError('先恢复数据完整性，再启动看板')
            launch=[str(tasks.backend.binary),'browser','--port',str(args['port'])]
            if args['no_open']:launch.append('--no-open')
            os.chdir(tasks.root)
            os.execve(tasks.backend.binary,launch,{'PATH':'/usr/bin:/bin','BACKLOG_CWD':str(tasks.root)})
        else:
            result=tasks.check_integrity()
            if result['ok']:result['warnings']=flow.consistency()
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if command=='check' and not result['ok']: return 1
        return 0
    except (ValueError,OSError) as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
