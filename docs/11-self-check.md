# 自测清单

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## ✅ 自测清单

### 配置自测（改 schema 或改配置后先跑这几条）

```bash
# 1. 配置表自身能被解析，示例能通过自己的校验
python3 scripts/resolve-config.py --print-example > /tmp/example.yaml
python3 scripts/resolve-config.py --check /tmp/example.yaml

# 2. 自己写的那份配置能过校验
python3 scripts/resolve-config.py --check my-config.yaml

# 3. 零配置也要能跑：所有默认值本身必须合法
#    （CI 里这一步就是各 workflow 的第一步；本地可以这样手动模拟）
CONFIG_SECTIONS="ports docker kube" GITHUB_ENV=/tmp/env GITHUB_OUTPUT=/tmp/out \
  CONFIG_VARS_JSON='{}' CONFIG_INPUTS_JSON='{}' CONFIG_SECRETS_JSON='{}' \
  python3 scripts/resolve-config.py && cat /tmp/env
```

| 步骤 | 检查项 | 状态 |
|------|--------|------|
| 1 | `--print-example` 的输出能通过 `--check` | ✅ |
| 2 | 键名拼错 / 类型不对 / 密钥误放明文都能报出路径与建议 | ✅ |
| 3 | 完全不配 vars 与 secrets 时，各 workflow 的第一步仍全绿（默认值兜底） | ✅ |
| 4 | 每个 workflow 里用到的 `env.XXX` 都由它请求的节导出 | ✅ |

### 后端工作流测试

| 步骤 | 检查项 | 状态 |
|------|--------|------|
| 1 | Code Quality 工作流可运行 | ✅ |
| 2 | Build Binary 工作流可运行 | ✅ |
| 3 | Build Docker 工作流可运行 | ✅ |
| 4 | Deploy K8s 工作流可运行 | ✅ |

### 前端工作流测试

| 步骤 | 检查项 | 状态 |
|------|--------|------|
| 1 | Build Frontend 支持 npm | ✅ |
| 2 | Build Frontend 支持 pnpm | ✅ |
| 3 | Build Frontend 支持 yarn | ✅ |
| 4 | Build Docker 支持自定义 nginx.conf | ✅ |
| 5 | Deploy K8s 工作流可运行 | ✅ |

### 模板测试

| 步骤 | 检查项 | 状态 |
|------|--------|------|
| 1 | backend/deployment.yaml 模板完整 | ✅ |
| 2 | backend/service.yaml 模板完整 | ✅ |
| 3 | backend/hpa.yaml 模板完整 | ✅ |
| 4 | backend/ingress.yaml 模板完整 | ✅ |
| 5 | frontend/deployment.yaml 模板完整 | ✅ |
| 6 | frontend/service.yaml 模板完整 | ✅ |
| 7 | frontend/hpa.yaml 模板完整 | ✅ |
| 8 | frontend/ingress.yaml 模板完整 | ✅ |
