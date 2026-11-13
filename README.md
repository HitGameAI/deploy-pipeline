# Deploy Pipeline

可复用的 GitHub Actions 流水线，覆盖 Go 后端、前端构建与 Kubernetes 部署，以及 Go 库的测试、发版与上下游联动

**第一次落地按 0 → 7 走完，先部署一个 dev 服务，再按第 8 节启用可选功能**
本指南以「Go HTTP 服务 → GHCR → Kubernetes」为起步路径，换组织时也适用

[选择能力](#0-先选需要的能力) · [准备仓库](#1-准备仓库放开流水线引用) · [准备集群](#2-准备集群和镜像仓库) · [App 权限](#3-按用途创建-github-app不需要就跳过) · [配置填写](#4-在业务仓库保存两份聚合-yaml) · [首次部署](#7-校验配置完成第一次部署) · [故障定位](#9-常见故障先看失败阶段) · [交接清单](#10-交接或换组织时的清单)

## 0. 先选需要的能力

| 目标 | 必须准备 | 暂时不用配 | 接入入口 |
|---|---|---|---|
| Go 服务构建、推镜像、部署 K8s | 业务仓库、流水线仓库、GHCR、可访问的集群、两份聚合 YAML | 无私有依赖和集中配置时，不需要 GitHub App | 本页 1 → 7 |
| 拉取私有 Go 模块 | 上述基础 + GitHub App 或 SSH 凭据 | 使用 App 时不用配 SSH 私钥或 PAT | [8.1](#81-私有-go-模块) |
| 集中配置仓库 | GitHub App、配置仓库、目录约定 | 无加密文件时不用配 age 私钥 | [8.2](#82-集中配置仓库) |
| 前端部署 | Node 构建配置、镜像仓库、集群 | 不需要 Go 构建认证 | [前端接入](docs/06-quickstart.md#前端应用示例) |
| Go 库只跑测试 | go.mod、测试入口；私有依赖另配读凭据 | 不需要 K8s、镜像仓库、配置仓库 | [Go 库接入](docs/05-go-library-pipeline.md) |
| Go 库自动通知下游、升级依赖 | App、上下游入口、仓库安装范围 | 不需要 K8s、镜像仓库 | [Go 库联动](docs/05-go-library-pipeline.md) |

示例中的 `your-org`、`your-service` 换成自己的组织与业务仓库名
当前公共入口是 `HitGameAI/deploy-pipeline@v1.0.0`，迁移后应指向自己的流水线仓库

## 1. 准备仓库，放开流水线引用

| 仓库 | 职责 | 要配置什么 |
|---|---|---|
| `your-org/deploy-pipeline` | 放本仓库的 workflows、actions、scripts、templates | 私有仓库开放 Actions 引用；普通服务部署不需要在这里存业务凭据 |
| `your-org/your-service` | 放源码与 `.github/workflows/pipeline.yml` | PIPELINE_CONFIG、PIPELINE_SECRETS、部署 Environment |
| `your-org/service-configs` | 可选，集中保存业务配置 | 启用时让 App 安装覆盖它；无需再存一份 App 私钥 |
| 私有 Go 模块仓库 | 可选，提供构建依赖 | 使用 App 时让安装覆盖它们 |

在新组织创建 `deploy-pipeline` 仓库，同步本仓库内容，并确认调用使用的分支存在
**不能只改业务仓库里的 uses 路径**：流水线内部的 action、workflow 自引用也要指向新组织
新组织从零初始化与已有副本的增量同步分别按[迁移步骤](#多组织同步与迁移)操作

流水线仓库是私有仓库时，进入：

```text
deploy-pipeline → Settings → Actions → General → Access
→ Accessible from repositories in the organization → Save
```

业务仓库的 Actions 策略也要允许这套复用工作流及其引用的第三方 Actions
Access 开关解决 uses 引用权限，配置仓库的 git clone 则依赖单独生成的 App token
详见 [GitHub 私有工作流共享说明](https://docs.github.com/en/actions/how-tos/reuse-automations/share-with-your-organization)

## 2. 准备集群和镜像仓库

| 项目 | 起步要求 | 是否必需 |
|---|---|---|
| Kubernetes API | GitHub runner 能访问，kubeconfig 地址、证书和凭据有效 | 部署必需 |
| 部署权限 | 当前实现会 apply Namespace，管理目标 namespace 的工作负载、Service、ConfigMap、Secret；读取 Pod、事件、日志用于诊断 | 部署必需；只读 kubeconfig 不够 |
| CPU 架构 | builder.arch、docker.arch 与目标节点一致 | 必需；本指南以 amd64 为例 |
| 镜像推送 | 允许 Actions 推送 GHCR 包；已有包需授权业务仓库访问 | 推镜像必需 |
| 镜像拉取 | 节点能访问 registry；imagePullSecret 已在目标 namespace 创建 | 私有镜像必需，见第 5 节 |
| Metrics Server | 为 HPA 提供指标 | 开 HPA 才需要，起步配置已关闭 |
| Traefik 与 CRD | 本仓库生成的是 IngressRoute / Middleware | 配域名入口才需要，起步配置不生成 |
| DNS、ACME certResolver | 域名解析、Traefik 证书签发配置 | 开域名 / HTTPS 才需要 |
| 数据库、Redis 等 | 业务依赖就绪、网络可达 | 由业务决定，流水线不会创建它们 |

默认工作流使用 ubuntu-latest，不会自动连进公司内网
内网集群需准备网络可达的自托管 runner 等执行环境，并调整对应工作流的 runs-on
本指南构建脚本适用于无需 CGO 的 Go 服务，有特殊编译依赖时看[公共脚本](docs/10-scripts.md)

## 3. 按用途创建 GitHub App，不需要就跳过

**只拉公开 Go 模块、配置也在业务仓库里时，本步跳过**
创建路径：组织 Settings → Developer settings → GitHub Apps → New GitHub App
建议归组织所有，由组织维护者保管私钥和安装关系，界面操作见 [GitHub App 指南](docs/04-github-app.md)

### Repository permissions 怎么选

| 权限 | 拉私有模块 / 配置仓库 | Go 库升级、推代码、开 PR | 什么时候才额外开启 |
|---|---|---|---|
| **Metadata** | **Read-only，自动授予** | **Read-only，自动授予** | 基础权限，无需手动写进 YAML |
| **Contents** | **Read-only** | **Read and write** | clone、读取文件、推代码、跨仓库 repository_dispatch |
| **Pull requests** | **No access** | **Read and write** | 仅查看 PR 用 Read-only，创建或评论 PR 用写权限 |
| **Secrets** | **No access** | **No access** | 仅 bootstrap 给别的仓库写 Actions secrets 时给 Read and write |
| **Workflows** | **No access** | **No access** | 仅自动创建 / 修改 .github/workflows/* 文件时给写权限 |
| **Actions** | **No access** | **No access** | 操作 workflow runs、artifacts、workflow_dispatch 时按功能添加 |
| **Administration** | **No access** | **No access** | 普通部署和依赖升级不需要 |
| Organization / Account permissions | 不开启 | 不开启 | 本页两种路径不需要 |

这里配置的是 **GitHub App 后台权限**
业务 pipeline.yml 里的 permissions 配置的是本次运行的 **GITHUB_TOKEN**，两个地方分开看
例如推 GHCR 用 packages: write，不意味着要给拉配置的 App 增加权限

### App 建好后还有两步

1. **安装范围**：Install App → 目标组织 → Repository access，加入配置仓库、所需私有模块仓库；依赖联动还要覆盖上下游仓库
2. **批准权限**：给已有 App 增加权限后，到组织的 Installed GitHub Apps 中批准待处理变更

Repository permissions 决定能做什么，安装范围决定能访问哪些仓库，两个都要满足
新增权限未经组织批准不会对已有安装生效，参见 [GitHub 权限变更规则](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration#changing-the-permissions-of-a-github-app)

**安装页已经列出 service-configs，也不代表有 Contents 权限**
例如只显示 `Read access to metadata` 和 `Read and write access to actions, pull requests, and workflows` 时，仍须检查 Contents
权限列表应包含 contents；没有就到 App 的 Permissions & events 添加并批准，详见[安装页逐项对照](docs/04-github-app.md#仓库已经勾选为什么仍然拉不到)

记录 App ID，生成并保存完整 PEM 私钥，放入**业务仓库**的 PIPELINE_SECRETS.go，见 8.1
不要把私钥上传到 Git，也不用在被读取的配置仓库重复存一份

## 4. 在业务仓库保存两份聚合 YAML

首次落地先用 Repository 级配置，Environment 只创建一个 dev

```text
your-service → Settings → Environments → New environment → dev
your-service → Settings → Secrets and variables → Actions
  Variables → New repository variable → PIPELINE_CONFIG
  Secrets   → New repository secret   → PIPELINE_SECRETS
```

### 先运行现有配置生成工作流

打开流水线仓库的 [Actions → ⚙️ Generate Pipeline Config](https://github.com/HitGameAI/deploy-pipeline/actions/workflows/generate-config.yml)：

1. 点击 Run workflow，选择已发布的分支，mode 选 generate
2. sections 留空生成全部节；只看后端服务时填 `ports docker kube builder service notify env`
3. 打开运行的 Summary，查看带注释的明文配置和密钥骨架；也可下载 pipeline-config artifact
4. 按下面的必填 / 选配表修改，再分别保存为业务仓库的 PIPELINE_CONFIG / PIPELINE_SECRETS

生成器源码是 [generate-config.yml](.github/workflows/generate-config.yml)，完整键、默认值和注释由 schema 统一生成
迁移组织后使用新流水线仓库的同名工作流，不需要维护额外的配置模板文件
生成结果是配置参考，不会替你创建 GitHub Variable / Secret，也不会生成业务仓库的 pipeline.yml

### PIPELINE_CONFIG：明文配置

以生成结果为准，仅保留需要的配置项，并检查下表
首次部署显式关闭 HPA、域名入口、通知和未使用端口，服务配置文件先放在业务仓库

| 路径 | 起步示例 | 你要确认什么 |
|---|---|---|
| ports.http | 8080 | 程序实际监听该端口和 0.0.0.0；探针默认是 TCP |
| ports.rpc / ws / pprof | 0 | 没有的端口显式关闭，避免沿用历史默认值 |
| builder.go_version | 1.25.1 | 改成项目 go.mod 所需的版本 |
| builder.go_auth_method | none | 无私有模块用 none；有则按 8.1 配 App |
| builder.arch + docker.arch | amd64 | 两处一起改，匹配目标节点 |
| service.binary_build_dir | ./cmd/server | main 包目录；在根目录时填 . |
| service.allowed_envs + service.deploy_branches | dev | 环境白名单与 push 部署分支都要匹配 |
| service.config_path | config/config.{env}.yaml | dev 部署要求业务仓库存在 config/config.dev.yaml |
| service.cmd_args | -f resources/{configfile} | 适用于支持 -f 的服务；其他程序改成实际参数，无参数填空字符串 |
| kube.hpa.enable | false | 首次先关闭，确认 Metrics Server 可用后再开启 |
| kube.ingress.host | 空字符串 | 首次不创建域名入口 |
| notify.provider | none | 首次先关闭通知 |
| kube.namespace | demo-dev | 与镜像拉取 Secret 所在 namespace 一致 |
| kube.image_pull_secrets | ghcr-cred | **只引用已有 Secret 的名字**，不会自动创建 registry 凭据 |

服务配置文件由程序定义，内容应是该服务真实需要的非敏感配置
默认挂载后，config/config.dev.yaml 对应容器工作目录下的 resources/config.dev.yaml
无需文件配置的服务将 service.config_path、service.cmd_args 都设成 `''`
密钥放在 app 节或加密文件，见[完整配置参考](docs/03-configuration.md)

### PIPELINE_SECRETS：密钥配置

使用同一次生成结果中的密钥骨架，部署至少填写 kube.kubeconfig 的完整内容，保留多行缩进
生成骨架中的占位值必须替换，不使用的节直接删除

其他项按条件增加，**没有对应功能就整个节省略**：

| YAML 路径 | 什么时候必填 | 不用配的情况 |
|---|---|---|
| kube.kubeconfig | 部署到 K8s | 仅测试或构建 |
| go.app_id + go.app_private_key | App 认证的私有模块、集中配置仓库、依赖联动 | 无这些功能 |
| go.git_ssh_private_key | builder.go_auth_method: ssh | 用 App 或 none |
| docker.username + docker.password | 外部 registry，或需要自备推送凭据 | 当前仓库 GHCR 推送可用 GITHUB_TOKEN 时 |
| config.age_key | 配置仓库存在 SOPS + age 加密文件 | 只有明文非敏感配置 |
| app.<环境变量名> | 服务依赖对应业务密钥 | 服务不需要这些密钥 |
| notify.* | 开启对应通知渠道 | notify.provider: none |
| go.gpa_token | 依赖联动采用 PAT 回退 | 已使用 App |

APP_ID、CONFIG_AGE_KEY 等是解析后给步骤用的环境变量名
**新接入只维护 PIPELINE_CONFIG / PIPELINE_SECRETS**，不要照日志再创建同名独立变量或 Secret
本仓库仍兼容旧的独立 vars / secrets 和分节配置，聚合密钥优先于同名独立 Secret
新接入统一使用上述两份聚合 YAML；已有项目的回退规则见[配置参考](docs/03-configuration.md)

## 5. 分清推镜像凭据与集群拉镜像凭据

**Actions 推得上去，不代表 Kubernetes 拉得下来**

| 使用方 | 凭据来源 | 配在哪里 |
|---|---|---|
| Actions → GHCR | 默认 github.actor + 本次运行的 GITHUB_TOKEN | 接入示例已声明 packages: write，通常不用额外填 docker 密钥 |
| Actions → 其他 registry | registry 用户名、密码 / token | PIPELINE_SECRETS.docker |
| K8s → 私有 GHCR 镜像 | 有包读取权限的账号与 PAT classic，至少 read:packages | 目标 namespace 的 imagePullSecret |

已有 GHCR 包检查 **Manage Actions access**，确保业务仓库有写入权限
组织使用 SSO 时 PAT 也要满足组织授权要求，参见 [GHCR 认证说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-to-the-container-registry)
不要把短期 GITHUB_TOKEN 当作集群长期拉镜像凭据

私有镜像首次部署前，在管理员终端创建 Secret
REGISTRY_USER / REGISTRY_TOKEN 是已准备好的用户名和 token：

```bash
kubectl create namespace demo-dev --dry-run=client -o yaml | kubectl apply -f -
kubectl -n demo-dev create secret docker-registry ghcr-cred \
  --docker-server=ghcr.io \
  --docker-username="$REGISTRY_USER" \
  --docker-password="$REGISTRY_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
```

命令在有集群权限的终端执行，不是贴进 GitHub Variable
其他 registry 同步替换 server、凭据和 docker.registry

## 6. 复制业务仓库的流水线入口

按照[快速接入中的后端调用示例](docs/06-quickstart.md#后端-go-服务示例)创建业务仓库的 `.github/workflows/pipeline.yml`
调用示例连接四个阶段，业务配置不用重复写进 with：

```text
Setup → Build Binary → Build Docker → Deploy K8s
```

调用示例显式传递 PIPELINE_SECRETS，其中 app 节的业务密钥也一起传递
若仍使用分散的旧业务 Secrets，先按[配置指南](docs/03-configuration.md)迁移到 app 节

| 触发方式 | 调用示例的行为 |
|---|---|
| push 到 dev | 构建、推镜像、部署 dev |
| 手动选择 dev | 构建并部署 dev |
| push v* tag | 构建、推镜像、生成 GitHub Release，**不部署集群** |

调用示例的 permissions 中 contents: write / packages: write / pull-requests: read 分别供内嵌发布单、镜像推送、PR 明细读取使用
不要因 dev 暂时不生成发布单而删权限：复用工作流声明的权限也受调用方上限限制
迁移后确认调用示例里每个 HitGameAI/deploy-pipeline 都已替换，且引用的 master 分支存在

## 7. 校验配置，完成第一次部署

明文配置可直接在同一个 Generate Pipeline Config 工作流选 `mode: validate`，把 YAML 填进 config 入参
该模式校验明文总表或单节配置，密钥 YAML 用下面的本地 `--secrets` 校验，不要贴进公开的 workflow 输入

本地校验在流水线仓库执行，依赖 Python 3 和 PyYAML：

```bash
python3 -m venv /tmp/pipeline-venv
/tmp/pipeline-venv/bin/pip install pyyaml

# 将改好的配置和密钥保存在仓库外的临时文件里，再校验
/tmp/pipeline-venv/bin/python scripts/resolve-config.py --check /tmp/pipeline-config.yaml
/tmp/pipeline-venv/bin/python scripts/resolve-config.py --check /tmp/pipeline-secrets.yaml --secrets
```

通过后把内容保存到第 4 节的 GitHub Variable / Secret，提交业务入口和非敏感服务配置
手动触发入口需存在于业务仓库默认分支，随后在 Actions → Backend CI/CD → Run workflow 选择 dev 分支运行
也可以 push 到调用示例监听的 dev 分支直接触发

| 阶段 | 成功标准 | 失败先检查 |
|---|---|---|
| Setup | summary 环境、入口目录、配置路径、镜像名正确 | YAML 作用域、服务文件路径、分支 / 环境白名单 |
| Build Binary | 产出 <服务名>-binary artifact | main 包、Go 版本、私有依赖认证 |
| Build Docker | GHCR 出现本次 tag，架构正确 | packages 权限、包授权、登录凭据 |
| Deploy K8s | rollout 成功，Pod Ready | API 网络、RBAC、imagePullSecret、配置和启动参数 |

以服务 your-service、namespace demo-dev 为例：

```bash
kubectl -n demo-dev rollout status deployment/your-service --timeout=300s
kubectl -n demo-dev get pods,svc
kubectl -n demo-dev logs deployment/your-service --tail=100
kubectl -n demo-dev port-forward service/your-service 8080:8080
```

访问 `http://127.0.0.1:8080/<服务真实接口>` 确认业务响应
资源名默认取业务仓库名，设置了 service.binary_name 则以配置值为准

## 8. 跑通后，按需启用

### 8.1 私有 Go 模块

在 PIPELINE_CONFIG 中合并以下键：

```yaml
builder:
  goprivate: github.com/your-org/*
  go_auth_method: app-token
```

在 PIPELINE_SECRETS 中增加 go 节，保留原有的 kube 等节：

```yaml
go:
  app_id: '123456'
  app_private_key: |
    -----BEGIN RSA PRIVATE KEY-----
    <完整 PEM 私钥内容>
    -----END RSA PRIVATE KEY-----
```

App 至少具备 Contents Read-only，安装到私有依赖仓库
后端构建按业务仓库所属组织申请安装令牌，依赖须在该安装可访问的范围内
跨组织依赖需单独设计对应组织的认证，扩展 goprivate 不会自动获得访问权限
本路径不需要 go.git_ssh_private_key 或 go.gpa_token

### 8.2 集中配置仓库

在 PIPELINE_CONFIG.service 下增加，保留第 4 节其他 service 配置：

```yaml
service:
  config_repo: your-org/service-configs
  config_ref: master                       # 改成配置仓库真实存在的分支
  config_repo_path: '{name}/{env}'
```

App 凭据仍放业务仓库的 PIPELINE_SECRETS.go
安装范围覆盖 service-configs，Contents 至少 Read-only，并完成权限变更批准
配置仓库无需设置 Actions Access，也无需存 App 私钥

```text
service-configs/
└── your-service/
    └── dev/
        └── config.dev.yaml
```

your-service 对应 service.binary_name，默认取业务仓库名
文件名与 service.config_path 推导出的名称保持一致；搬迁后移除业务仓库同名配置文件，避免两个来源重复
明文文件进 ConfigMap；SOPS 文件解密后进 K8s Secret
只有加密文件才需 PIPELINE_SECRETS.config.age_key，密钥生成与加密步骤见[配置仓库加密指南](docs/03-configuration.md#加密密文进仓库明文只在流水线里)

### 8.3 其他可选功能

| 能力 | 改哪里的配置 | 还需准备 |
|---|---|---|
| 明文环境变量 | PIPELINE_CONFIG.env | 业务定义的键值 |
| 密钥环境变量 | PIPELINE_SECRETS.app | 键名就是容器环境变量名 |
| 飞书 / 企业微信 / Telegram | PIPELINE_CONFIG.notify.provider + PIPELINE_SECRETS.notify | webhook / bot 凭据，见[通知指南](docs/07-notifications.md) |
| HPA | kube.hpa.enable: true 与副本范围 | Metrics Server、合理的 resource requests |
| 域名入口 | kube.ingress.host | Traefik CRD、入口点、DNS |
| HTTPS | kube.ingress.tls: true、cert_resolver | Traefik 中真实存在的 ACME resolver |
| staging / prod | service.allowed_envs、deploy_branches 与入口触发条件 | 同名 GitHub Environment、配置、审批规则 |
| Go 库 CI / 上下游升级 | templates/golib/ 中相应入口 | [Go 库指南](docs/05-go-library-pipeline.md)，权限按第 3 节选择 |

**Environment 上同名聚合 YAML 整体覆盖 Repository 上那份，不做逐键合并**
首次接入用 Repository 级基线；环境不同时为每个环境保存完整 YAML，不要只写 kube 节而丢掉 go / app 等密钥
新增 prod 还要改业务工作流的手动选项、分支触发条件；只创建 Environment 不会自动开启生产部署

## 9. 常见故障：先看失败阶段

| 现象 | 优先检查 |
|---|---|
| workflow was not found | 仓库 / 分支路径、私有 Actions Access、调用方 Actions 策略、迁移后内部自引用 |
| App 安装查询 404 | 当前 go.app_id 的 App 是否覆盖目标组织和仓库，仓库名是否正确 |
| token 申请权限不足 | App 的 Contents、组织是否批准变更 |
| clone 报 Repository not found | 不能直接断言没装；查同一 App 的安装范围、Contents、生效状态及最新自检输出 |
| 凭据已配但读不到 | 业务仓库的 PIPELINE_SECRETS 是否传给被调工作流，Environment 是否整份覆盖仓库配置 |
| 私有模块下载 404 / 走公共代理 | goprivate、go_auth_method、依赖所在组织和安装范围 |
| 镜像推送 denied | GITHUB_TOKEN 的 packages: write、已有 GHCR 包 Actions access、registry 凭据 |
| ImagePullBackOff | 集群 imagePullSecret、namespace、包读取权限、镜像 tag、节点网络与架构 |
| 配置找不到 / 容器启动失败 | config_path、配置仓库目录与文件名、cmd_args、程序真实参数格式 |
| Pod 不 Ready | 实际监听地址和端口、TCP 探针、业务依赖、内存限制、容器日志 |
| HPA / IngressRoute 失败 | 是否启用了尚未安装的 Metrics Server / Traefik CRD |
| 改完仍看到旧提示 | 确认引用包含新提交，重新触发；重跑失败 job 会复用原先解析到的工作流 SHA |

重跑版本规则见 [GitHub 复用工作流说明](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)

## 10. 交接或换组织时的清单

- [ ] 新组织流水线内容、内部自引用、默认分支和 Actions Access 已确认
- [ ] 组织维护者能管理 App、私钥、安装清单和权限批准，不是只留下某个人的 PAT
- [ ] 业务仓库的 PIPELINE_CONFIG / PIPELINE_SECRETS 与各 Environment 完整配置已确认
- [ ] 记录集群 API 可达方式、部署权限、namespace、节点架构
- [ ] 明确推镜像与集群拉镜像凭据的维护者及更新方式
- [ ] 记录配置仓库、真实 ref、目录和 age 密钥保管位置；未启用则注明无需配置
- [ ] 用第 7 节从新入口跑通 dev 服务，保存运行链接并验证业务接口
- [ ] 保留可回退的镜像 tag / digest 和配置版本，再扩展到 staging / prod

## 多组织同步与迁移

### 新组织首次初始化

先在新组织创建空的 deploy-pipeline 仓库，再完整克隆当前这份代码、修改 origin
不要直接用 sync-fork.sh 初始化空仓库：它有 KEEP_DIVERGENT 跳过清单，包含配置解析器及核心工作流

```bash
git clone --branch master https://github.com/HitGameAI/deploy-pipeline.git ../deploy-pipeline-new
git -C ../deploy-pipeline-new remote set-url origin https://github.com/your-org/deploy-pipeline.git

# 只替换流水线自身的 owner/repo 引用，不全局替换组织名
# 将下面 your-org/deploy-pipeline 改成新仓库；已存在的其他业务模块路径保持原样
python3 - <<'PY'
from pathlib import Path
import subprocess

repo = Path('../deploy-pipeline-new')
source = 'HitGameAI/deploy-pipeline'
target = 'your-org/deploy-pipeline'
paths = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '-z'])
for name in paths.decode().split('\0'):
    path = repo / name
    if not name or not path.is_file():
        continue
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        continue
    if source in text:
        path.write_text(text.replace(source, target), encoding='utf-8')
PY
```

在新副本里检查 diff，执行 `python3 scripts/self-test.py`，检查所有内部自引用
确认无误后提交并推送 master 到新组织，再配置第 1 节的 Actions Access，业务入口也改为新引用
此步骤保留来源历史；业务依赖模块、通知目标、依赖图扫描组织等按自身业务另行调整，替换自引用不会迁移它们

### 已有副本的增量同步

[scripts/sync-fork.sh](scripts/sync-fork.sh) 从双方 git remote 推导完整 owner/repo，替换自引用：

```bash
git clone https://github.com/your-org/deploy-pipeline.git ../deploy-pipeline-fork
bash scripts/sync-fork.sh ../deploy-pipeline-fork --check
bash scripts/sync-fork.sh ../deploy-pipeline-fork
```

脚本只改目标工作区，不自动提交或推送；到目标仓库检查 diff 后再提交发布
它会保留 KEEP_DIVERGENT 列出的差异文件，必须核对输出并手动迁移相应改动
`--force` 会删除目标多出的文件，仍然跳过差异文件，并不代表完整镜像复制
更换分支还需手动更新 ref；业务仓库入口也要单独更新

## 按主题查阅

| 文档 | 解决什么问题 |
|---|---|
| [01 项目架构](docs/01-architecture.md) | 目录职责与脚本引用 |
| [02 工作流列表](docs/02-workflows.md) | 输入、输出与职责 |
| [03 配置完整参考](docs/03-configuration.md) | 聚合 YAML、全部键、优先级、SOPS |
| [04 GitHub App](docs/04-github-app.md) | 注册、权限、安装、批准、403 / 404 |
| [05 Go 库流水线](docs/05-go-library-pipeline.md) | 测试矩阵、发版、上下游升级与 bootstrap |
| [06 接入与多架构](docs/06-quickstart.md) | 后端调用示例、前端、Monorepo、arm64 |
| [07 通知与发布单](docs/07-notifications.md) | 通知渠道与 Release |
| [08 Kubernetes](docs/08-kubernetes.md) | 模板、资源、探针、HPA、Traefik |
| [09 Composite Actions](docs/09-composite-actions.md) | 公共 Action 入参与出参 |
| [10 公共脚本](docs/10-scripts.md) | scripts 下工具用法 |
| [11 自测](docs/11-self-check.md) | 本地与 CI 检查 |
| [12 依赖图](docs/12-dependency-graph.md) | 模块依赖、影响面与发版顺序 |

## 许可证

MIT
