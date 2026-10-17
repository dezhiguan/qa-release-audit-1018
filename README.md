# qa-release-audit-1018

独立的 Keel 智能体项目，基于 `supervisor` 模板。此目录就是项目根目录，可单独初始化 Git 仓库；不需要检出 Keel 底座源码。

## 开发

修改 `agent.yaml`、业务入口、`tools/`、`prompts/` 和 `evals/`。提交前运行项目测试，发布前运行 `keel gate --env staging`。

本地已登录 GitHub CLI 后运行：

```bash
version=$(cat .keel-sdk-version)
mkdir -p vendor
gh release download "sdk-v${version}" --repo dezhiguan/keel --pattern "keel_sdk-${version}-py3-none-any.whl" --dir vendor
python -m pip install vendor/keel_sdk-*.whl -r requirements.lock
python -m pytest -q
keel dev
```

`evals/` 保存评测样例与评分器，`.github/workflows/keel.yml` 定义项目自己的流水线。SDK 提供运行时、契约校验和平台客户端；项目只保存自己的业务代码。

## 生产任务 DF-0013

模型生成的研发岗位代码候选，需通过隔离沙箱、人工 PR review、评测门禁和发布审批。
多智能体项目运行时需配置 KEEL_DELEGATE_ROUTES，按 agent.yaml 中的子智能体名称映射其登记 endpoint 和 env。例如 `{"child-name":{"endpoint":"http://child-service:8000","env":"dev"}}`。Keel 可信流水线会自动注入；独立部署时由开发者配置，缺少绑定会明确失败。

## 生产任务 DF-0013

模型生成的研发岗位代码候选，需通过隔离沙箱、人工 PR review、评测门禁和发布审批。
多智能体项目运行时需配置 KEEL_DELEGATE_ROUTES，按 agent.yaml 中的子智能体名称映射其登记 endpoint 和 env。例如 `{"child-name":{"endpoint":"http://child-service:8000","env":"dev"}}`。Keel 可信流水线会自动注入；独立部署时由开发者配置，缺少绑定会明确失败。

## 生产任务 DF-0013

模型生成的研发岗位代码候选，需通过隔离沙箱、人工 PR review、评测门禁和发布审批。
多智能体项目运行时需配置 KEEL_DELEGATE_ROUTES，按 agent.yaml 中的子智能体名称映射其登记 endpoint 和 env。例如 `{"child-name":{"endpoint":"http://child-service:8000","env":"dev"}}`。Keel 可信流水线会自动注入；独立部署时由开发者配置，缺少绑定会明确失败。
