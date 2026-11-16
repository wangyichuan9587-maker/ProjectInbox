"""兼容原有 MCP / CLI；所有任务写入均交给官方 Backlog.md。"""
from datetime import datetime,timezone
from difflib import SequenceMatcher
from pathlib import Path
import os
import re
from zoneinfo import ZoneInfo
import yaml
from .backlog import sync_projects,STATES,TASK_TYPES
from .project_registry import Registry

TASK_ID=re.compile(r'TASK-[1-9][0-9]*(?:\.[1-9][0-9]*)*\Z')
TYPES=set(TASK_TYPES)
STATUSES=set(STATES)|{'archived'}
PRIORITIES={'P0','P1','P2','P3'}


def text(value,limit,field):
    if not isinstance(value,str) or len(value)>limit or '\x00' in value:
        raise ValueError(f'{field} 必须是长度不超过 {limit} 的文本')
    return value


def created_id(output):
    """从 Backlog 新建任务的回显里取编号。

    回显先是「File: …/task-5 - 标题.md」，再是「Task TASK-5 - 标题」。标题里可能提到别的任务
    （「修复 TASK-2 引入的回归」），所以不能取全文第一个 TASK-N，只认标题行的行首和文件名。
    """
    match=re.search(r'^Task (TASK-[0-9]+(?:\.[0-9]+)*) - ',output,re.M) or re.search(r'^File: .*/(task-[0-9]+(?:\.[0-9]+)*) - [^/\n]*$',output,re.M|re.I)
    if not match:raise ValueError('未从 Backlog 响应获得 ID，请查询后再重试')
    return match[1].upper()


class TaskManager:
    def __init__(self,root):
        self.registry=Registry(root)
        self.backend=sync_projects(root,self.registry.list_projects())
        self.store=self.backend.store
        self.root=self.store.root

    def _id(self,value):
        if not isinstance(value,str) or not TASK_ID.fullmatch(value):raise ValueError('无效任务 ID')
        return value

    def _project(self,value):
        if value is None:return None
        project_id=self.registry.resolve(value)
        return next(p for p in self.registry.list_projects() if p['id']==project_id)

    def _time(self,value):
        if not value:return None
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo(self.registry.settings().get('timezone','America/Los_Angeles'))).isoformat()

    def _section(self,body,name):
        match=re.search(r'<!-- SECTION:'+name+r':BEGIN -->\n?(.*?)\n?<!-- SECTION:'+name+r':END -->',body,re.S)
        return match[1].strip() if match else ''

    def _description(self,description,expected='',steps=''):
        if not expected and not steps:return description
        return '\n\n'.join(['## 问题描述\n\n'+description,'## 期望行为\n\n'+expected,'## 复现步骤\n\n'+steps])

    def _split_description(self,body):
        sections=re.split(r'^## (问题描述|期望行为|复现步骤)\n\n',body,flags=re.M)
        if len(sections)==7 and sections[1::2]==['问题描述','期望行为','复现步骤']:
            return [x.strip() for x in sections[2::2]]
        return body,'',''

    def _parse(self,path,archived):
        raw=self.store.read(path)
        if not raw.startswith('---\n') or '\n---\n' not in raw[4:]:raise ValueError('缺少 Front Matter')
        header,body=raw[4:].split('\n---\n',1)
        meta=yaml.safe_load(header)
        if not isinstance(meta,dict):raise ValueError('任务元信息无效')
        task_id=self._id(meta.get('id'))
        if not Path(path).name.casefold().startswith(task_id.casefold()+' - '):raise ValueError('任务 ID 与文件名不一致')
        title=text(meta.get('title'),500,'title')
        if not title.strip() or '\n' in title:raise ValueError('标题必须是单行非空文本')
        if meta.get('onStatusChange') or meta.get('on_status_change'):raise ValueError('不允许任务回调命令')
        status='archived' if archived else next((k for k,v in STATES.items() if v==meta.get('status')),None)
        if status is None:raise ValueError('状态不在 Project Inbox 的看板列中')
        desc,expected,steps=self._split_description(self._section(body,'DESCRIPTION'))
        project=meta.get('project')
        mapped=[p for p in self.registry.list_projects() if p['name']==project]
        project_id=mapped[0]['id'] if len(mapped)==1 else project
        created=self._time(meta.get('created_date'))
        if created is None:raise ValueError('缺少创建时间')
        return dict(id=task_id,title=title,project=project_id,project_name=project,type=meta.get('type') or 'bug',status=status,priority=str(meta.get('priority') or 'p2').upper(),created_at=created,updated_at=self._time(meta.get('updated_date')) or created,tags=meta.get('labels',[]),description=desc,expected_behavior=expected,reproduction_steps=steps,notes=self._section(body,'NOTES'),plan=self._section(body,'PLAN'),final_summary=self._section(body,'FINAL_SUMMARY'),acceptance=self._acceptance(body),assignee=[str(a) for a in meta.get('assignee') or []],commits=[r[7:] for r in meta.get('references',[]) if isinstance(r,str) and r.startswith('commit:')],path=str(self.store.safe(path)),content=raw)

    def _acceptance(self,body):
        match=re.search(r'<!-- AC:BEGIN -->\n?(.*?)<!-- AC:END -->',body,re.S)
        if not match:return []
        return [dict(index=int(m[2]),checked=m[1]=='x',text=m[3].strip()) for m in re.finditer(r'^- \[([ x])\] #([0-9]+) (.*)$',match[1],re.M)]

    def scan(self):
        tasks,issues,seen=[],[],set()
        self.backend.verify_config()
        for base,archived in [('backlog/tasks',False),('backlog/completed',True),('backlog/archive/tasks',True)]:
            if not self.store.safe(base).exists():continue
            for current,dirs,files in os.walk(self.store.safe(base),followlinks=False):
                for d in list(dirs):
                    if (Path(current)/d).is_symlink():
                        issues.append({'path':str(Path(current)/d),'error':'数据目录包含符号链接'});dirs.remove(d)
                for name in files:
                    if not name.endswith('.md'):continue
                    if len(tasks)+len(issues)>=10000:raise ValueError('任务数量超过读取上限 10000')
                    relative=(Path(current)/name).relative_to(self.root)
                    try:
                        task=self._parse(relative,archived)
                        if task['id'] in seen:raise ValueError('重复任务 ID')
                        seen.add(task['id']);tasks.append(task)
                    except (OSError,ValueError,TypeError,KeyError,yaml.YAMLError) as error:
                        issues.append({'path':str(relative),'error':str(error)[:300]})
        return tasks,issues

    def healthy_tasks(self):
        tasks,issues=self.scan()
        if issues:raise ValueError('数据完整性异常，请运行 project-inbox check')
        return tasks

    def _validate(self,task,only=None):
        """only 为 None 时检查全部字段（新建）；更新时只检查这次要改的字段。
        否则程序追加或看板里写长了的旧内容，会让无关的修改（例如只改状态）也被拒绝。"""
        wanted=lambda field:only is None or field in only
        if wanted('title'):
            title=text(task['title'],500,'title')
            if not title.strip() or '\n' in title or '\r' in title:raise ValueError('标题必须是非空单行文本')
        for key,choices in [('type',TYPES),('status',STATUSES),('priority',PRIORITIES)]:
            if wanted(key) and (not isinstance(task[key],str) or task[key] not in choices):raise ValueError('任务类型、状态或优先级无效')
        for field in ['description','expected_behavior','reproduction_steps','notes']:
            if wanted(field):text(task.get(field,''),20000,field)
        for field,maximum,length in [('tags',50,100),('commits',100,200)]:
            if not wanted(field):continue
            values=task.get(field,[])
            if not isinstance(values,list) or len(values)>maximum:raise ValueError(field+' 列表超限')
            for value in values:
                text(value,length,field)
                if ',' in value or '\n' in value:raise ValueError(field+' 不能含逗号或换行')

    def create_task(self,project_id=None,title='待整理任务',type='bug',description='',expected_behavior='',reproduction_steps='',priority='P2',tags=None):
        project=self._project(project_id)
        task=dict(title=title,type=type,status='todo' if project else 'inbox',priority=priority,tags=[] if tags is None else tags,description=description,expected_behavior=expected_behavior,reproduction_steps=reproduction_steps)
        self._validate(task)
        with self.store.lock():
            return self.get_task(self._create(task,project),_locked=True)

    def _create(self,task,project,extra=()):
        """调用方已持有锁且已校验字段；返回新任务 ID。"""
        self.healthy_tasks()
        args=['task','create','--plain','--type='+task['type'],'--status='+STATES[task['status']],'--priority='+task['priority'],'--description='+self._description(task['description'],task.get('expected_behavior',''),task.get('reproduction_steps',''))]
        if project:args.append('--project='+project['name'])
        if task['tags']:args.append('--labels='+','.join(task['tags']))
        return created_id(self.backend.run(*args,*extra,'--',task['title']))

    def get_task(self,task_id,_locked=False):
        self._id(task_id)
        if not _locked:
            with self.store.lock():return self.get_task(task_id,_locked=True)
        found=next((t for t in self.healthy_tasks() if t['id']==task_id),None)
        if found is None:raise ValueError('任务不存在')
        return found

    def update_task(self,task_id,/,**changes):
        # task_id 只按位置传：changes 里出现同名键时走下面的字段检查，而不是抛 TypeError
        self._id(task_id)
        allowed={'project_id','title','type','status','description','expected_behavior','reproduction_steps','notes','priority','tags','commits'}
        if not changes or set(changes)-allowed:raise ValueError('更新字段为空或含禁止修改字段')
        if changes.get('status')=='archived' and len(changes)==1:return self.archive_task(task_id)
        with self.store.lock():
            original=self.get_task(task_id,_locked=True)
            if original['status']=='archived':raise ValueError('归档任务为只读，不自动恢复或覆盖')
            task=dict(original,**changes);self._validate(task,set(changes))
            if task['status']=='archived':raise ValueError('请单独调用归档工具')
            args=['task','edit',task_id,'--plain']
            for field,flag in [('title','title'),('type','type'),('priority','priority'),('notes','notes')]:
                if field in changes:args.append('--'+flag+'='+changes[field])
            if 'status' in changes:args.append('--status='+STATES[changes['status']])
            if 'project_id' in changes:
                project=self._project(changes['project_id']);args.append('--project='+(project['name'] if project else ''))
            if set(changes)&{'description','expected_behavior','reproduction_steps'}:
                args.append('--description='+self._description(task['description'],task['expected_behavior'],task['reproduction_steps']))
            if 'tags' in changes:
                args.append('--label='+','.join(changes['tags']) if changes['tags'] else '--clear-labels')
            if 'commits' in changes:
                # 只替换 commit: 引用，保留页面中添加的其他参考链接。
                raw=yaml.safe_load(original['content'].split('---',2)[1])
                refs=[r for r in raw.get('references',[]) if not str(r).startswith('commit:')]+['commit:'+c for c in changes['commits']]
                args.append('--ref='+','.join(refs) if refs else '--clear-refs')
            self.backend.run(*args)
            return self.get_task(task_id,_locked=True)

    def archive_task(self,task_id):
        self._id(task_id)
        with self.store.lock():
            current=self.get_task(task_id,_locked=True)
            if current['status']!='archived':
                self.backend.run('task','complete' if current['status']=='done' else 'archive',task_id)
            return self.get_task(task_id,_locked=True)

    def search_tasks(self,query='',project_id=None,type=None,status=None,priority=None,limit=50):
        text(query,2000,'query')
        if not isinstance(limit,int) or not 1<=limit<=200:raise ValueError('limit 必须在 1–200 之间')
        for value,choices in [(type,TYPES),(status,STATUSES),(priority,PRIORITIES)]:
            if value is not None and (not isinstance(value,str) or value not in choices):raise ValueError('筛选条件无效')
        project=self._project(project_id)
        with self.store.lock():tasks=self.healthy_tasks()
        result=[]
        for task in sorted(tasks,key=lambda t:(t['updated_at'],t['id']),reverse=True):
            if project and task['project']!=project['id'] or type and task['type']!=type or status and task['status']!=status or priority and task['priority']!=priority:continue
            if query.casefold() not in ' '.join(str(task.get(k,'')) for k in ['title','description','expected_behavior','reproduction_steps','notes','tags']).casefold():continue
            result.append({k:task[k] for k in ['id','project','project_name','title','type','status','priority','updated_at','tags']})
        return result[:limit]

    def get_project_tasks(self,project_id,status=None,limit=50):
        return self.search_tasks(project_id=project_id,status=status,limit=limit)

    def find_duplicates(self,title,project_id=None,description='',limit=5):
        text(title,500,'title');text(description,20000,'description')
        if not isinstance(limit,int) or not 1<=limit<=20:raise ValueError('limit 必须在 1–20 之间')
        project=self._project(project_id)
        with self.store.lock():tasks=self.healthy_tasks()
        result=[]
        normalize=lambda s:''.join(re.findall(r'\w+',s.casefold()))
        for task in tasks:
            if task['project']!=(project['id'] if project else None):continue
            score=max(SequenceMatcher(None,normalize(title),normalize(task['title'])).ratio(),SequenceMatcher(None,normalize(title+' '+description),normalize(task['title']+' '+task['description'])).ratio())
            if score>=0.45:result.append(dict(id=task['id'],title=task['title'],status=task['status'],score=round(score,3),reason='同项目标题或正文相似'))
        return sorted(result,key=lambda t:(-t['score'],t['id']))[:limit]

    def check_integrity(self):
        with self.store.lock():tasks,issues=self.scan()
        return dict(ok=not issues,tasks=len(tasks),issues=issues,backend='backlog.md@1.53.0-project-inbox.8')
