# 自测清单

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## ✅ 自测清单

这份清单原先是手工打勾的，勾是人填的，没人回去重跑 —— 于是「每个 workflow 用到的 env
都由它请求的节导出」这种条目一直是 ✅ 而从未被验证过
现在能自动化的部分都搬进了 [🔬 Self Test](02-workflows.md) 工作流，每次 push 自己跑，
下面只保留两类内容：**怎么在本地跑那套自动检查**，以及**哪些确实还得人工过**

### 本地跑自动检查

```bash
# 依赖：resolve-config.py 与 self-test.py 都要 PyYAML
# 系统 python 没有的话用隔离环境，别污染全局
python3 -m venv /tmp/pipeline-venv
/tmp/pipeline-venv/bin/pip install pyyaml

# 62 项断言，和 CI 上跑的是同一个脚本
/tmp/pipeline-venv/bin/python scripts/self-test.py

# 工作流语法与表达式（未声明的 inputs.xxx 会求值成空串而不报错）
actionlint

# scripts/ 下的 shell
shellcheck --severity=warning scripts/*.sh
```

`self-test.py` 覆盖了什么，见 [工作流](02-workflows.md) 里 Self Test 那一节的断言表

### 单独调配置时的几条命令

```bash
PY=/tmp/pipeline-venv/bin/python

# 打印带注释的完整示例（总表形状）
$PY scripts/resolve-config.py --print-example

# 只打印某几节
$PY scripts/resolve-config.py --print-example kube docker

# 校验总表形状的文件（顶层是节名）
$PY scripts/resolve-config.py --check my-config.yaml

# 校验分节形状的文件（顶层直接是键名，即写进 vars.KUBE_CONFIG 的那份内容）
$PY scripts/resolve-config.py --check kube.yaml --section kube

# 校验密钥总表
$PY scripts/resolve-config.py --check my-secrets.yaml --secrets
```

**两种形状别搞混**：`--print-example kube` 产出的是**总表形状**（带 `kube:` 外层），
要不带 `--section` 去校验；`--check --section kube` 要的是**分节形状**（不带外层）
搞反了会报「没有 'kube.xxx' 这一项」或者「未知配置节」，而两份内容其实都是对的

### 还得人工过的部分

这些需要真的集群、真的镜像仓库、真的 webhook，CI 里没法断言：

| 项 | 怎么验 |
|----|--------|
| Build Docker 推得上去 | 在一个接入仓库上跑一次 Build Docker，看镜像有没有出现在 registry |
| Deploy K8s 滚得动 | 在 dev 环境跑一次 Deploy K8s，看 `kubectl rollout status` 有没有真的等到 Ready |
| HPA 与 Ingress 渲染正确 | 部署后 `kubectl get hpa,ingress -n <ns> -o yaml`，对着 `templates/` 下的模板看字段 |
| 通知真的发得出 | 配好 webhook 后开一个 PR，看群里有没有收到卡片 |
| 跨仓库 dispatch 真的发得出 | 发一个 tag，看 Go Trigger Downstream 的 dispatch job 是否全绿，以及下游有没有收到 `upstream-updated` |
| App 凭证权限够不够 | 跑 [Go Library Bootstrap](02-workflows.md) 的 `audit` 模式，看 job summary 里的凭证列 |

### 改了东西之后该跑哪个

| 改了什么 | 本地先跑 | CI 上会跑 |
|---------|---------|----------|
| `scripts/config/schema.yaml` | `self-test.py` | Self Test 全部四个 job |
| `scripts/resolve-config.py` | `self-test.py` | 同上 |
| 任何 `.github/workflows/*.yml` | `actionlint` + `self-test.py` | 同上 |
| `scripts/*.sh` | `shellcheck` | 同上 |
| `README.md` 或 `docs/*.md` | `self-test.py` | 同上（查句号、死链、工作流覆盖） |
| `templates/` 下的模板 | `self-test.py` | 同上（查占位符有无来源） |
| `.github/actions/` 下的 composite action | `actionlint` | 同上 |
