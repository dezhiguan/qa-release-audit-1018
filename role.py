import json
import re
from typing import Dict, Any, List, Optional

async def execute(data: dict, ctx) -> dict:
    text = data.get('text', '').strip()
    if not text:
        return _blocked('input.text is empty')

    # Extract identifiers from natural language text
    repo = _extract_repo(text)
    run_id = _extract_run_id(text)
    pr_number = _extract_pr_number(text)
    job_id = _extract_job_id(text)

    # Validate required fields
    missing = []
    if not repo:
        missing.append('repo')
    if not run_id:
        missing.append('runId')
    if not pr_number:
        missing.append('PR number')
    if not job_id:
        missing.append('jobId')
    if missing:
        return _blocked(f'missing required identifiers: {", ".join(missing)}')

    # Step 1: Fetch CI log
    try:
        ci_log_res = await ctx.tools.call(
            "ci.log.fetch",
            repo=repo,
            runId=run_id
        )
        if not isinstance(ci_log_res, dict) or not ci_log_res.get('log'):
            return _blocked('ci.log.fetch returned empty or invalid log')
        ci_log = ci_log_res['log']
    except Exception:
        return _blocked('ci.log.fetch failed')

    # Step 2: Fetch PR diff
    try:
        pr_diff_res = await ctx.tools.call(
            "git.pr.diff",
            repo=repo,
            number=int(pr_number)
        )
        if not isinstance(pr_diff_res, dict) or not pr_diff_res.get('diff'):
            return _blocked('git.pr.diff returned empty or invalid diff')
        pr_diff = pr_diff_res['diff']
    except (ValueError, TypeError):
        return _blocked('git.pr.diff: PR number must be a positive integer')
    except Exception:
        return _blocked('git.pr.diff failed')

    # Step 3: Read devflow job
    try:
        job_res = await ctx.tools.call("devflow.job.read", jobId=job_id)
        if not isinstance(job_res, list):
            return _blocked('devflow.job.read returned non-list')
        if not job_res:
            return _blocked('devflow.job.read returned empty list')
        # Check for known terminal states; reject UNKNOWN
        valid_states = {'RUN', 'WAIT', 'HUMAN', 'QUEUED', 'DONE', 'FAIL', 'CANCEL'}
        job_states = {j.get('status') for j in job_res if isinstance(j, dict)}
        if not job_states:
            return _blocked('devflow.job.read: no status field found in any job record')
        if not job_states.issubset(valid_states):
            unknowns = job_states - valid_states
            return _blocked(f'devflow.job.read contains unknown status(es): {unknowns}')
        job_records = job_res
    except Exception:
        return _blocked('devflow.job.read failed')

    # Assemble evidence
    evidence = {
        "ci_log": ci_log[:2048] + "..." if len(ci_log) > 2048 else ci_log,
        "pr_diff": pr_diff[:2048] + "..." if len(pr_diff) > 2048 else pr_diff,
        "job_records": job_records[:5]
    }

    # Delegate to two registered sub-agents
    allowed_delegates = list(ctx.manifest.spec.delegates or [])
    if len(allowed_delegates) < 2:
        return _blocked(f'insufficient delegates registered: {len(allowed_delegates)}, need at least 2')

    delegate_results = []
    for name in allowed_delegates[:2]:
        try:
            delegate_res = await ctx.delegate(name, text)
            if not isinstance(delegate_res, dict) or 'result' not in delegate_res:
                return _blocked(f'delegate {name} returned malformed response')
            if delegate_res['result'] is None or delegate_res['result'] == '':
                return _blocked(f'delegate {name} returned empty result')
            if isinstance(delegate_res['result'], dict) and delegate_res['result'].get('decision') == 'BLOCKED':
                return _blocked(f'delegate {name} explicitly blocked')
            delegate_results.append({
                'agent': name,
                'result': delegate_res['result'],
                'trace_id': delegate_res.get('trace_id'),
                'budget_used': delegate_res.get('budget')
            })
        except Exception as e:
            return _blocked(f'delegate {name} failed: {str(e)}')

    # Compile prompt — only static field descriptions, NO dynamic input/evidence
    prompt = await ctx.prompt('role')
    system_msg = prompt.compile(
        input='user-provided natural language request containing repo, runId, PR number, jobId',
        evidence='verified read-only evidence from ci.log.fetch, git.pr.diff, devflow.job.read'
    )

    # User message contains actual values as JSON
    user_msg = json.dumps({
        'input': text,
        'evidence': evidence
    }, ensure_ascii=False)

    try:
        llm_output = await ctx.llm.chat([
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': user_msg}
        ], prompt=prompt)  # ✅ pass prompt for versioning
        parsed = json.loads(llm_output.strip())
        decision = parsed.get('decision')
        if decision not in ('PASS', 'BLOCKED'):
            return _blocked(f'LLM returned invalid decision: {decision}')
    except json.JSONDecodeError:
        return _blocked('LLM output is not valid JSON')
    except Exception as e:
        return _blocked(f'LLM parsing failed: {str(e)}')

    steps = [
        'extracted repo, runId, PR number, jobId from input',
        'fetched CI log',
        'fetched PR diff',
        'read devflow job records',
        'delegated to two sub-agents',
        'parsed LLM decision'
    ]

    evidence_sources = [
        {'tool': 'ci.log.fetch', 'repo': repo, 'runId': run_id},
        {'tool': 'git.pr.diff', 'repo': repo, 'number': pr_number},
        {'tool': 'devflow.job.read', 'jobId': job_id}
    ]

    uncertainties = []

    return {
        'decision': decision,
        'evidence_sources': evidence_sources,
        'delegate_results': delegate_results,
        'steps': steps,
        'uncertainties': uncertainties
    }


def _blocked(reason: str) -> dict:
    return {
        'decision': 'BLOCKED',
        'evidence_sources': [],
        'delegate_results': [],
        'steps': [],
        'uncertainties': [reason]
    }


def _extract_repo(text: str) -> Optional[str]:
    patterns = [
        r'(?:repo|repository|仓库)[^\w\d]*(?:[:=\s]+)([\w\-]+)',
        r'(?:in|of|for)\s+([\w\-]+)(?:\s+(?:CI|run|PR|task|job|任务|研发任务|CI运行|PR编号))?\b',
        r'^[^\w\d]*([\w\-]+)(?:\s+(?:CI|run|PR|task|job|任务|研发任务|CI运行|PR编号))?\b'
    ]
    for p in patterns:
        m = re.search(p, text, re.I | re.U)
        if m:
            candidate = m.group(1).strip()
            if candidate and len(candidate) >= 3 and '-' in candidate:
                return candidate
    return None


def _extract_run_id(text: str) -> Optional[str]:
    patterns = [
        r'(?:runId|CI\s+runId|CI\s+run|CI运行ID|CI运行id)[^\w\d]*(?:[:=\s]+)(\d+)',
        r'(?:CI|CI运行)\s+(?:run|runId|ID)[^\w\d]*(?:[:=\s]+)(\d+)',
        r'run\s*[:=\s]+(\d+)',
        r'runId\s*[:=\s]+(\d+)'
    ]
    for p in patterns:
        m = re.search(p, text, re.I | re.U)
        if m:
            return m.group(1)
    return None


def _extract_pr_number(text: str) -> Optional[str]:
    patterns = [
        r'(?:PR|PR编号|PR号|Pull Request)[^\w\d]*(?:[:=#\s]+)(\d+)',
        r'#(\d+)',
        r'PR\s+(\d+)',
        r'编号\s*[:=\s]*(\d+)'
    ]
    for p in patterns:
        m = re.search(p, text, re.I | re.U)
        if m:
            num_str = m.group(1)
            if num_str.isdigit() and int(num_str) > 0:
                return num_str
    return None


def _extract_job_id(text: str) -> Optional[str]:
    patterns = [
        r'(?:jobId|task|任务|研发任务|job|Task)[^\w\d]*(?:[:=\s]+)(DF-\d+)',
        r'(DF-\d+)',
        r'(?:DF[-\s]\d+)'
    ]
    for p in patterns:
        m = re.search(p, text, re.I | re.U)
        if m:
            candidate = m.group(1) if m.groups() else m.group(0)
            if candidate and 'DF-' in candidate:
                cleaned = re.sub(r'\s+', '', candidate).replace(' ', '').replace('　', '')
                if re.match(r'^DF-\d+$', cleaned):
                    return cleaned
    return None