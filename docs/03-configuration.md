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

配置是怎么解析出来的:

```mermaid
flowchart TD
    A["vars.PIPELINE_CONFIG<br/>一份聚合 YAML"] --> R
    B["secrets.PIPELINE_SECRETS<br/>一份聚合 YAML"] --> R
    C["workflow input<br/>只用来传上游算出的运行时值"] --> R
    D["workflow 自己的 defaults"] --> R
    E["schema.yaml 的默认值"] --> R
    R["resolve-config.py<br/>逐项校验，按上面的优先级合并"]
    R --> F["GITHUB_ENV<br/>明文配置与大部分密钥"]
    R --> G["RUNNER_TEMP 下权限 0600 的文件<br/>app 节的业务密钥，不进 GITHUB_ENV"]
```

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
或手动触发 [⚙️ Generate Pipeline Config](02-workflows.md#generate-pipeline-config) 让流水线打给你：

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
| `golib.watch` | `GOLIB_WATCH` | `watch` | `GOLIB_WATCH` | string | `''` |
| `golib.watch_max_stat` | `GOLIB_WATCH_MAX_STAT` | `watch-max-stat` | `GOLIB_WATCH_MAX_STAT` | string | `60` |
| `golib.watch_max_diff` | `GOLIB_WATCH_MAX_DIFF` | `watch-max-diff` | `GOLIB_WATCH_MAX_DIFF` | string | `120` |

**`watch` 与两个 `watch_max_*` 给 [`go-dep-watch.yml`](02-workflows.md) 用**,留空则整个 job 跳过

`watch` 是一个 **YAML 列表**,每组一个模块,各自带 `focus` 与 `probes`:

```yaml
golib:
  watch: |
    - module: example.com/upstream-sdk
      focus:
        - store/payload.go
      probes:
        - name: 客户端协议版本
          file: store/payload.go
          regex: 'ProtocolVersion\{[0-9, ]+\}'
    - module: github.com/example/transport
      focus:
        - transport/client.go
```

不用「逗号分隔的字符串」是因为探针的值是正则,里面本来就带逗号、分号、冒号,
任何单字符分隔符都会撞上 —— 第一版就是这么坏的,`[0-9, ]` 把一条探针切成两半,
而表现是探针静默取不到值、不报错

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
| `service.config_repo` | `CONFIG_REPO` | `CONFIG_REPO` | string | `''` |
| `service.config_ref` | `CONFIG_REF` | `CONFIG_REF` | string | `master` |
| `service.config_repo_path` | `CONFIG_REPO_PATH` | `CONFIG_REPO_PATH` | string | `{name}/{env}` |
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
| `notify.provider` | `NOTIFICATION_PROVIDER` | `NOTIFICATION_PROVIDER` | enum: none \| feishu \| wecom \| telegram \| email \| all | `all` |
| `notify.env_styles` | `NOTIFY_ENV_STYLES` | `NOTIFY_ENV_STYLES` | string | `''` |
| `notify.email_smtp_host` | `EMAIL_SMTP_HOST` | `EMAIL_SMTP_HOST` | string | `''` |
| `notify.email_smtp_port` | `EMAIL_SMTP_PORT` | `EMAIL_SMTP_PORT` | port | `465` |
| `notify.email_smtp_tls` | `EMAIL_SMTP_TLS` | `EMAIL_SMTP_TLS` | enum: '' \| ssl \| starttls \| none | `''` |
| `notify.email_from` | `EMAIL_FROM` | `EMAIL_FROM` | string | `''` |
| `notify.email_from_name` | `EMAIL_FROM_NAME` | `EMAIL_FROM_NAME` | string | `CI 流水线` |
| `notify.email_to` | `EMAIL_TO` | `EMAIL_TO` | string | `''` |

`notify.env_styles` 覆盖环境标签与配色，格式 `环境名=标签|主色|底色`，多项用逗号分隔，
只写要改的、其余用内置值（内置 `prod` / `staging` / `test` / `uat` / `dev`）
色值可以不带 `#`：聚合配置是 YAML，不带引号的 `#` 会被当成注释起点
邮件各项的用法、常见邮箱的地址端口与排错见 [通知与发布单](07-notifications.md)

### 业务配置放在一个中心仓库里

业务配置不进 `PIPELINE_CONFIG`,也不必留在服务仓库里 —— 用一个专门的仓库集中存放,
部署时按「服务 / 环境」把那一层的文件全部挂进 ConfigMap

```yaml
service:
  config_repo: your-org/service-configs
  config_ref: master              # 想让部署可复现就填 tag 或 commit
  config_repo_path: '{name}/{env}'   # 默认值,一般不用改
```

目录约定(`config_repo_path` 里的 `{name}` 是 `service.binary_name`,`{env}` 是本次部署的环境):

```text
service-configs/
├── demo-gateway/
│   ├── dev/config.yaml
│   └── staging/config.yaml
├── demo-api/
│   ├── dev/config.yaml
│   └── staging/config.yaml
└── demo-service/
    └── dev/config.yaml
```

`<服务>/<环境>/` 这一层里的**明文文件**全部进 ConfigMap,按文件名作 data 的键,与服务自己
那份配置挂在同一个目录(`<工作目录>/resources`) 只取这一层,**不递归** —— ConfigMap 的
data 键名是扁平的文件名,子目录里的文件一进来就只剩 basename,层级信息丢掉还会撞名

文件名带 `.enc.` 的是密文,走下面的解密那条路,不进 ConfigMap

文件内容原样进去,不经任何拼接 所以这里**不存在块标量缩进那一类问题**:文件就是文件

拉取与分流的全过程:

```mermaid
flowchart TD
    S{"service.config_repo 配了吗"} -->|没配| X["只用服务仓库自己那份配置"]
    S -->|配了| T["用 go.app_id 换一个跨仓库 App token"]
    T --> U["sparse clone 只取 服务 与 环境 那一层目录"]
    U --> V{"文件名里带 .enc. 吗"}
    V -->|不带| W["明文，直接进 ConfigMap"]
    V -->|带| Y["sops 加 age 解密<br/>私钥取自 config.age_key"]
    Y --> Z{"解开后的名字是 secrets.yaml 吗"}
    Z -->|是| AA["扁平的 KEY 冒号 value<br/>进业务 Secret，当环境变量注入"]
    Z -->|否| AB["按文件挂载<br/>进一个后缀 files 的 Secret"]
    W --> AC["projected volume 挂到容器的 resources 目录"]
    AB --> AC
    AC --> AD["容器侧路径与文件名不变<br/>服务不需要知道哪份是解密来的"]
```

#### 凭据:必须用 GitHub App,不能靠 GITHUB_TOKEN

这一条踩过就知道难查 复用工作流里 `GITHUB_TOKEN` 的作用域是**调用方仓库**,对配置仓库
没有读权限 而 GitHub 对无权访问的私有仓库返回 **404 而不是 403**(避免泄漏仓库是否存在),
所以失败信息是 `remote: Repository not found.` —— 看起来像仓库名写错了

`Settings → Actions → General → Access` 只放开 `uses:` 对私有 workflow / action 的引用,
**不给 checkout 提供凭据**,开了也修不掉这一步

所以流水线用调用方 `PIPELINE_SECRETS` 的 `go.app_id` / `go.app_private_key` 换一个配置仓库 token
必须同时满足两项：**安装范围包含配置仓库**，以及 **Contents 至少 Read-only 且安装已批准生效**
安装页只显示 metadata、actions、pull requests、workflows，不等于能读取仓库文件
仓库已经在清单里时不用反复添加，应检查 Contents 与待批准权限变更
配置仓库本身无需再保存一份 App 私钥
最新流程显式申请 contents:read，clone 失败还会用同一 token 自检，见[安装页权限对照](04-github-app.md#仓库已经勾选为什么仍然拉不到)

#### 加密:密文进仓库,明文只在流水线里

配置仓库里的文件可以是密文 用 [sops](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age):
age 的**公钥**写进配置仓库的 `.sops.yaml`,谁都能加密;**私钥**只配在 `PIPELINE_SECRETS`
的 `config.age_key` 里,只有流水线解得开

为什么用 sops 而不是整份文件 base64 或一个对称口令:sops **只加密值、保留键名**,
配置仓库的 diff 仍然看得出改了哪一项 —— 整份文件加密之后,review 能看到的只有「变了」

一次性准备(本机):

```bash
# 0. 本机装对平台的 sops：下面流水线用的是 linux.amd64 资产，macOS 上跑不起来
#    （踩过：/usr/local/bin/sops 放了个 Linux 二进制，报 exec format error）
brew install sops age

# 1. 生成一对 age 密钥
age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

# 2. 让 sops 找得到私钥：macOS 上它默认找
#    ~/Library/Application Support/sops/age/keys.txt，放在 ~/.config 下就得显式指定
export SOPS_AGE_KEY_FILE="$HOME/.config/sops/age/keys.txt"

# 3. 配置仓库根目录放 .sops.yaml，填公钥（age-keygen 打到标准输出那行）
cat > .sops.yaml <<'EOF'
creation_rules:
  - path_regex: \.enc\.(yaml|yml|json)$
    age: age1...
EOF

# 4. 私钥全文填进 PIPELINE_SECRETS 的 config.age_key
```

日常加密(本机):

```bash
# 新建密文：先写成目标文件名，再就地加密
#
# creation_rules 按「输入文件的路径」匹配，所以
#   sops --encrypt secrets.yaml > x.enc.yaml
# 会报 no matching creation rules found —— 输入那个名字不带 .enc.
cp secrets.yaml demo-gateway/dev/secrets.enc.yaml
sops --encrypt --in-place demo-gateway/dev/secrets.enc.yaml

# 或者显式指定按哪个文件名套规则
sops --encrypt --filename-override demo-gateway/dev/config.dev.enc.yaml config.yaml \
  > demo-gateway/dev/config.dev.enc.yaml

# 改已有的密文：直接编辑，存盘时就地重新加密，不用先解再加
sops demo-gateway/dev/config.dev.enc.yaml
```

只有文件名里带 `.enc.` 的才会被解密 解开后摘掉 `.enc.` 这一节,其余不动:
`config.dev.enc.yaml` → `config.dev.yaml`

两类密文按文件名分两条路:

| 密文文件 | 解开后落在哪 | 容器里怎么拿到 |
|---|---|---|
| `secrets.enc.yaml` | 业务 Secret(`kube.secret_name`,默认 `{name}-secrets`) | 环境变量,每个键一条 `secretKeyRef` |
| 其余 `*.enc.*` | 配置文件 Secret(同名加后缀 `-files`) | 挂在 `<工作目录>/resources` 下,文件名不变 |

`secrets.enc.yaml` 必须是**扁平**的 `KEY: value` 映射:

```yaml
DEMO_JWT_ACCESS_SECRET: ...
DEMO_SERVICE_AUTH_GATEWAY_TO_API_SECRET: ...
```

嵌套结构当场报错,不做任何转换 —— 环境变量只能是字符串,让它悄悄变成 `{'a': 1}` 注进容器,
服务会拿着一个看着像配置的垃圾值跑起来,比在流水线里失败难查得多

解开的配置文件**不进 ConfigMap,进 Secret** ConfigMap 在集群里是明文可读的(任何能
`get configmap` 的人都能看),把解开的东西放回去,这层加密就只剩「传输时是密文」
容器侧两者挂在同一个目录、文件名不变 —— 服务不需要知道哪份是明文来的、哪份是解密来的
(Deployment 的 config volume 因此是 `projected`,ConfigMap 与 Secret 两个来源都 `optional`,
任一侧不存在时 Pod 仍能起来,由服务自己报缺哪份配置)

同一个密钥在 `PIPELINE_SECRETS` 的 `app` 节和 `secrets.enc.yaml` 里各写一份时**直接报错**,
不猜哪边该赢:两边迟早会不一致,而不一致时服务拿到哪个值完全取决于一个看不见的默认,
排查时根本看不出来

两条路各扫一半,最后合起来:

```mermaid
sequenceDiagram
    autonumber
    participant SRC as 服务仓库的 Go 源码
    participant YML as 配置文件（在配置仓库）
    participant SETUP as backend-setup
    participant DEP as backend-deploy-k8s
    participant SEC as 集群里的 Secret
    participant POD as 容器

    SETUP->>SRC: 扫 internal 下的 env 等号形式的 struct tag
    SRC-->>SETUP: 环境变量名清单
    SETUP-->>DEP: extra-env-block，已经是 secretKeyRef 条目
    DEP->>YML: 扫配置里以 Env 结尾的配置项
    YML-->>DEP: 环境变量名清单
    DEP->>YML: 解密 secrets.enc.yaml，取它的键名
    YML-->>DEP: 环境变量名与值
    Note over DEP: 两份按名字取并集去重<br/>结果写进 GITHUB_ENV 的 MERGED_ENV_BLOCK
    DEP->>SEC: 按合并后的名单建 Secret<br/>值来自 app 节、同名单个 secret 或解密结果
    DEP->>POD: Deployment 的 env 用 secretKeyRef 指向这个 Secret
    POD->>SEC: 启动时取值
    Note over SETUP,DEP: 少了任一侧，被漏掉的变量不会报错<br/>服务会带着空值起来，错在运行期不在部署期
```

`secrets.enc.yaml` 还顺手补上一个洞:[service-vars.sh](#service-varssh) 扫的是**服务仓库里**
那份配置文件,配置搬进配置仓库之后它扫不到那些 `*Env:` 声明,不生成 `secretKeyRef`,
容器就拿不到这些环境变量 解密那步会按解开的键名补一份 env 条目,与 setup 传来的那份
按名字去重后合并,再交给渲染与同步 Secret 两步 —— 两边都读合并后的那一份

#### 两道闸门

**内容里有 `BEGIN ... PRIVATE KEY` 的直接拒** ConfigMap 是明文的,命名空间里有读权限
的人都能看 密钥走 `secrets` 侧的 `app` 节,渲染成 `secretKeyRef`,值只存在集群的 Secret 里
公钥证书(`BEGIN CERTIFICATE`)不受影响

**同名文件直接拒** 服务自己那份配置与配置仓库里的文件重名时不静默覆盖,而是报错并
指出是哪两个路径 —— data 的键名是文件名,重名的后果是一份悄悄顶掉另一份

尾换行按原文保留:以换行结尾的用 `|`,不以换行结尾的用 `|-` 有的解析器要求配置文件以
换行结尾,凭空加或凭空去掉都会出问题

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
| `notify.email_smtp_user` | `EMAIL_SMTP_USER` | `EMAIL_SMTP_USER` | secret | `''` |
| `notify.email_smtp_password` | `EMAIL_SMTP_PASSWORD` | `EMAIL_SMTP_PASSWORD` | secret | `''` |

邮件只有账号与密码算密钥，地址、端口、收件人在明文的 `notify` 节
密码多数邮箱要填「授权码」而不是登录密码

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

#### `config` — 业务配置仓库的解密凭据 | Config repository decryption credential

| YAML 路径 | 导出的 env | 旧式单个 secret | 类型 | 默认值 |
|---|---|---|---|---|
| `config.age_key` | `CONFIG_AGE_KEY` | `CONFIG_AGE_KEY` | secret | `''` |

age 私钥全文,`AGE-SECRET-KEY-` 开头那一行 留空而配置仓库里又有 `*.enc.*` 密文时,
部署会在解密那步失败并点出这一项;配置仓库里没有密文则这一项可以不填

#### `app` — 业务密钥，键名即容器里的环境变量名 | Service secrets, keyed by container env name

自由键值表：键名就是业务容器里的环境变量名，值是密钥内容
由 `backend-deploy-k8s` 的「同步密钥到集群」那步写成集群里的 K8s Secret，名单取自 `extra-env-block` 里的 `secretKeyRef`（也就是 service-vars.sh 自动抽出来的那份）

### 旧式单个变量（仍然有效）

上面每张表的「旧式单个 vars / secret」列就是原先那套变量名，一个都没废弃，优先级在聚合 YAML 之下
已经配好的仓库不动也能跑；想迁移就把它们合成一份 `PIPELINE_CONFIG`，迁完再把旧变量删掉

---
