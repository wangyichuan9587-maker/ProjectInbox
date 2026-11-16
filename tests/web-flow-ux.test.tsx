import { expect, test } from 'bun:test';
import React, { act } from '../vendor/backlog-source/node_modules/react';
import { renderToStaticMarkup } from '../vendor/backlog-source/node_modules/react-dom/server';
import { createRoot } from '../vendor/backlog-source/node_modules/react-dom/client';
import { JSDOM } from '../vendor/backlog-source/node_modules/jsdom';
import Board from '../vendor/backlog-source/src/web/components/Board';
import TaskDetailsModal from '../vendor/backlog-source/src/web/components/TaskDetailsModal';
import { ThemeProvider } from '../vendor/backlog-source/src/web/contexts/ThemeContext';
import { apiClient } from '../vendor/backlog-source/src/web/lib/api';
import { blockedReason, flowNextStep } from '../vendor/backlog-source/src/web/utils/flow-next-step';

const statuses = ['Inbox','To Do','In Progress','Review','Blocked','Done'];
const criteria = [{index:1,text:'[A1][必须看到] 合成条件一',checked:true},{index:2,text:'[A2][不能破坏] 合成条件二',checked:false}];
const base = {assignee:[],labels:['交接单'],dependencies:[],createdDate:'2026-01-01',type:'feature'};
const report = '### 汇报单 report/v1\n**改了什么：** 合成改动\n\n**在哪看：** 合成页面';
const blockedReport = '### 汇报单 report/v1\n**受阻：** 需要合成的测试账号\n**改了什么：** 做到一半';
const tasks = [
 {...base,id:'TASK-1',title:'Synthetic waiting',status:'Inbox',labels:['交接单','待确认']},
 {...base,id:'TASK-2',title:'Synthetic ready',status:'To Do'},
 {...base,id:'TASK-3',title:'Synthetic review',status:'Review',acceptanceCriteriaItems:criteria,finalSummary:report},
 {...base,id:'TASK-4',title:'Synthetic blocked',status:'Blocked',finalSummary:blockedReport},
 {...base,id:'TASK-5',title:'Synthetic done',status:'Done',finalSummary:report},
 {...base,id:'TASK-6',title:'Synthetic legacy review',status:'Review',labels:[]},
];
const boardProps = {tasks,statuses,milestones:[],availableLabels:[],milestoneEntities:[],archivedMilestones:[],isLoading:false,laneMode:'none',onLaneChange:()=>{},onEditTask:()=>{},onNewTask:()=>{},availableTypes:['bug','feature','improvement','investigation','idea'],availablePriorities:['P0','P1','P2','P3'],onFiltersChange:()=>{}};

test('next step is only offered when the card is waiting for the user',()=>{
 const step = (id:string) => flowNextStep(tasks.find(t => t.id === id) as any);
 expect(step('TASK-3')?.commands.map(c => c.command)).toEqual(['project-inbox accept TASK-3','project-inbox reject TASK-3 --comment "写一句意见"']);
 expect(step('TASK-4')?.commands.map(c => c.command)).toEqual(['project-inbox cancel TASK-4 --reason "写一句原因"']);
 expect(step('TASK-4')?.detail).toContain('task_id: TASK-4');
 expect(step('TASK-1')?.title).toContain('等你确认');
 expect(step('TASK-1')?.commands).toEqual([]);
 for (const id of ['TASK-2','TASK-5','TASK-6']) expect(step(id)).toBeNull();
 expect(flowNextStep({...tasks[1],status:'In Progress'} as any)).toBeNull();
 expect(flowNextStep(null)).toBeNull();
 expect(blockedReason(blockedReport)).toBe('需要合成的测试账号');
 expect(blockedReason(report)).toBe('');
 expect(blockedReason(undefined)).toBe('');
});

test('board shows who is waiting for the user without opening cards',()=>{
 const doc = new JSDOM(renderToStaticMarkup(React.createElement(Board,boardProps))).window.document;
 const card = (id:string) => [...doc.querySelectorAll('[draggable="true"], [draggable]')].find(e => e.textContent?.includes(id)) ?? doc.body;
 // 六列最小 12.75rem：1440 宽、侧栏展开时收件箱到受阻不用横向滚动
 expect(doc.querySelectorAll('div.flex-1.min-w-\\[12\\.75rem\\]').length).toBe(6);
 expect(card('TASK-4').textContent).toContain('受阻：需要合成的测试账号');
 expect(card('TASK-5').textContent).not.toContain('受阻：');
 const waiting = [...doc.querySelectorAll('span')].find(e => e.textContent === '待确认');
 expect(waiting?.className).toContain('bg-amber-100');
 expect([...doc.querySelectorAll('span')].find(e => e.textContent === '交接单')?.className).toContain('bg-gray-100');
 // 待验收的卡也显示验收进度，位置在卡片底部
 const progress = card('TASK-3').querySelector('[data-acceptance-criteria-progress]');
 expect(progress?.textContent).toBe('1/2');
 expect(progress?.getAttribute('title')).toBe('2 条验收条件，已勾选 1 条');
 expect(doc.body.textContent).toContain('收起旧任务');
 expect(doc.body.textContent).not.toContain('Clean Up Old Tasks');
});

async function detail(task: any, run: () => Promise<void>) {
 const dom = new JSDOM('<div id="root"></div>', {url:'http://localhost'});
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,navigator:dom.window.navigator,localStorage:dom.window.localStorage,Element:dom.window.Element,IS_REACT_ACT_ENVIRONMENT:true,
  // 汇报单用 Markdown 渲染组件显示，它在下一帧处理流程图；jsdom 默认没有这两个函数
  requestAnimationFrame:(callback: () => void) => setTimeout(callback,0),cancelAnimationFrame:(id: any) => clearTimeout(id)});
 const root = createRoot(document.getElementById('root')!);
 const original = apiClient.fetchStatuses;
 apiClient.fetchStatuses = async () => statuses;
 try {
  await act(async () => root.render(React.createElement(ThemeProvider,null,React.createElement(TaskDetailsModal,{task,isOpen:true,onClose:() => {},availableTasks:[]}))));
  await run();
 } finally {
  await act(async () => root.unmount());
  apiClient.fetchStatuses = original;
  dom.window.close();
 }
}

const order = (...texts: string[]) => texts.map(text => document.body.textContent!.indexOf(text));

test('a card waiting for acceptance leads with the next step and the report', async () => {
 await detail({...tasks[2],description:'Synthetic description body'}, async () => {
  const panel = document.querySelector('section[aria-label="下一步"]');
  expect(panel?.textContent).toContain('等你验收');
  const [next, summary, description] = order('等你验收','合成改动','Synthetic description body');
  expect(next).toBeGreaterThan(-1);
  expect(next).toBeLessThan(summary);
  expect(summary).toBeLessThan(description);
  let copied = '';
  Object.defineProperty(navigator,'clipboard',{value:{writeText:async(text:string) => {copied=text;}},configurable:true});
  const button = [...panel!.querySelectorAll('button')][0] as HTMLButtonElement;
  await act(async () => button.click());
  expect(copied).toBe('project-inbox accept TASK-3');
  expect(button.textContent).toBe('已复制');
  Object.defineProperty(navigator,'clipboard',{value:{writeText:async() => {throw new Error('denied');}},configurable:true});
  const second = [...panel!.querySelectorAll('button')][1] as HTMLButtonElement;
  await act(async () => second.click());
  // 剪贴板被拒绝时不提示成功，而是替用户选中整条命令
  expect(second.textContent).toBe('已选中，请手动复制');
  expect(window.getSelection()?.toString()).toBe('project-inbox reject TASK-3 --comment "写一句意见"');
  expect(panel!.querySelectorAll('code')[1]?.textContent).toBe('project-inbox reject TASK-3 --comment "写一句意见"');
 });
});

test('cards that do not need the user keep the original layout', async () => {
 await detail({...tasks[4],description:'Synthetic description body'}, async () => {
  expect(document.querySelector('section[aria-label="下一步"]')).toBeNull();
  const [summary, description] = order('合成改动','Synthetic description body');
  expect(description).toBeGreaterThan(-1);
  expect(description).toBeLessThan(summary);
 });
 await detail({...tasks[5],finalSummary:report,description:'Synthetic description body'}, async () => {
  // 没有交接单的旧卡在待验收时不给命令（accept 对它不做合并），但汇报仍然放在最前面
  expect(document.querySelector('section[aria-label="下一步"]')).toBeNull();
  const [summary, description] = order('合成改动','Synthetic description body');
  expect(summary).toBeLessThan(description);
 });
});
