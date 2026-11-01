# 配置：一份聚合 YAML

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## 🔐 配置：一份聚合 YAML

> 以前每个配置项都是一个 GitHub 变量：`K8S_CPU_LIMIT`、`K8S_MIN_REPLICAS`、`INGRESS_HOST`……
> 配一个服务要在 Settings 里点几十次，还得记住每个名字
> 现在**一个变量装完整份配置**，子对象分节写，推送前能本地校验
> 旧的单个变量**继续有效**，作为聚合 YAML 之下的一层回退，不用一次性迁完
>
> [EN] Every setting used to be one GitHub variable, which meant dozens of clicks per service
> and a name to remember for each. One variable now holds the whole document as nested
> sections, validated before it is pushed. The legacy per-key variables keep working as a
> fallback layer, so nothing has to be migrated at once.

### 配什么

| 在哪配 | 名字 | 装什么 |
|---|---|---|
| Variables（仓库级，打底） | `PIPELINE_CONFIG` | 整份明文配置，顶层是 `ports` / `docker` / `kube` / `builder` / `cleanup` / `branch` / `notify` 七个节 |
| Variables（环境级，覆盖） | `KUBE_CONFIG`、`DOCKER_CONFIG`、`BUILDER_CONFIG`、`PORTS_CONFIG`、`CLEANUP_CONFIG`、`BRANCH_CONFIG`、`NOTIFY_CONFIG` | 对应节的内容（**不带节名**），按节深度合并覆盖 `PIPELINE_CONFIG` |
| Secrets | `PIPELINE_SECRETS` | kubeconfig、镜像仓库凭证、通知 webhook、GitHub App 私钥、跨仓库 token、业务密钥 |

配置路径：

```text
仓库级：Settings → Secrets and variables → Actions → Repository variables / secrets
环境级：Settings → Environments → [环境名] → Environment variables / secrets
```

### 优先级

```text
分节变量（KUBE_CONFIG 等） > 总表（PIPELINE_CONFIG） > workflow input > 旧式单个 vars > 默认值
         ↑ 环境级覆盖仓库级            ↑ 新配置优先级最高
```

常用姿势：`PIPELINE_CONFIG` 写仓库级基线（端口、资源、Go 版本），各环境只用 `KUBE_CONFIG`
覆盖差异的那几项（命名空间、副本数、域名）

> **input 层的语义**：沿用原先 `${{ inputs.x || vars.X || '默认' }}` 的判断，空字符串和 `false`
> 都算「没传」
> 所以调用方不传就自动落到 vars 与默认值，和改造前一致
>
> [EN] The input layer keeps the semantics of the original expression chain: an empty string and
> false both mean "not provided", so a caller that passes nothing falls through to the variables
> and the defaults exactly as before.

### 示例

`vars.PIPELINE_CONFIG`（仓库级基线）：

```yaml
ports:
  http: 8080
  rpc: 9181
  ws: 18085
  metrics: 9090
docker:
  registry: ghcr.io
  base_image: alpine:3.20
  arch: arm64
  timezone: Asia/Shanghai
kube:
  namespace: business
  workload_kind: Deployment
  image_pull_secrets: ghcr-cred
  resources:
    requests: { cpu: 200m, memory: 256Mi }
    limits: { cpu: 1, memory: 1Gi }
  probes:
    startup: { period: 5, failure_threshold: 60 }
  hpa:
    enable: true
    min_replicas: 3
    max_replicas: 30
    target_cpu_utilization: 70
  gomemlimit_percent: 90
  ingress:
    host: gw.example.com
    tls: true
    cert_resolver: letsencrypt
builder:
  go_version: "1.25.1"
  goprivate: github.com/kamalyes
  go_auth_method: app-token
  arch: arm64
  upx: { enable: true }
cleanup:
  keep_count: 20
  tag_exclude_prefix: v, prod , release
notify:
  provider: feishu
```

`vars.KUBE_CONFIG`（prod 环境级，只覆盖这几项）：

```yaml
namespace: prod
replicas: 4
resources:
  limits: { cpu: 2, memory: 2Gi }
hpa:
  min_replicas: 4
  max_replicas: 50
ingress:
  host: api.example.com
```

`secrets.PIPELINE_SECRETS`（多行值用 `|` 块标量）：

```yaml
kube:
  kubeconfig: |
    apiVersion: v1
    clusters:
      - cluster:
          server: https://10.0.0.1:6443
        name: prod
docker:
  username: ci-bot
  password: ghp_xxxxxxxxxxxx
notify:
  feishu_webhook_url: https://open.feishu.cn/open-apis/bot/v2/hook/xxxx
go:
  app_id: "123456"
  app_private_key: |
    -----BEGIN RSA PRIVATE KEY-----
    ...
    -----END RSA PRIVATE KEY-----
# 业务密钥：键名就是容器里的环境变量名，部署时同步成集群里的 K8s Secret
app:
  DB_PASSWORD: s3cr3t
  JWT_SECRET: another-secret
```

### 校验：贴进去之前先在本地过一遍

```bash
# 校验一份总表（键名拼错、类型不对、把密钥写进明文都会报出来）
python3 scripts/resolve-config.py --check my-config.yaml

# 校验一份分节配置（内容不带节名时要指明是哪一节）
python3 scripts/resolve-config.py --check kube.yaml --section kube

# 校验聚合密钥
python3 scripts/resolve-config.py --check my-secrets.yaml --secrets

# 打印带注释的完整示例，直接改完贴进变量
python3 scripts/resolve-config.py --print-example
python3 scripts/resolve-config.py --print-example kube docker
```

报错长这样 —— 一次全报完，不是报一个停一个，未知键还给「是否想写」提示：

```text
❌ 配置校验失败，共 4 处 | 4 configuration problem(s)
   bad.yaml: 未知配置节 'kubernetes'，是否想写 kube？ | unknown section
   bad.yaml: kube 节里没有 'namspace' 这一项，是否想写 namespace？ | unknown key
   bad.yaml 的 kube.service.type: 只能是 ClusterIP / NodePort / LoadBalancer 之一，实际 'ClusterIp'，是否想写 ClusterIP？
   bad.yaml: kube.kubeconfig 是密钥，必须配在 secrets.PIPELINE_SECRETS 里，不能放进明文 vars | this key belongs in secrets
```

流水线里也是同一套校验：配错了在第一步就红，并且在 job summary 里给出「每一项最终值 + 它来自哪一层」的表，
排查「为什么是这个值」只看这一张表

### 工作流里的样子

所有配置先过 [`resolve-config`](#resolve-config) 这一个 action，之后各步骤只认 `env`：

```yaml
jobs:
  deploy:
    steps:
      - name: ⚙️ Resolve Pipeline Config
        uses: kamalyes/deploy-pipeline/.github/actions/resolve-config@master
        with:
          sections: ports docker kube
          secret-sections: kube notify app
          vars-json: ${{ toJSON(vars) }}
          inputs-json: ${{ toJSON(inputs) }}
          secrets-json: ${{ toJSON(secrets) }}

      - run: echo "${{ env.NAMESPACE }} ${{ env.CPU_LIMIT }}"
```

调用方要注意两点：

- **`PIPELINE_SECRETS` 要能传到被调用的工作流**：在 `secrets:` 里显式写
  `PIPELINE_SECRETS: ${{ secrets.PIPELINE_SECRETS }}`
  只显式传了几个 secret 又没带它的话，聚合密钥读不到

  > <a id="inherit-cross-account"></a>**不要依赖 `secrets: inherit`**：它在**跨账号**调用时
  > 一个 secret 都传不过去 —— 可复用工作流在个人账号、调用方在组织时实测如此
  > 而且不只是 `toJSON(secrets)` 枚举不到：按名字取 `toJSON(secrets.FEISHU_WEBHOOK_URL)`
  > 也求值成 `null`，即该 secret 根本没进被调方上下文
  > 症状是**没有任何报错** —— job 绿、其余步骤正常，只有依赖那个 secret 的一步静默跳过
  >
  > 对照之下 `vars` 是另一回事：同一次运行里 `toJSON(vars)` 能完整拿到调用方的全部仓库变量，
  > 调用方一行都不用传
  > 所以配置走 `vars`、凭证走**显式映射**
  >
  > 同账号调用时 `inherit` 是有效的，但显式映射在两种情形下都成立，没有理由不写
- **job 名与 job 级 `if:` 看不到解析结果**：这两处在任何 step 之前求值，拿不到 `GITHUB_ENV`
  所以 build 的 job 名不再内联平台（改成在步骤日志与报告里显示），`notify` job 的跳过条件仍按
  `notification-provider` 这个 input 判断，而真正发哪些渠道由解析后的 `notify.provider` 决定

### 全部配置项

明文配置（`PIPELINE_CONFIG` / 分节变量），也可以 `python3 scripts/resolve-config.py --print-example` 直接打印，
或手动触发 [⚙️ Generate Pipeline Config](#generate-pipeline-config) 让流水线打给你：

#### `ports` — 容器端口；留空 / 0 / none 表示该端口不启用 | Container ports; empty, 0 or none disables one

| YAML 路径 | 导出的 env | 旧式单个 vars | 类型 | 默认值 |
|---|---|---|---|---|
| `ports.http` | `HTTP_PORT` | `HTTP_PORT` | port | `''` |
| `ports.rpc` | `RPC_PORT` | `RPC_PORT` | port | `9181` |
| `ports.ws` | `WS_PORT` | `WS_PORT` | port | `''` |
| `ports.pprof` | `PPROF_PORT` | `PPROF_PORT` | port | `6160` |
| `ports.node_grpc` | `NODE_GRPC_PORT` | `NODE_GRPC_PORT` | port | `''` |
| `ports.metrics` | `METRICS_PORT` | `METRICS_PORT` | port | `''` |
| `ports.metrics_path` | `METRICS_PATH` | `METRICS_PATH` | string | `/metrics` |
| `ports.probe_name` | `PROBE_PORT_NAME` | `PROBE_PORT_NAME` | string | `''` |

#### `docker` — 镜像构建与推送 | Image build and push

| YAML 路径 | 导出的 env | 旧式单个 vars | 类型 | 默认值 |
|---|---|---|---|---|
| `docker.registry` | `DOCKER_REGISTRY` | `DOCKER_REGISTRY` | string | `ghcr.io` |
| `docker.run_workdir` | `DOCKER_RUN_WORKDIR` | `DOCKER_RUN_WORKDIR` | string | `/usr/local/services/{repo}` |
| `docker.base_image` | `BASE_IMAGE` | `BUILD_DOCKER_BASE_IMAGE` / `BASE_IMAGE` | string | `alpine:3.19.1` |
| `docker.os` | `BUILD_DOCKER_OS` / `BUILD_BINARY_OS` | `BUILD_DOCKER_OS` / `BUILD_BINARY_OS` | enum: linux | `linux` |
| `docker.arch` | `BUILD_DOCKER_ARCH` / `BUILD_BINARY_ARCH` | `BUILD_DOCKER_ARCH` / `BUILD_BINARY_ARCH` | enum: amd64 \| arm64 | `amd64` |
| `docker.timezone` | `TIMEZONE` | `TIMEZONE` | string | `Etc/UTC` |

#### `kube` — K8s 工作负载、资源、探针、HPA、Ingress | Workload, resources, probes, HPA, Ingress

| YAML 路径 | 导出的 env | 旧式单个 vars | 类型 | 默认值 |
|---|---|---|---|---|
| `kube.namespace` | `NAMESPACE` | `K8S_NAMESPACE` | string | `default` |
| `kube.workload_kind` | `KIND` | `KIND` | enum: Deployment \| StatefulSet \| DaemonSet | `Deployment` |
| `kube.image_pull_secrets` | `IMAGE_PULL_SECRETS` | `K8S_IMAGE_PULL_SECRETS` | string | `ghcr.io` |
| `kube.replicas` | `DEPLOYMENT_REPLICAS` | `K8S_DEPLOYMENT_REPLICAS` | int (0–1000) | `2` |
| `kube.termination_grace_period_seconds` | `TERMINATION_GRACE_PERIOD_SECONDS` | `K8S_TERMINATION_GRACE_PERIOD_SECONDS` | int (0–3600) | `30` |
| `kube.gomemlimit_percent` | `GOMEMLIMIT_PERCENT` | `K8S_GOMEMLIMIT_PERCENT` | percent | `''` |
| `kube.health_max_wait` | `HEALTH_MAX_WAIT` | `K8S_HEALTH_MAX_WAIT` | int (30–3600) | `300` |
| `kube.service.kind` | `SERVICE_KIND` | `SERVICE_KIND` | enum: Service \| NodePort \| LoadBalancer | `Service` |
| `kube.service.type` | `SERVICE_TYPE` | `SERVICE_TYPE` | enum: ClusterIP \| NodePort \| LoadBalancer | `ClusterIP` |
| `kube.resources.requests.cpu` | `CPU_REQUEST` | `K8S_CPU_REQUEST` | quantity | `100m` |
| `kube.resources.requests.memory` | `MEMORY_REQUEST` | `K8S_MEMORY_REQUEST` | quantity | `128Mi` |
| `kube.resources.limits.cpu` | `CPU_LIMIT` | `K8S_CPU_LIMIT` | quantity | `300m` |
| `kube.resources.limits.memory` | `MEMORY_LIMIT` | `K8S_MEMORY_LIMIT` | quantity | `256Mi` |
| `kube.probes.readiness.delay` | `READINESS_DELAY` | `K8S_READINESS_DELAY_SECONDS` | int (0–600) | `5` |
| `kube.probes.readiness.period` | `READINESS_PERIOD` | `K8S_READINESS_PERIOD_SECONDS` | int (1–600) | `10` |
| `kube.probes.liveness.delay` | `LIVENESS_DELAY` | `K8S_LIVENESS_DELAY_SECONDS` | int (0–600) | `15` |
| `kube.probes.liveness.period` | `LIVENESS_PERIOD` | `K8S_LIVENESS_PERIOD_SECONDS` | int (1–600) | `20` |
| `kube.probes.startup.period` | `STARTUP_PERIOD` | `K8S_STARTUP_PERIOD_SECONDS` | int (1–600) | `10` |
| `kube.probes.startup.failure_threshold` | `STARTUP_FAILURE_THRESHOLD` | `K8S_STARTUP_FAILURE_THRESHOLD` | int (1–1000) | `30` |
| `kube.hpa.enable` | `ENABLE_HPA` | `K8S_ENABLE_HPA` | bool | `true` |
| `kube.hpa.min_replicas` | `MIN_REPLICAS` | `K8S_MIN_REPLICAS` | int (1–1000) | `2` |
| `kube.hpa.max_replicas` | `MAX_REPLICAS` | `K8S_MAX_REPLICAS` | int (1–1000) | `10` |
| `kube.hpa.target_cpu_utilization` | `TARGET_CPU_UTILIZATION` | `K8S_TARGET_CPU_UTILIZATION` | percent | `80` |
| `kube.hpa.target_memory_utilization` | `TARGET_MEMORY_UTILIZATION` | `K8S_TARGET_MEMORY_UTILIZATION` | percent | `80` |
| `kube.hpa.scale_up_percent` | `SCALE_UP_PERCENT` | `K8S_SCALE_UP_PERCENT` | percent | `100` |
| `kube.hpa.scale_down_percent` | `SCALE_DOWN_PERCENT` | `K8S_SCALE_DOWN_PERCENT` | percent | `50` |
| `kube.hpa.scale_down_stabilization_seconds` | `SCALE_DOWN_STABILIZATION_SECONDS` | `K8S_SCALE_DOWN_STABILIZATION_SECONDS` | int (0–3600) | `300` |
| `kube.ingress.entry_point` | `INGRESS_ENTRY_POINT` | `INGRESS_ENTRY_POINT` | string | `web` |
| `kube.ingress.host` | `INGRESS_HOST` | `INGRESS_HOST` | string | `''` |
| `kube.ingress.path_prefix` | `INGRESS_PATH_PREFIX` | `INGRESS_PATH_PREFIX` | string | `''` |
| `kube.ingress.tls` | `INGRESS_TLS` | `INGRESS_TLS` | bool | `false` |
| `kube.ingress.cert_resolver` | `INGRESS_CERT_RESOLVER` | `INGRESS_CERT_RESOLVER` | string | `letsencrypt` |
| `kube.ingress.tls_entry_point` | `INGRESS_TLS_ENTRY_POINT` | `INGRESS_TLS_ENTRY_POINT` | string | `websecure` |
| `kube.ingress.redirect_http_to_https` | `INGRESS_REDIRECT_HTTP_TO_HTTPS` | `INGRESS_REDIRECT_HTTP_TO_HTTPS` | bool | `true` |

#### `builder` — Go 构建与代码质量检查 | Go build and code quality

| YAML 路径 | 导出的 env | 旧式单个 vars | 类型 | 默认值 |
|---|---|---|---|---|
| `builder.go_version` | `GO_VERSION` | `GO_VERSION` | string | `1.25.1` |
| `builder.gotestsum_version` | `GOTESTSUM_VERSION` | `GOTESTSUM_VERSION` | string | `v1.13.0` |
| `builder.goproxy` | `GOPROXY` | `GOPROXY` | string | `https://goproxy.cn\|direct` |
| `builder.goprivate` | `GOPRIVATE` | `GOPRIVATE` | string | `''` |
| `builder.go_auth_method` | `GO_AUTH_METHOD` | `GO_AUTH_METHOD` | enum: app-token \| ssh \| none | `ssh` |
| `builder.os` | `BUILD_BINARY_OS` | `BUILD_BINARY_OS` | enum: linux | `linux` |
| `builder.arch` | `BUILD_BINARY_ARCH` | `BUILD_BINARY_ARCH` | enum: amd64 \| arm64 | `amd64` |
| `builder.upx.enable` | `UPX_COMPRESS` | `UPX_COMPRESS` | bool | `false` |
| `builder.upx.level` | `UPX_LEVEL` | `UPX_LEVEL` | string | `--best --lzma` |

#### `golib` — go-* 公共库的测试与上下游联动 | Test matrix and release chain for the go-* libraries

这一节管着所有 Go 库的 CI 与上下游联动，由 [Go Library Test / Bump Upstream / Trigger
Downstream](02-workflows.md) 三条工作流共用

| YAML 路径 | 导出的 env | workflow input | 旧式单个 vars | 类型 | 默认值 |
|---|---|---|---|---|---|
| `golib.go_versions` | `GOLIB_GO_VERSIONS` | `go-versions` | `GOLIB_GO_VERSIONS` / `GO_VERSIONS` | string | `''` |
| `golib.platforms` | `GOLIB_PLATFORMS` | `platforms` | `GOLIB_PLATFORMS` / `PLATFORMS` | string | `["ubuntu-latest"]` |
| `golib.gofmt_check` | `GOLIB_GOFMT_CHECK` | `gofmt-check` | `GOLIB_GOFMT_CHECK` | bool | `true` |
| `golib.build_tags` | `GOLIB_BUILD_TAGS` | `build-tags` | `GOLIB_BUILD_TAGS` | string | `''` |
| `golib.test_flags` | `GOLIB_TEST_FLAGS` | `test-flags` | `GOLIB_TEST_FLAGS` | string | `-race -count=1 -shuffle=on` |
| `golib.test_timeout` | `GOLIB_TEST_TIMEOUT` | `test-timeout` | `GOLIB_TEST_TIMEOUT` | string | `10m` |
| `golib.gotestsum_version` | `GOTESTSUM_VERSION` | `gotestsum-version` | `GOTESTSUM_VERSION` | string | `v1.13.0` |
| `golib.coverage_go_version` | `GOLIB_COVERAGE_GO_VERSION` | `coverage-go-version` | `GOLIB_COVERAGE_GO_VERSION` | string | `''` |
| `golib.coverage_platform` | `GOLIB_COVERAGE_PLATFORM` | `coverage-platform` | `GOLIB_COVERAGE_PLATFORM` | string | `ubuntu-latest` |
| `golib.codecov_slug` | `GOLIB_CODECOV_SLUG` | `codecov-slug` | `GOLIB_CODECOV_SLUG` | string | `''` |
| `golib.downstream_repos` | `GOLIB_DOWNSTREAM_REPOS` | `downstream-repos` | `GOLIB_DOWNSTREAM_REPOS` / `DOWNSTREAM_REPOS` | string | `''` |
| `golib.exclude_repos_regex` | `GOLIB_EXCLUDE_REPOS_REGEX` | `exclude-repos-regex` | `GOLIB_EXCLUDE_REPOS_REGEX` | string | `^(.*-template$\|deploy-pipeline$)` |
| `golib.downstream_create_tag` | `GOLIB_DOWNSTREAM_CREATE_TAG` | `downstream-create-tag` | `GOLIB_DOWNSTREAM_CREATE_TAG` | bool | `false` |
| `golib.bump_run_tests` | `GOLIB_BUMP_RUN_TESTS` | `run-tests` | `GOLIB_BUMP_RUN_TESTS` | bool | `true` |

**四个默认值留空是故意的，填上会坏事：**

`go_versions` 留空不是「还没想好默认值」，而是工作流靠「这一项为空」判断要不要从调用方
`go.mod` 推导矩阵
一旦填上静态默认值，推导分支就永远走不到 —— 这个错犯过：默认值写成
`["1.25.x","1.26.x","1.27.x"]` 时，地板是 1.26 的仓库拿到了 1.25.x，被工具链闸门当场判红；
而地板是 1.21 或 1.23 的仓库则悄悄不再测自己的地板
要覆盖推导结果就填，但每一项都必须不低于本仓库 `go.mod` 的 `go` 指令

`coverage_go_version` 留空取矩阵里最高的那个
别写死版本号：矩阵会随 Go 发新版自己上移，写死的值早晚匹配不到任何 job，
于是覆盖率静默停止上传而 CI 仍然全绿

`codecov_slug` 留空由工作流回落到 `github.repository`
注意 schema 的 `{repo}` 只展开仓库名、不含 owner，而 Codecov 要的是 `owner/repo`，
所以这一项不能用 `{repo}` 占位

`exclude_repos_regex` 的默认值只排除模板与 pipeline 自身，这是对任何账号都成立的通用规则
**组织内部那些「已部署服务」的清单不要写进共用流水线的默认值** —— 那会把服务名固化进一份
公开资产，而这些名字对别的账号毫无意义
要排除就配成一个组织级变量，一个变量管住全部仓库

`downstream_repos` 留空走真实依赖图自动推导，这是默认也是推荐做法
填了之后流水线仍会比对推导结果，缺漏的在 job summary 里点出来并发 warning
格式认 JSON 数组、逗号分隔、空白分隔，裸仓库名按当前 owner 补全
真实的依赖图与各仓库的影响面见 [依赖层级](12-dependency-graph.md)

**`test_flags` 的默认值带 `-shuffle=on`**，所以测试之间不能有顺序依赖
依赖全局状态初值的用例在这个默认下会随机失败，那是用例的问题不是流水线的问题

#### `cleanup` — artifacts / cache / 镜像清理 | Artifact, cache and image cleanup

| YAML 路径 | 导出的 env | 旧式单个 vars | 类型 | 默认值 |
|---|---|---|---|---|
| `cleanup.keep_count` | `KEEP_COUNT` | `CLEANUP_KEEP_COUNT` | int (1–1000) | `10` |
| `cleanup.tag_include_prefix` | `TAG_INCLUDE_PREFIX` | `CLEANUP_TAG_INCLUDE_PREFIX` | csv | `''` |
| `cleanup.tag_exclude_prefix` | `TAG_EXCLUDE_PREFIX` | `CLEANUP_TAG_EXCLUDE_PREFIX` | csv | `v,prod` |

#### `service` — 服务身份与布局 | Service identity and layout

| YAML 路径 | 导出的 env | 旧式单个 vars | 类型 | 默认值 |
|---|---|---|---|---|
| `service.binary_name` | `BINARY_NAME` | `BINARY_NAME` | string | `{repo}` |
| `service.binary_source_dir` | `BINARY_SOURCE_DIR` | `BINARY_SOURCE_DIR` | string | `deployments` |
| `service.binary_build_dir` | `BINARY_BUILD_DIR` | `BINARY_BUILD_DIR` | string | `''` |
| `service.docker_namespace` | `DOCKER_NAMESPACE` | `DOCKER_NAMESPACE` | string | `''` |
| `service.extra_copy_dirs` | `EXTRA_COPY_DIRS` | `EXTRA_COPY_DIRS` | csv | `''` |
| `service.allowed_envs` | `ALLOWED_ENVS` | `DOCKER_K8S_ALLOWED_ENVS` / `ALLOWED_ENVS` | csv | `dev,staging` |
| `service.deploy_branches` | `DEPLOY_BRANCHES` | `DEPLOY_BRANCHES` | csv | `dev,staging` |
| `service.config_path` | `CONFIG_PATH_TEMPLATE` | `CONFIG_PATH_TEMPLATE` | string | `config/config.{env}.yaml` |
| `service.configmap_name` | `CONFIGMAP_NAME_TEMPLATE` | `CONFIGMAP_NAME_TEMPLATE` | string | `{name}-config-{env}` |
| `service.cmd_args` | `CMD_ARGS_TEMPLATE` | `CMD_ARGS_TEMPLATE` | string | `-f resources/{configfile}` |
| `service.secret_name` | `K8S_SECRET_NAME` | `K8S_SECRET_NAME` | string | `{name}-secrets` |
| `service.secret_scan_dirs` | `SECRET_SCAN_DIRS` | `SECRET_SCAN_DIRS` | csv | `internal` |

#### `branch` — 批量切分支与 dev 回收 | Batch branch cut and dev recycle

| YAML 路径 | 导出的 env | 旧式单个 vars | 类型 | 默认值 |
|---|---|---|---|---|
| `branch.repos` | `BRANCH_OPS_REPOS` | `BRANCH_OPS_REPOS` | csv | `''` |
| `branch.base_branch` | `BRANCH_OPS_BASE` | `BRANCH_OPS_BASE` | string | `master` |
| `branch.source_branch` | `BRANCH_OPS_SOURCE` | `BRANCH_OPS_SOURCE` | string | `master` |
| `branch.target_branch` | `BRANCH_OPS_TARGET` | `BRANCH_OPS_TARGET` | string | `dev` |
| `branch.feature_prefix` | `BRANCH_OPS_FEATURE_PREFIX` | `BRANCH_OPS_FEATURE_PREFIX` | string | `feature/` |
| `branch.clean_features` | `BRANCH_OPS_CLEAN_FEATURES` | `BRANCH_OPS_CLEAN_FEATURES` | bool | `true` |
| `branch.require_today_tag` | `BRANCH_OPS_REQUIRE_TAG` | `BRANCH_OPS_REQUIRE_TAG` | bool | `false` |

#### `notify` — 通知渠道选择；webhook 凭证在 secrets 侧 | Channel choice; credentials live in secrets

| YAML 路径 | 导出的 env | 旧式单个 vars | 类型 | 默认值 |
|---|---|---|---|---|
| `notify.provider` | `NOTIFICATION_PROVIDER` | `NOTIFICATION_PROVIDER` | enum: none \| feishu \| wecom \| telegram \| all | `all` |

#### `env` — 容器里的非密钥环境变量，键名即变量名 | Plaintext container environment, keyed by name

自由键值表：键名由你定（必须是大写环境变量名），值是明文
整节合并后交给 [service-vars.sh](#service-varssh) 渲染成 Deployment 的 env 条目

```yaml
env:
  LOG_LEVEL: info
  GOMAXPROCS: "4"
```

聚合密钥（`PIPELINE_SECRETS`），骨架用 `--print-example --secrets` 生成：

#### `kube` — 集群凭证 | Cluster credentials

| YAML 路径 | 导出的 env | 旧式单个 secret | 类型 | 默认值 |
|---|---|---|---|---|
| `kube.kubeconfig` | `KUBECONFIG` | `KUBECONFIG` | secret | `''` |

#### `docker` — 镜像仓库凭证 | Registry credentials

| YAML 路径 | 导出的 env | 旧式单个 secret | 类型 | 默认值 |
|---|---|---|---|---|
| `docker.username` | `DOCKER_USERNAME` | `DOCKER_USERNAME` | secret | `''` |
| `docker.password` | `DOCKER_PASSWORD` | `DOCKER_PASSWORD` | secret | `''` |

#### `notify` — 通知 webhook 凭证 | Notification webhook credentials

| YAML 路径 | 导出的 env | 旧式单个 secret | 类型 | 默认值 |
|---|---|---|---|---|
| `notify.feishu_webhook_url` | `FEISHU_WEBHOOK_URL` | `FEISHU_WEBHOOK_URL` | secret | `''` |
| `notify.wecom_webhook_url` | `WECOM_WEBHOOK_URL` | `WECOM_WEBHOOK_URL` | secret | `''` |
| `notify.wecom_webhook_key` | `WECOM_WEBHOOK_KEY` | `WECOM_WEBHOOK_KEY` | secret | `''` |
| `notify.telegram_bot_token` | `TELEGRAM_BOT_TOKEN` | `TELEGRAM_BOT_TOKEN` | secret | `''` |
| `notify.telegram_chat_id` | `TELEGRAM_CHAT_ID` | `TELEGRAM_CHAT_ID` | secret | `''` |

#### `go` — 私有模块拉取凭证 | Private module credentials

| YAML 路径 | 导出的 env | 旧式单个 secret | 类型 | 默认值 |
|---|---|---|---|---|
| `go.app_id` | `APP_ID` | `APP_ID` | secret | `''` |
| `go.app_private_key` | `APP_PRIVATE_KEY` | `APP_PRIVATE_KEY` | secret | `''` |
| `go.git_ssh_private_key` | `GIT_SSH_PRIVATE_KEY` | `GIT_SSH_PRIVATE_KEY` | secret | `''` |
| `go.gpa_token` | `GH_GPA_TOKEN` | `GH_GPA_TOKEN` | secret | `''` |

`gpa_token` 是上下游 dispatch 与推送的**回退** PAT，优先级低于 App 凭证
三条 Go 工作流取凭证的顺序都是 App token 优先、其次 `GH_GPA_TOKEN`、最后 `github.token`
回落到 `github.token` 时跨仓库 dispatch 根本发不出去（它只对本仓库有效），
而 bump 用它推的提交**不触发本仓库任何 workflow**，于是升级结果无人校验

`app_private_key` 要写 GitHub App 私钥全文，在 YAML 里用 `|` 块标量写多行

#### `branch` — 跨仓库批量操作凭证 | Cross-repository batch credentials

| YAML 路径 | 导出的 env | 旧式单个 secret | 类型 | 默认值 |
|---|---|---|---|---|
| `branch.token` | `BRANCH_OPS_TOKEN` | `BRANCH_OPS_TOKEN` | secret | `''` |

#### `app` — 业务密钥，键名即容器里的环境变量名 | Service secrets, keyed by container env name

自由键值表：键名就是业务容器里的环境变量名，值是密钥内容
由 `backend-deploy-k8s` 的「同步密钥到集群」那步写成集群里的 K8s Secret，名单取自 `extra-env-block` 里的 `secretKeyRef`（也就是 service-vars.sh 自动抽出来的那份）

### 旧式单个变量（仍然有效）

上面每张表的「旧式单个 vars / secret」列就是原先那套变量名，一个都没废弃，优先级在聚合 YAML 之下
已经配好的仓库不动也能跑；想迁移就把它们合成一份 `PIPELINE_CONFIG`，迁完再把旧变量删掉

---
