# Composite Actions

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## 🧩 Composite Actions

`.github/actions/` 下的复合 Action，可在自己的工作流里单独引用

### resolve-config

所有配置的唯一入口：把聚合 YAML 校验、合并、兜底、打码之后写进 `GITHUB_ENV`，
之后同一个 job 里的步骤一律用 `${{ env.XXX }}` 取值
详见[配置章节](#-配置一份聚合-yaml)

| 入参 | 必填 | 说明 |
|------|------|------|
| `sections` | ❌ | 要解析哪些明文配置节，空格分隔：`ports` / `docker` / `kube` / `builder` / `cleanup` / `branch` / `notify` |
| `secret-sections` | ❌ | 要解析哪些密钥节，空格分隔：`kube` / `docker` / `notify` / `go` / `branch` / `app` |
| `vars-json` | ❌ | `${{ toJSON(vars) }}`；`PIPELINE_CONFIG`、分节 `<SECTION>_CONFIG`、旧式单个 vars 都从这里取 |
| `inputs-json` | ❌ | `${{ toJSON(inputs) }}`；作为 YAML 之下的一层回退 |
| `secrets-json` | ❌ | `${{ toJSON(secrets) }}`；`PIPELINE_SECRETS` 与旧式单个 secret 都从这里取 |
| `config` | ❌ | 直接传一份 YAML，优先级最高（适合调用方自己从别处读出来再传） |
| `config-file` | ❌ | 仓库内的配置文件路径，作为最低一层基线；需要先 checkout |
| `defaults` | ❌ | 本 workflow 的默认值覆盖，每行一个 `ENV_NAME=value`；优先级在旧式 vars 之下 |
| `summary` | ❌ | `true` 时把解析结果表写进 job summary |

| 出参 | 说明 |
|------|------|
| `json` | 解析结果的 JSON（不含密钥），job 级字段取不到 `GITHUB_ENV`，要跨 job 传值就用 `fromJSON(steps.<id>.outputs.json).XXX` |

```yaml
- name: ⚙️ Resolve Pipeline Config
  id: config
  uses: HitGameAI/deploy-pipeline/.github/actions/resolve-config@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
  with:
    sections: ports docker kube
    secret-sections: kube notify app
    vars-json: ${{ toJSON(vars) }}
    inputs-json: ${{ toJSON(inputs) }}
    secrets-json: ${{ toJSON(secrets) }}
    defaults: |
      WS_PORT=18085
```

密钥的处理：每个叶子值逐行 `::add-mask::` 打码（4 个字符以下不打，免得日志全是 `***`）；
`app` 节不进 `GITHUB_ENV`，而是落到 `$RUNNER_TEMP` 下 0600 的文件，由部署那步读去同步成集群 Secret

### resolve-service-vars

服务流水线的 setup 一步到位：算出产物与镜像身份、构建部署决策、ConfigMap 与启动参数、
容器 env 列表
替掉的是调用方 `pipeline.yml` 里那段 158 行 shell（三个服务仓库逐字节
相同，只差一个默认 Secret 名）
内部先跑 [resolve-config](#resolve-config) 解析
`service` / `env` / `docker` / `kube` / `notify` 几节，再跑 `scripts/service-vars.sh`

**调用方必须先 `actions/checkout`**：这一步要读服务的配置文件、扫源码抽密钥名

| 入参 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `environment` | ❌ | `dev` | 部署环境，对应 `service.config_path` 模板里的 `{env}` |
| `vars-json` / `inputs-json` / `secrets-json` | ❌ | `{}` | 三份上下文，透传给 resolve-config |
| `build-docker` / `deploy-k8s` | ❌ | `''` | 手动触发时的开关；push 事件忽略，按 `service.deploy_branches` 判断 |
| `code-quality` | ❌ | `false` | 原样透传给调用方的 job 条件 |
| `notification-provider` | ❌ | `''` | 留空则按 `notify.provider` 配置 |
| `config` | ❌ | `''` | 直接传一份聚合 YAML（优先级最高） |

出参 22 个：`binary-name` / `binary-source-dir` / `binary-output` / `binary-build-dir` /
`version` / `build-time` / `git-commit` / `image-base` / `image-name` / `release-mode` /
`deploy-environment` / `should-build-docker` / `should-deploy-k8s` / `code-quality` /
`notification-provider` / `k8s-config-yaml` / `k8s-configmap-name` / `k8s-enable-hpa` /
`k8s-secret-name` / `cmd-args` / `extra-copy-dirs` / `extra-env-block`

用法见[后端 Go 服务示例](#后端-go-服务示例)；各项默认值与模板占位符见
[`service` 配置节](#全部配置项)

### normalize-image-name

把 Docker 镜像名规范化，并按需生成 tag 列表

**为什么需要它**：Docker 对镜像名有两条硬规则 —— repository 部分必须全小写，tag 里不能出现 `/`
而 GitHub 的组织名常带大写（`MyOrgName`），分支名常带斜杠（`feature/turnover`），直接拼进镜像名
会在 push 阶段报 `invalid reference format`
这个 Action 把 repository 转小写、把 tag 里的 `/` 换成 `-`，
同时**不碰 registry 端口号**（`registry:5000/app:v1` 里 `/` 之前的冒号不会被当成 tag 分隔符）

| 入参 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `image-base` | 否 | `''` | 镜像基址，如 `ghcr.io/MyOrg/app` |
| `image-name` | 否 | `''` | 完整镜像名，如 `ghcr.io/MyOrg/app:Feature/X` |
| `generate-tags` | 否 | `false` | 是否额外输出 tag 列表（构建类工作流才需要）|

| 出参 | 说明 |
|------|------|
| `image-base` | 规范化后的基址 |
| `image-name` | 规范化后的完整镜像名 |
| `tags` | 多行 tag 字符串：`image-name` 一行 + `image-base:<清洗后的分支名>` 一行，仅 `generate-tags=true` 时输出 |

```yaml
- id: img
  uses: HitGameAI/deploy-pipeline/.github/actions/normalize-image-name@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
  with:
    image-base: ghcr.io/${{ github.repository_owner }}/${{ github.event.repository.name }}
    image-name: ghcr.io/${{ github.repository_owner }}/${{ github.event.repository.name }}:${{ github.ref_name }}
    generate-tags: 'true'
- run: echo "${{ steps.img.outputs.tags }}"
```

### print-runner-info

打印 runner 的内核、架构、CPU 型号与核数、内存、磁盘
无入参无出参

排查这几类问题时很省事：构建突然变慢（换了规格更低的 runner）、交叉编译结果不对
（架构与预期不符）、`go build` 被 OOM kill（内存不够）、磁盘写满导致 docker build 失败

```yaml
- uses: HitGameAI/deploy-pipeline/.github/actions/print-runner-info@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
```

### notify

统一的通知 Action，支持飞书 / 企业微信 / Telegram，详见
[通知与发布单](#-通知与发布单) 一节的「通用通知 Action」

### update-proto-mod

下游仓库升级 proto 模块引用，详见
[proto 模块引用升级](#proto-模块引用升级update-proto-mod) 一节

---

---

### fetch-pipeline-assets

把本仓库自带的 `scripts/` 与 `templates/` 复制到调用方工作区

| 入参 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `path` | 否 | `_deploy-workflows` | 目标目录（相对工作区） |
| `assets` | 否 | `scripts templates` | 要复制的顶层目录，空格分隔；只取用得到的那些 |

**为什么需要它，而不是直接 checkout 本仓库**

复用工作流要用到本仓库的 `scripts/` 与 `templates/`
直觉做法是

```yaml
- uses: actions/checkout@v6
  with: { repository: HitGameAI/deploy-pipeline, ref: 0c70ed9ef8fd8b81bad3423612fdb99be418ef2e }
```

但在复用工作流里，`GITHUB_TOKEN` 的作用域是**调用方仓库**，对本仓库没有读权限
而 GitHub 对
无权访问的私有仓库返回 **404 而不是 403**（避免泄漏仓库是否存在），所以报错是
`remote: Repository not found.` —— 看起来像仓库不存在，实际是没权限，这个误导性很强

Settings → Actions → General → Access 只放开 `uses:` 对私有工作流/Action 的**引用**权限，
不会给 checkout 提供凭据，所以开了 Access 也修不掉那一步

换成 action 之后**不需要任何凭据**：远程引用 action 时 runner 已经把整个仓库下载到 `_actions`
下，这两个目录本来就在手边，用 `GITHUB_ACTION_PATH` 相对定位即可

使用方：`backend-build-binary.yml`、`branch-cut.yml`、`branch-recycle.yml`

> 层数约定：action 位于 `.github/actions/<name>/`，所以回三层到仓库根
> `notify` action 里的 `${GITHUB_ACTION_PATH}/../../../scripts/notify.sh` 是同一层数，
> 改目录结构时两处要一起改
