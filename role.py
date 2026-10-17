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

    # Validate required fields — strict, no fallback
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
        if not isinstance(ci_log_res, dict):
            return _blocked('ci.log.fetch returned non-dict')
        if ci_log_res.get('logsUnavailable') is True:
            return _blocked('ci.log.fetch: logsUnavailable=true — raw log unavailable')
        if not isinstance(ci_log_res.get('log'), str) or not ci_log_res['log'].strip():
            return _blocked('ci.log.fetch returned empty or non-string log')
        ci_log = ci_log_res['log'].strip()
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

    # Assemble evidence — AFTER delegate_results is defined
    evidence = {
        "ci_log": ci_log[:2048] + "..." if len(ci_log) > 2048 else ci_log,
        "pr_diff": pr_diff[:2048] + "..." if len(pr_diff) > 2048 else pr_diff,
        "job_records": job_records[:5],
        "delegate_results": delegate_results
    }

    # Compile prompt — only static field descriptions, NO dynamic input/evidence
    prompt = await ctx.prompt('role')
    system_msg = prompt.compile(
        input='user-provided natural language request containing repo, runId, PR number, jobId',
        evidence='verified read-only evidence from ci.log.fetch, git.pr.diff, devflow.job.read'
    )

    # User message contains actual values as JSON — includes delegate_results for model visibility
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
    # Match repo slug: ASCII letters/digits/hyphens only, min 3 chars, must contain '-', bounded by non-alnum or start/end
    # Supports tight adjacency: "仓库qa-ci-auditor-1017b", "repo=qa-ci-auditor-1017b", "CI runId987654321、PR#17、任务DF-1234" → extract qa-ci-auditor-1017b
    # Avoid eating Chinese prefixes: match only if preceded by boundary (non-alnum or start) and followed by boundary (non-alnum or end)
    pattern = r'(?<!\w)([a-zA-Z0-9][a-zA-Z0-9\-]{2,}[a-zA-Z0-9])(?![a-zA-Z0-9])'
    candidates = re.findall(pattern, text, re.U)
    for cand in candidates:
        if '-' in cand and len(cand) >= 3:
            # Verify it's *intended* as repo: check proximity to known repo-indicating tokens
            context_start = max(0, text.find(cand) - 20)
            context = text[context_start:text.find(cand)+len(cand)+20].lower()
            if any(kw in context for kw in ['仓库', 'repo', 'repository', 'in ', 'for ', 'of ', '使用', '属于', '对应']):
                return cand
    # Fallback: try explicit labels first
    explicit_patterns = [
        r'(?:repo|repository|仓库)[^\w\d]*(?:[:=\s]+)([a-zA-Z0-9][a-zA-Z0-9\-]{2,}[a-zA-Z0-9])',
        r'(?:in|of|for|使用|属于|来自|对应|位于|在)[^\w\d]+([a-zA-Z0-9][a-zA-Z0-9\-]{2,}[a-zA-Z0-9])(?=[^\w\d]|$)'
    ]
    for p in explicit_patterns:
        m = re.search(p, text, re.U)
        if m:
            candidate = m.group(1)
            if candidate and '-' in candidate:
                return candidate
    return None


def _extract_run_id(text: str) -> Optional[str]:
    # Match runId: digits only, supports tight adjacency like "runId39053829490", "CI runId987654321", "CI运行ID：987654321"
    # Prioritize labeled matches; fallback to bounded digits only if labeled fails
    patterns = [
        # Labeled: handles Chinese/English labels + optional separator
        r'(?:runId|CI\s*runId|CI\s*run|CI运行ID|CI运行id|CI运行)[^\w\d]*(?:[:=\s\u3000\uff1a\uff1d]+)(\d+)',
        r'(?:run|runId|ID)[^\w\d]*(?:[:=\s\u3000\uff1a\uff1d]+)(\d+)',
        # Tight adjacency: "runId123456" → 123456, but NOT "abc123456def"
        r'(?i)runId(\d+)(?=[^a-zA-Z0-9]|$)',
        r'(?i)CI\s*run\s*(\d+)(?=[^a-zA-Z0-9]|$)',
        r'(?i)CI运行ID[\s\u3000：:](\d+)(?=[^a-zA-Z0-9]|$)',
        # Final fallback: isolated digit string ≥6 chars, bounded
        r'(?<!\d)(\d{6,})(?![a-zA-Z0-9])'
    ]
    for p in patterns:
        m = re.search(p, text, re.U)
        if m:
            num_str = m.group(1)
            if num_str.isdigit():
                return num_str
    return None


def _extract_pr_number(text: str) -> Optional[str]:
    # Match PR number: positive integer, supports tight adjacency like "PR#1", "PR1", "PR编号：1", "#1", "PR号=1"
    # Critical: handle "PR#1" and "PR1" without space — avoid matching "PR123abc"
    patterns = [
        # Labeled with separator
        r'(?:PR|PR编号|PR号|Pull Request|PR运行|PR提交|PR号|PR编号)[^\w\d]*(?:[:=#\s\u3000\uff1a\uff1d]+)(\d+)',
        r'#(\d+)',
        r'编号\s*[:=\s\u3000\uff1a\uff1d]*(\d+)',
        # Tight adjacency: "PR#1", "PR1", "PR123" — must be followed by boundary
        r'(?i)PR[#\s\u3000：:](\d+)(?=[^a-zA-Z0-9]|$)',
        r'(?i)PR(\d+)(?=[^a-zA-Z0-9]|$)',
        # Fallback: isolated digits after # or in context
        r'(?<!\d)(\d+)(?=[^a-zA-Z0-9]|$)'
    ]
    for p in patterns:
        m = re.search(p, text, re.U)
        if m:
            num_str = m.group(1)
            if num_str.isdigit() and int(num_str) > 0:
                return num_str
    return None


def _extract_job_id(text: str) -> Optional[str]:
    # Match jobId: DF-<digits>, supports tight adjacency like "任务DF-0010", "jobId=DF-0010", "DF-0010", "DF0010", "DF：0010"
    # Prioritize labeled matches; handle "DF0010" → "DF-0010"
    patterns = [
        # Labeled with separator
        r'(?:jobId|task|任务|研发任务|job|Task|DF)[^\w\d]*(?:[:=\s\u3000\uff1a\uff1d]+)(DF-\d+)',
        r'(?:jobId|task|任务|研发任务|job|Task|DF)[^\w\d]*(?:[:=\s\u3000\uff1a\uff1d]+)(DF)(?:[-\s\u3000：:]+)(\d+)',
        # Tight adjacency: "DF-0010", "DF0010", "DF：0010"
        r'(DF)-(\d+)',
        r'(DF)(\d{4,})(?=[^a-zA-Z0-9]|$)',
        r'(DF)[\s\u3000：:](\d+)(?=[^a-zA-Z0-9]|$)',
        # Final fallback: standalone DF-\d+
        r'(DF-\d+)'
    ]
    for p in patterns:
        m = re.search(p, text, re.I | re.U)
        if m:
            if len(m.groups()) == 2:
                prefix, digits = m.group(1), m.group(2)
                if prefix.upper() == 'DF' and digits.isdigit():
                    return f'DF-{digits}'
            elif m.group(0).startswith('DF-') and re.match(r'^DF-\d+$', m.group(0)):
                return m.group(0)
    return None