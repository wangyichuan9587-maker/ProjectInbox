"""交接单和汇报单的固定格式：缺什么就退回什么。只用合成数据，不碰文件。"""
import copy
import pytest
from server.contract import (EXAMPLE_HANDOFF, EXAMPLE_REPORT, ContractError, acceptance_lines, handoff_template, render_plan, render_summary, validate_handoff, validate_report)

PROJECTS = [dict(id='projectinbox', name='ProjectInbox', aliases=['inbox'], enabled=True, missing=False), dict(id='newn8nbase', name='newn8nbase', aliases=[], enabled=True, missing=False)]


def handoff(**changes):
    data = copy.deepcopy(EXAMPLE_HANDOFF)
    data.update(changes)
    return data


def refused(data, text):
    with pytest.raises(ContractError) as error:
        validate_handoff(data, PROJECTS)
    assert any(text in p for p in error.value.problems), error.value.problems
    return error.value


def test_example_passes_and_is_normalized():
    result = validate_handoff(handoff(project='ProjectInbox'), PROJECTS)
    assert result['project'] == 'projectinbox' and result['version'] == 'handoff/v1'
    assert acceptance_lines(result)[1].startswith('[A2][必须看到] ')
    plan = render_plan(result, 'claude-code', '2026-11-14T10:00:00+08:00')
    assert '**这次不做：**' in plan and 'uv run pytest' in plan
    template = handoff_template()
    assert set(template['required']) <= set(template['fields']) and template['example']['acceptance']


def test_missing_scope_dont_is_refused_with_hint():
    error = refused(handoff(scope={'do': ['一件事'], 'dont': []}), 'scope.dont')
    assert '这次不做' in str(error)


def test_acceptance_count_and_kinds():
    many = [{'id': f'A{i}', 'kind': '必须看到', 'text': f'第 {i} 条'} for i in range(1, 9)]
    refused(handoff(acceptance=many), '拆成两张')
    refused(handoff(acceptance=EXAMPLE_HANDOFF['acceptance'][:2]), '至少 3 条')
    no_see = [{'id': 'A1', 'kind': '参照物', 'text': 'x'}, {'id': 'A2', 'kind': '不能破坏', 'text': 'y'}, {'id': 'A3', 'kind': '参照物', 'text': 'z'}]
    refused(handoff(acceptance=no_see), '没有「必须看到」')
    no_keep = [{'id': 'A1', 'kind': '必须看到', 'text': 'x'}, {'id': 'A2', 'kind': '必须看到', 'text': 'y'}, {'id': 'A3', 'kind': '参照物', 'text': 'z'}]
    refused(handoff(acceptance=no_keep), '没有「不能破坏」')
    dup = copy.deepcopy(EXAMPLE_HANDOFF['acceptance'])
    dup[1]['id'] = 'A1'
    refused(handoff(acceptance=dup), '重复')


def test_unknown_project_lists_candidates():
    error = refused(handoff(project='inbx'), '未注册')
    assert 'projectinbox' in str(error) or 'ProjectInbox' in str(error)


def test_current_state_and_verify_must_be_explicit():
    refused(handoff(current_state=None), 'current_state')
    refused(handoff(current_state={'investigated': True, 'notes': ''}), 'notes')
    assert validate_handoff(handoff(current_state={'investigated': False}), PROJECTS)['current_state']['notes'] == '未调查'
    refused(handoff(verify={'tests': '', 'screenshots': True}), 'verify.tests')
    assert validate_handoff(handoff(verify={'tests': 'none', 'screenshots': False}), PROJECTS)['verify']['tests'] == 'none'


def test_unknown_fields_and_multiple_problems_reported_together():
    data = handoff(colour='blue')
    del data['why']
    error = refused(data, '不认识的字段')
    assert any('why' in p for p in error.problems)


def test_report_needs_every_acceptance_result_and_mermaid():
    h = validate_handoff(handoff(), PROJECTS)
    ok = validate_report(copy.deepcopy(EXAMPLE_REPORT), h, 'TASK-12')
    assert ok['screenshots'] == ['a2-before.png', 'a2-after.png', 'a3-stale.png']
    summary = render_summary(ok, ['测试通过'])
    assert '```mermaid' in summary and 'A4：仅自动测试' in summary
    missing = copy.deepcopy(EXAMPLE_REPORT)
    del missing['acceptance']['A3']
    with pytest.raises(ContractError, match='漏了 A3'):
        validate_report(missing, h, 'TASK-12')
    untested = copy.deepcopy(EXAMPLE_REPORT)
    untested['acceptance']['A1'] = {'result': '未测'}
    with pytest.raises(ContractError, match='原因'):
        validate_report(untested, h, 'TASK-12')
    no_chart = copy.deepcopy(EXAMPLE_REPORT)
    no_chart['visual_summary'] = ''
    with pytest.raises(ContractError, match='Mermaid'):
        validate_report(no_chart, h, 'TASK-12')
    wrong = copy.deepcopy(EXAMPLE_REPORT)
    with pytest.raises(ContractError, match='TASK-13'):
        validate_report(wrong, h, 'TASK-13')


def test_blocked_report_needs_reason_and_screenshot_paths_rejected():
    h = validate_handoff(handoff(), PROJECTS)
    blocked = dict(task='TASK-12', outcome='blocked', summary='没做完', acceptance={a['id']: {'result': '未测', 'note': '受阻'} for a in h['acceptance']}, where_to_see='无', deviations='无')
    with pytest.raises(ContractError, match='blocked_reason'):
        validate_report(blocked, h, 'TASK-12')
    blocked['blocked_reason'] = '需要数据库权限'
    assert validate_report(blocked, h, 'TASK-12')['outcome'] == 'blocked'
    sneaky = copy.deepcopy(EXAMPLE_REPORT)
    sneaky['screenshots'] = ['../../etc/passwd']
    with pytest.raises(ContractError, match='只写文件名'):
        validate_report(sneaky, h, 'TASK-12')


def test_malformed_values_are_refused_instead_of_crashing():
    odd_kind = handoff()
    odd_kind['acceptance'][0]['kind'] = ['必须看到']
    refused(odd_kind, 'A1 的 kind 只能是')
    odd_keys = handoff()
    odd_keys[1] = 'yaml 里的数字键'
    odd_keys[None] = '空键'
    error = refused(odd_keys, '不认识的字段')
    assert '1' in str(error) and 'None' in str(error)
    h = validate_handoff(handoff(), PROJECTS)
    odd_report = copy.deepcopy(EXAMPLE_REPORT)
    odd_report[7] = 'x'
    with pytest.raises(ContractError, match='不认识的字段：7'):
        validate_report(odd_report, h, 'TASK-12')
