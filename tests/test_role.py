import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, Any

# Import the execute function
from role import execute

@pytest.mark.asyncio
async def test_execute_normal_pass():
    # Mock context
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ['qa-devops-investigator-1017', 'qa-policy-advisor-1017']
    ctx.prompt = AsyncMock()
    # ✅ compile only static descriptions
    ctx.prompt.return_value.compile = lambda input, evidence: f'SYSTEM: {input}, {evidence}'
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    # Mock tools
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        {'log': 'CI log content...'},
        {'diff': 'diff content...'},
        [{'status': 'DONE', 'id': 'DF-0010'}]
    ]
    
    # Mock delegate
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {'result': 'devops finding', 'trace_id': 't1', 'budget': 10},
        {'result': 'policy finding', 'trace_id': 't2', 'budget': 8}
    ]
    
    # Input
    data = {
        'text': '请核验仓库 qa-ci-auditor-1017b 的 CI runId 39053829490、PR #1、任务 DF-0010；不代表自动合并或发布。'
    }
    
    result = await execute(data, ctx)
    
    assert result['decision'] == 'PASS'
    assert len(result['evidence_sources']) == 3
    assert len(result['delegate_results']) == 2
    assert len(result['steps']) >= 5
    assert 'qa-devops-investigator-1017' in [d['agent'] for d in result['delegate_results']]
    assert 'qa-policy-advisor-1017' in [d['agent'] for d in result['delegate_results']]


@pytest.mark.asyncio
async def test_execute_missing_runid_blocked():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ['qa-devops-investigator-1017', 'qa-policy-advisor-1017']
    ctx.prompt = AsyncMock()
    ctx.llm.chat = AsyncMock()
    
    # Tools won't be called — early exit on missing runId
    ctx.tools.call = AsyncMock()
    ctx.delegate = AsyncMock()
    
    data = {
        'text': '仓库 qa-ci-auditor-1017b，PR #1，任务 DF-0010。runId暂未提供。'
    }
    
    result = await execute(data, ctx)
    
    assert result['decision'] == 'BLOCKED'
    assert 'runId' in result['uncertainties'][0]


@pytest.mark.asyncio
async def test_execute_delegate_blocked_final_blocked():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ['qa-devops-investigator-1017', 'qa-policy-advisor-1017']
    ctx.prompt = AsyncMock()
    # ✅ compile static only
    ctx.prompt.return_value.compile = lambda input, evidence: 'SYSTEM'
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    # Tools succeed
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        {'log': 'log'},
        {'diff': 'diff'},
        [{'status': 'DONE'}]
    ]
    
    # First delegate returns BLOCKED structurally
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {'result': {'decision': 'BLOCKED'}, 'trace_id': 't1'},
        {'result': 'ok', 'trace_id': 't2'}
    ]
    
    data = {
        'text': 'repo=qa-ci-auditor-1017b, runId=123, PR #1, jobId=DF-0010'
    }
    
    result = await execute(data, ctx)
    
    assert result['decision'] == 'BLOCKED'
    assert 'delegate qa-devops-investigator-1017 explicitly blocked' in result['uncertainties'][0]


@pytest.mark.asyncio
async def test_execute_llm_returns_invalid_json_blocked():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ['qa-devops-investigator-1017', 'qa-policy-advisor-1017']
    ctx.prompt = AsyncMock()
    ctx.prompt.return_value.compile = lambda input, evidence: 'SYSTEM'
    ctx.llm.chat = AsyncMock(return_value='invalid json')
    
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        {'log': 'log'},
        {'diff': 'diff'},
        [{'status': 'DONE'}]
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {'result': 'ok', 'trace_id': 't1'},
        {'result': 'ok', 'trace_id': 't2'}
    ]
    
    data = {
        'text': 'repo=qa-ci-auditor-1017b, runId=123, PR #1, jobId=DF-0010'
    }
    
    result = await execute(data, ctx)
    
    assert result['decision'] == 'BLOCKED'
    assert 'LLM output is not valid JSON' in result['uncertainties'][0]


@pytest.mark.asyncio
async def test_execute_llm_returns_pass_but_with_explanation_blocked():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ['qa-devops-investigator-1017', 'qa-policy-advisor-1017']
    ctx.prompt = AsyncMock()
    ctx.prompt.return_value.compile = lambda input, evidence: 'SYSTEM'
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS", "note": "all good"}')
    
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        {'log': 'log'},
        {'diff': 'diff'},
        [{'status': 'DONE'}]
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {'result': 'ok', 'trace_id': 't1'},
        {'result': 'ok', 'trace_id': 't2'}
    ]
    
    data = {
        'text': 'repo=qa-ci-auditor-1017b, runId=123, PR #1, jobId=DF-0010'
    }
    
    result = await execute(data, ctx)
    
    assert result['decision'] == 'PASS'
    # Note: extra fields are ignored — only decision matters


@pytest.mark.asyncio
async def test_execute_ci_log_unavailable_blocked():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ['qa-devops-investigator-1017', 'qa-policy-advisor-1017']
    ctx.prompt = AsyncMock()
    ctx.prompt.return_value.compile = lambda input, evidence: 'SYSTEM'
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    
    # ci.log.fetch returns logsUnavailable=True (even with log present)
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        {'log': 'summary only', 'logsUnavailable': True},
        {'diff': 'diff'},
        [{'status': 'DONE'}]
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {'result': 'ok', 'trace_id': 't1'},
        {'result': 'ok', 'trace_id': 't2'}
    ]
    
    data = {
        'text': 'repo=qa-ci-auditor-1017b, runId=123, PR #1, jobId=DF-0010'
    }
    
    result = await execute(data, ctx)
    
    assert result['decision'] == 'BLOCKED'
    assert 'logsUnavailable=true' in result['uncertainties'][0]


@pytest.mark.asyncio
async def test_execute_delegate_results_in_user_msg():
    ctx = MagicMock()
    ctx.manifest.spec.delegates = ['qa-devops-investigator-1017', 'qa-policy-advisor-1017']
    ctx.prompt = AsyncMock()
    
    # Capture what gets passed to compile() and chat()
    captured_system = []
    captured_user = []
    
    def mock_compile(**kwargs):
        captured_system.append(kwargs)
        return 'SYSTEM PROMPT'
    ctx.prompt.return_value.compile = mock_compile
    
    async def mock_chat(messages, **_):
        assert len(messages) == 2
        assert messages[0]['role'] == 'system'
        assert messages[1]['role'] == 'user'
        captured_user.append(json.loads(messages[1]['content']))
        return '{"decision": "PASS"}'
    ctx.llm.chat = mock_chat
    
    ctx.tools.call = AsyncMock()
    ctx.tools.call.side_effect = [
        {'log': 'log', 'logsUnavailable': False},
        {'diff': 'diff'},
        [{'status': 'DONE'}]
    ]
    
    ctx.delegate = AsyncMock()
    ctx.delegate.side_effect = [
        {'result': 'devops finding', 'trace_id': 't1', 'budget': 10},
        {'result': 'policy finding', 'trace_id': 't2', 'budget': 8}
    ]
    
    data = {
        'text': 'repo=qa-ci-auditor-1017b, runId=123, PR #1, jobId=DF-0010'
    }
    
    await execute(data, ctx)
    
    assert len(captured_user) == 1
    assert 'delegate_results' in captured_user[0]['evidence']
    assert len(captured_user[0]['evidence']['delegate_results']) == 2
    assert captured_user[0]['evidence']['delegate_results'][0]['agent'] == 'qa-devops-investigator-1017'
    assert captured_user[0]['evidence']['delegate_results'][1]['agent'] == 'qa-policy-advisor-1017'
