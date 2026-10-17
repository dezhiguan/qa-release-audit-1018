import json
from keel import Agent
from keel.protocol.errors import ErrorCode, KeelError
from role import execute

agent = Agent.from_manifest("agent.yaml")

import os
import httpx
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


async def _invoke_child(name: str, text: str, budget: dict, trace_id: str):
    route = json.loads(os.environ.get("KEEL_DELEGATE_ROUTES", "{}" )).get(name)
    if not isinstance(route, dict) or route.get("env") not in {"dev", "test", "staging", "prod"}:
        raise RuntimeError(f"委派 {name} 缺少明确的环境路由，请配置 KEEL_DELEGATE_ROUTES")
    endpoint = str(route.get("endpoint") or "").rstrip("/") + "/v1/invoke"
    if not endpoint.startswith(("http://", "https://")):
        raise RuntimeError(f"委派 {name} 缺少登记 endpoint")
    headers = {"X-Keel-Agent": name, "X-Keel-Env": route["env"]}
    TraceContextTextMapPropagator().inject(headers)
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(endpoint, json={"input": {"text": text}}, headers=headers)
        response.raise_for_status()
    for block in response.text.split("\n\n"):
        if "event: final" not in block:
            continue
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            answer = json.loads(line[5:].strip()).get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError(f"委派 {name} 返回空结果")
            try:
                return json.loads(answer)
            except json.JSONDecodeError:
                return answer
    raise RuntimeError(f"委派 {name} 未返回 final")


for _child_name in agent.manifest.spec.delegates or []:
    agent.child(_child_name)(lambda text, budget, trace_id, name=_child_name:
                             _invoke_child(name, text, budget, trace_id))


@agent.entry
async def run(request, ctx):
    raw = request.input.get("text") or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"text": raw}
    if isinstance(data, list):
        raise KeelError(ErrorCode.SERVER_INVALID_PARAM, "输入必须是 JSON 对象")
    if not isinstance(data, dict):
        data = {"text": raw}
    ctx.step("role")
    result = await execute(data, ctx)
    if not isinstance(result, dict):
        raise KeelError(ErrorCode.SERVER_INVALID_PARAM, "岗位结果必须是 JSON 对象")
    return ctx.final(json.dumps(result, ensure_ascii=False))

app = agent.asgi()
