"""仅 stdio；不开放网络端口或任意文件 / Shell 工具。"""
import argparse
import os
from pathlib import Path
from typing import Literal
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from .project_registry import Registry
from .task_manager import TaskManager
from .backlog import sync_projects
from .contract import handoff_template
from .flow import Flow


def default_root():
    return Path(os.environ.get('PROJECT_INBOX_HOME',Path(__file__).resolve().parents[1]))


def invoke(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except ValueError as error:
        raise ToolError(str(error)) from error
    except OSError as error:
        raise ToolError('本地文件操作失败，请检查权限或运行 project-inbox check') from error


def build_server(root):
    flow=Flow(root)
    tasks=flow.tasks
    registry=Registry(root)
    mcp=MCPServer('Project Inbox',instructions='本地项目任务收件箱。项目不明确可进入全局 inbox；先查重复再创建；不自动合并。讨论结束用 get_handoff_template + submit_handoff 交交接单；执行会话用 claim_tasks 领取，做完用 submit_report 汇报。状态只能通过这几个工具推进，验收和退回只有用户能做。',log_level='WARNING')

    @mcp.tool()
    def list_projects() -> list[dict]:
        """列出已注册项目及别名、简介、可用状态。"""
        return invoke(registry.list_projects)

    @mcp.tool()
    def match_project(text: str) -> dict:
        """按名称、别名、关键词匹配；有歧义时返回全部候选。"""
        return invoke(registry.match_project, text)

    @mcp.tool()
    def create_task(project_id: str | None=None,title: str='待整理任务',type: Literal['bug','feature','improvement','investigation','idea']='bug',description: str='',expected_behavior: str='',reproduction_steps: str='',priority: Literal['P0','P1','P2','P3']='P2',tags: list[str] | None=None) -> dict:
        """保存新任务，返回实际任务 ID。项目不明确传 null 进入全局 inbox。"""
        return invoke(tasks.create_task, project_id,title,type,description,expected_behavior,reproduction_steps,priority,tags)

    @mcp.tool()
    def search_tasks(query: str='',project_id: str | None=None,type: str | None=None,status: str | None=None,priority: str | None=None,limit: int=50) -> list[dict]:
        """搜索任务，含归档；返回精简结果。未解决需排除 done 和 archived。"""
        return invoke(tasks.search_tasks, query,project_id,type,status,priority,limit)

    @mcp.tool()
    def get_task(task_id: str) -> dict:
        """按 ID 读取完整任务。"""
        return invoke(tasks.get_task, task_id)

    @mcp.tool()
    def update_task(task_id: str,changes: dict) -> dict:
        """修改指定字段；changes 可含 title/description/priority/tags/project_id/type/expected_behavior/reproduction_steps/notes/commits。不能改 status：状态只走 claim_tasks / submit_report / release_task。notes 只能追加（原内容保留在前）。project_id=null 移至全局 inbox。"""
        return invoke(flow.agent_update, task_id,changes)

    @mcp.tool()
    def find_duplicates(title: str,project_id: str | None=None,description: str='',limit: int=5) -> list[dict]:
        """查找同项目相似任务；不自动合并、修改或删除。"""
        return invoke(tasks.find_duplicates, title,project_id,description,limit)

    @mcp.tool()
    def archive_task(task_id: str) -> dict:
        """归档任务，保留 ID 和文件，不永久删除。带交接单且未完成的卡不能归档，只能由用户取消。"""
        return invoke(flow.agent_archive, task_id)

    @mcp.tool()
    def refresh_projects() -> dict:
        """纳入配置目录内最近三个月有 Git 提交或本地文件改动的全部项目，保留已有信息。"""
        projects=invoke(registry.refresh_projects)
        invoke(sync_projects,root,projects)
        return {'projects':projects,'discovery':registry.discovery_report}

    @mcp.tool()
    def get_project_tasks(project_id: str,status: str | None=None,limit: int=50) -> list[dict]:
        """列出项目任务，可按状态筛选。"""
        return invoke(tasks.get_project_tasks, project_id,status,limit)

    @mcp.tool()
    def get_handoff_template() -> dict:
        """交接单 handoff/v1 的空表、字段说明和填好的样例。讨论会话结束前先调用它。"""
        return handoff_template()

    @mcp.tool()
    def submit_handoff(handoff: dict,author: str) -> dict:
        """提交交接单。缺字段直接退回并说明怎么补；合格就建卡（或带 task_id 补到已有卡）并放进待办。有 open_questions 时留在收件箱、标「待确认」。author 填你自己的名字，例如 claude-code。"""
        return invoke(flow.submit_handoff, handoff,author)

    @mcp.tool()
    def claim_tasks(agent: str,task_ids: list[str] | None=None,project_id: str | None=None,limit: int=1,here: str | None=None) -> dict:
        """领取待办里带合格交接单的卡：按编号领，或按项目领前 limit 张（最多 5）。程序在项目的 .worktrees/task-编号 建好工作区并返回领取包（交接单、工作区路径、截图目录、规矩、空汇报单）。同一张卡不能被两个会话同时领。会话本身已在该项目的独立 worktree 里（如 Claude Code 桌面版的 .claude/worktrees/…）时，here 填会话目录的绝对路径、只领一张，程序就在那里开任务分支。"""
        return invoke(flow.claim_tasks, agent,task_ids,project_id,limit,here)

    @mcp.tool()
    def release_task(task_id: str,reason: str,agent: str) -> dict:
        """做不了时把卡放回待办，必须写原因。工作区保留，下次领取沿用。"""
        return invoke(flow.release_task, task_id,reason,agent)

    @mcp.tool()
    def get_report_template(task_id: str) -> dict:
        """某张卡的汇报单 report/v1 空表和样例，验收编号已按交接单填好。"""
        return invoke(flow.report_template, task_id)

    @mcp.tool()
    def submit_report(report: dict,agent: str) -> dict:
        """交汇报单。程序当场检查格子、截图文件、任务分支上的提交和未提交改动，并在工作区重跑交接单的测试命令；全部通过才进待验收，不过就拒收并说明原因，卡片停在进行中。outcome=blocked 时标为受阻。"""
        return invoke(flow.submit_report, report,agent)

    return mcp


def main():
    parser=argparse.ArgumentParser(description='Project Inbox stdio MCP Server')
    parser.add_argument('--home',type=Path,default=default_root())
    args=parser.parse_args()
    build_server(args.home).run(transport='stdio')


if __name__=='__main__':
    main()
