# Deploy Pipeline

可复用的 GitHub Actions 工作流集合，支持 **Go 后端服务** 和 **前端应用** 的完整 CI/CD 流程及 Kubernetes 部署

## 📑 目录

| 章节 | 看这里解决什么问题 |
|------|-------------------|
| [项目架构](#️-项目架构) | 这个仓库里有什么、各目录的职责 |
| [工作流列表](#-工作流列表) | 有哪些可复用工作流，分别干什么 |
| [Secrets 和 Variables 配置](#-secrets-和-variables-配置) | 接入前要在仓库里配哪些变量与密钥 |
| [GitHub App 创建与配置](#-github-app-创建与配置) | 拉私有 Go 模块 / 跨仓库推送要的 APP_ID 怎么来 |
| [快速接入](#-快速接入) | 复制一份能跑的 pipeline.yml |
| [多架构构建](#️-多架构构建amd64--arm64) | 怎么出 arm64 镜像 |
| [通知与发布单](#-通知与发布单) | 飞书 / 企业微信 / Telegram 通知与 Release Note |
| [工作流详细说明](#-工作流详细说明) | 每个工作流的完整入参与 Secrets |
| [K8s 部署架构](#-k8s-部署架构) | 部署出来的资源长什么样 |
| [K8s 模板清单与占位符](#-k8s-模板清单与占位符) | 12 个 yaml 模板分别何时渲染、占位符从哪来 |
| [Composite Actions](#-composite-actions) | 四个复合 Action 的入参与出参 |
| [公共脚本](#-公共脚本) | scripts/ 下每个脚本的用法 |
| [工作流脚本引用机制](#-工作流脚本引用机制) | 工作流是怎么找到这些脚本的 |
| [自测清单](#-自测清单) | 改完之后怎么验 |

---

## 🏗️ 项目架构

```bash
deploy-pipeline/
├── .github/
│   ├── workflows/               # 可复用工作流
│   │   ├── code-quality.yml         # 代码质量检查
│   │   ├── cleanup-artifacts.yml   # Artifacts + GHCR 镜像清理
│   │   ├── backend-build-binary.yml # 编译 Go 二进制文件
│   │   ├── backend-build-docker.yml # 构建推送后端 Docker 镜像（tag 发布模式自动生成发布单）
│   │   ├── backend-deploy-k8s.yml   # K8s 部署（后端服务）
│   │   ├── frontend-build-frontend.yml # 前端构建（支持 npm/pnpm/yarn）
│   │   ├── frontend-build-docker.yml   # 构建推送前端 Docker 镜像（tag 发布模式自动生成发布单）
│   │   ├── frontend-deploy-k8s.yml     # K8s 部署（前端应用）
│   │   ├── release-note.yml           # tag 发布单（自动生成 GitHub Release）
│   │   ├── pr-opened-notify.yml       # PR 开启变更摘要通知
│   │   ├── pr-merged-notify.yml       # PR 合并通知
│   │   ├── branch-recycle.yml         # dev 回收重切 + 已合并 feature 清理（周三发布后）
│   │   └── branch-cut.yml             # 批量切分支（新迭代从 master 切 feature/xx）
│   └── actions/                 # 可复用 Composite Actions
│       ├── notify/              # 通用通知（飞书/企业微信/Telegram，支持自定义标题正文）
│       ├── update-proto-mod/    # 升级 go.mod 中 proto 模块引用并提 PR
│       ├── print-runner-info/   # 打印 Runner 环境信息
│       └── normalize-image-name/ # 镜像名规范与 tag 生成
├── scripts/                     # 公共脚本
│   ├── backend/                 # 后端脚本
│   │   └── build-linux.sh       # Go 构建脚本
│   ├── frontend/                # 前端脚本
│   │   └── build-frontend.sh    # 前端构建脚本
│   ├── common.sh                # 通用函数库
│   └── notify.sh                # 飞书/企业微信/Telegram 通知
├── templates/                   # K8s 资源模板
│   └── k8s/                     # Kubernetes 模板
│       ├── backend/             # 后端服务模板
│       │   ├── deployment.yaml  # Deployment 模板
│       │   ├── service.yaml     # Service 模板
│       │   ├── hpa.yaml         # HPA 模板
│       │   └── ingress.yaml     # IngressRoute 模板
│       └── frontend/            # 前端应用模板
│           ├── deployment.yaml  # Deployment 模板
│           ├── service.yaml     # Service 模板
│           ├── hpa.yaml         # HPA 模板
│           └── ingress.yaml     # IngressRoute 模板
└── README.md                    # 项目文档
```

## 📋 工作流列表

| 分类 | 工作流 | 文件路径 | 说明 |
|------|--------|----------|------|
| **共享** | Code Quality | `code-quality.yml` | 代码格式检查、静态分析、单元测试 |
| **共享** | Cleanup | `cleanup-artifacts.yml` | 清理 Actions artifacts 和 GHCR 旧镜像 |
| **后端** | Build Binary | `backend-build-binary.yml` | 编译 Go 二进制文件（支持 UPX 压缩） |
| **后端** | Build Docker | `backend-build-docker.yml` | 构建并推送后端 Docker 镜像（含 Summary 报告） |
| **后端** | Deploy K8s | `backend-deploy-k8s.yml` | 部署后端服务到 Kubernetes |
| **前端** | Build Frontend | `frontend-build-frontend.yml` | 前端构建（支持 npm/pnpm/yarn） |
| **前端** | Build Docker | `frontend-build-docker.yml` | 构建并推送前端 Docker 镜像（含 Summary 报告） |
| **前端** | Deploy K8s | `frontend-deploy-k8s.yml` | 部署前端应用到 Kubernetes |
| **通知** | Release Note | `release-note.yml` | tag 发布模式构建成功后自动生成 GitHub Release 发布单 |
| **通知** | PR Opened Notify | `pr-opened-notify.yml` | PR 开启时推送变更摘要到飞书/TG |
| **通知** | PR Merged Notify | `pr-merged-notify.yml` | PR 合并后推送通知到飞书/TG |

> GitHub reusable workflow 的 `uses:` 目标必须直接位于 `.github/workflows/` 顶层，不能放在 `backend/`、`frontend/`、`shared/` 等二级目录。

---

## 🔐 Secrets 和 Variables 配置

### 配置优先级

GitHub Actions 支持**环境级别**和**仓库级别**两种配置方式，优先级为：

```bash
环境级别 (Environment) > 仓库级别 (Repository)
```

当工作流引用 `environment` 时，会优先读取该环境下的 secrets/variables；如果找不到，则回退到仓库级别的配置。

### 🏷️ 所需 Secrets

#### 仓库级别 Secrets（全局共享）

| Secret | 说明 | 用途 | 必填 |
|--------|------|------|------|
| `DOCKER_USERNAME` | Docker/GitHub 用户名 | 镜像推送认证 | ✅ |
| `DOCKER_PASSWORD` | Docker 密码或 GitHub Token | 镜像推送认证 | ✅ |
| `APP_ID` | GitHub App ID | App Token 方式拉取私有 Go 模块 | ❌ |
| `APP_PRIVATE_KEY` | GitHub App 私钥 | App Token 方式拉取私有 Go 模块 | ❌ |
| `GIT_SSH_PRIVATE_KEY` | Git SSH 私钥 (Legacy) | SSH 方式拉取私有 Go 模块 | ❌ |

> **认证方式选择**：推荐使用 `APP_ID` + `APP_PRIVATE_KEY`（GitHub App Token），它会自动生成临时 Token，安全性更高。`GIT_SSH_PRIVATE_KEY` 仍可使用但属于旧方式。

#### 环境级别 Secrets（按环境配置）

| Secret | 说明 | 用途 | 适用环境 |
|--------|------|------|----------|
| `KUBECONFIG` | Kubernetes 集群访问配置 | K8s 部署 | dev/test/prod |
| `FEISHU_WEBHOOK_URL` | 飞书机器人 Webhook | 部署通知 | dev/test/prod |
| `WECOM_WEBHOOK_URL` | 企业微信群机器人 Webhook（完整地址） | 部署通知 | dev/test/prod |
| `WECOM_WEBHOOK_KEY` | 企业微信群机器人 key（与 URL 二选一） | 部署通知 | dev/test/prod |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | 部署通知 | dev/test/prod |
| `TELEGRAM_CHAT_ID` | Telegram 聊天群组 ID | 部署通知 | dev/test/prod |

### 📦 所需 Variables (GitHub Variables)

#### 仓库级别 Variables

| Variable | 说明 | 默认值 | 必填 |
|----------|------|--------|------|
| `GO_VERSION` | 默认 Go 版本 | `1.25.1` | ❌ |
| `GOPROXY` | Go 模块代理地址 | `https://goproxy.cn\|direct` | ❌ |
| `GOPRIVATE` | Go 私有模块路径 | `''` | ❌ |
| `GO_AUTH_METHOD` | Go 私有模块认证方式 | `none` | ❌ |
| `NODE_VERSION` | 默认 Node.js 版本 | `20` | ❌ |
| `PACKAGE_REGISTRY` | 默认包管理器仓库 | `https://registry.npmmirror.com` | ❌ |

#### 环境级别 Variables

| Variable | 说明 | 示例值 | 适用环境 |
|----------|------|--------|----------|
| `IMAGE_REGISTRY` | 镜像仓库地址 | `ghcr.io` | dev/test/prod |
| `IMAGE_PREFIX` | 镜像前缀（组织名） | `ghcr.io/your-org` | dev/test/prod |
| `KIND` | Kubernetes workload 类型 | `Deployment` / `StatefulSet` / `DaemonSet` | dev/test/prod |
| `SERVICE_KIND` | Kubernetes Service 类型 | `Service` | dev/test/prod |
| `SERVICE_TYPE` | Kubernetes Service 暴露类型 | `ClusterIP` / `NodePort` / `LoadBalancer` | dev/test/prod |
| `WS_PORT` | WebSocket 端口（可选） | `9090` | dev/test/prod |
| `DEPLOYMENT_REPLICAS` | 默认副本数 | `3` | dev/test/prod |
| `HPA_ENABLED` | 是否启用 HPA | `true` | dev/test/prod |

### 📝 配置示例

#### 仓库级别配置路径

```
Settings > Secrets and variables > Actions > Repository secrets
Settings > Secrets and variables > Actions > Repository variables
```

#### 环境级别配置路径

```
Settings > Environments > [环境名称] > Environment secrets
Settings > Environments > [环境名称] > Environment variables
```

#### 环境配置示例

| 环境名称 | KUBECONFIG | FEISHU_WEBHOOK_URL | WECOM_WEBHOOK_URL | DEPLOYMENT_REPLICAS |
|----------|------------|---------------------|-------------------|---------------------|
| `dev` | dev-cluster-config | <https://open.feishu.cn/>... | <https://qyapi.weixin.qq.com/>... | `2` |
| `test` | test-cluster-config | <https://open.feishu.cn/>... | <https://qyapi.weixin.qq.com/>... | `3` |
| `prod` | prod-cluster-config | <https://open.feishu.cn/>... | <https://qyapi.weixin.qq.com/>... | `5` |

---

## 🔑 GitHub App 创建与配置

`APP_ID` + `APP_PRIVATE_KEY` 这两个 Secret 不是随手填的字符串，它们来自一个你自己注册的
GitHub App。本节把「从零注册到工作流跑通」这条路写完整，官方文档见
[注册 GitHub App](https://docs.github.com/zh/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)。

### 为什么用 App 而不是 PAT

| 维度 | GitHub App | 个人访问令牌（PAT） |
|------|------------|---------------------|
| 令牌有效期 | 每次运行现签，1 小时自动失效 | 手动设定，常年有效甚至永不过期 |
| 授权范围 | 按仓库逐个安装，按权限项勾选 | 跟着人走，默认能碰到这个人所有仓库 |
| 人员流动 | 与个人账号无关，离职不影响 | 发证人离职 / 改密码即全线崩 |
| 审计 | 操作记录显示为 App 身份，可追溯 | 只能看到个人账号，分不清是人还是流水线 |
| 成员席位 | 不占用组织席位 | 机器人账号要占席位（私有仓库要付费）|

工作流里用的是 [`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)：
它拿 App ID 与私钥换一个**短时效的安装令牌**，用完即弃。

### 这个仓库用 App 做什么

| 用途 | 哪个工作流 | 需要的权限 |
|------|-----------|-----------|
| 拉取私有 Go 模块（`go mod download`）| `code-quality.yml`、`backend-build-binary.yml` | Contents: **Read-only** |
| 下游仓库升级 proto 引用并推回（`update-proto-mod`）| 业务仓库调用该 Action | Contents: **Read and write** |
| 机器人评论 PR（pb 同步报告、变更摘要）| `pr-opened-notify.yml` 及业务仓库的生成流程 | Pull requests: **Read and write** |

只做前一件事时，权限给到 `Contents: Read-only` 就够了。
把三件事放在同一个 App 上没问题 —— 权限是按安装仓库生效的，不会让只读场景拿到写权限。

### 令牌是怎么换出来的

弄清这条链路，才知道为什么「App 建好了但还是 403」——问题几乎总出在第 3 步的安装关系上。

```mermaid
sequenceDiagram
    autonumber
    participant WF as 业务仓库 Workflow
    participant ACT as actions/create-github-app-token
    participant GH as GitHub API
    participant REPO as 私有 proto 仓库
    participant GO as go mod download

    WF->>ACT: 传入 APP_ID + APP_PRIVATE_KEY + owner
    ACT->>GH: 用私钥签 JWT，查该 App 在 owner 下的安装记录
    GH-->>ACT: 返回 installation_id
    Note over GH,ACT: 这一步要求 App 已安装到该组织<br/>没装则查不到安装记录，直接失败
    ACT->>GH: 用 installation_id 申请安装令牌
    GH-->>ACT: 返回短时效令牌，1 小时过期
    ACT-->>WF: 输出 steps.app_token.outputs.token
    WF->>WF: git config url.insteadOf 注入令牌
    WF->>GO: go mod download
    GO->>REPO: https 拉取私有模块
    REPO-->>GO: 返回模块内容
    Note over REPO,GO: 令牌的权限是 App 在「这个仓库」上的权限<br/>App 没装到 proto 仓库时这里 404，而不是 401
```

### 注册步骤

1. **进入注册页**
   - 组织用（推荐）：`https://github.com/organizations/<你的组织>/settings/apps/new`
   - 个人用：Settings → Developer settings → GitHub Apps → New GitHub App

2. **填基本信息**

   | 字段 | 填什么 | 说明 |
   |------|--------|------|
   | GitHub App name | 如 `<org>-ops-cli` | 全局唯一，重名会被拒 |
   | Homepage URL | 组织或仓库地址即可 | 必填但不影响功能 |
   | Webhook → Active | **取消勾选** | 我们只用它换令牌，不接收事件；不取消会一直给你推送失败告警 |

3. **勾权限**（Permissions → Repository permissions）

   | 权限项 | 给什么 | 为什么 |
   |--------|--------|--------|
   | Contents | Read-only（需推送时 Read and write）| 拉私有模块 / 推送 go.mod 变更 |
   | Pull requests | Read and write（只拉模块可不给）| 机器人评论 PR |
   | Metadata | Read-only（勾了上面会自动带上）| GitHub 强制要求 |

   其余一律不动。多给的权限不会让流程更顺，只会扩大泄露后的影响面。

4. **选安装范围**：Where can this GitHub App be installed → **Only on this account**

5. **创建并记下 App ID**：创建后页面顶部的 `App ID`（纯数字）就是 `APP_ID`

6. **生成私钥**：同一页 → Private keys → Generate a private key，浏览器会下载一个
   `.pem` 文件。**这个文件只能下载这一次**，关掉页面就拿不回来了（可以再生成新的）

7. **安装到仓库**：左侧 Install App → 选择账号/组织 → 选 `Only select repositories`，
   勾上需要被访问的仓库（比如 proto 仓库 + 所有要拉它的业务仓库）

   > 漏装是最常见的失败原因：App 创建好了但没装到目标仓库，换令牌会成功，
   > 但用它 `git clone` 私有仓库仍然 404。

8. **写进 Secrets**（仓库 Settings → Secrets and variables → Actions）

   | Secret | 值 |
   |--------|----|
   | `APP_ID` | 第 5 步那串数字 |
   | `APP_PRIVATE_KEY` | `.pem` 文件的**全部内容**，含 `-----BEGIN RSA PRIVATE KEY-----` 和 `-----END` 两行 |

   粘贴私钥时保持原始换行，不要手动删空行或加缩进 —— 粘成一行会报
   `error:0909006C:PEM routines:get_name:no start line`。

   组织多仓库复用时，用 **Organization secrets** 配一次，再按仓库授权，比每个仓库各存一份好维护。

9. **在工作流里启用**：把认证方式切到 `app-token` 并把两个 Secret 透传给可复用工作流

   ```yaml
   jobs:
     code-quality:
       uses: kamalyes/deploy-pipeline/.github/workflows/code-quality.yml@master
       with:
         goprivate: github.com/<你的组织>/*      # 不填则不会触发私有模块认证
         go-auth-method: app-token
       secrets:
         APP_ID: ${{ secrets.APP_ID }}
         APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
   ```

   也可以把 `go-auth-method` 配成仓库 Variable `GO_AUTH_METHOD=app-token`，省掉每个
   工作流都写一遍。

### 常见错误对照

| 报错 / 现象 | 原因 | 处理 |
|------------|------|------|
| `Input required and not supplied: app-id` | Secret 没配，或可复用工作流没透传 `secrets:` | 补 `secrets:` 段，或改用 `secrets: inherit` |
| `PEM routines:get_name:no start line` | 私钥粘成了一行 / 丢了 BEGIN-END 行 | 重新粘贴完整 `.pem` 内容 |
| 换令牌成功但 `go mod download` 仍 403/404 | App 没安装到被依赖的那个私有仓库 | Install App 里补勾该仓库 |
| `GOPRIVATE` 没生效，走了公网代理 | `goprivate` 入参为空 | 填 `github.com/<org>/*` 或配 `vars.GOPRIVATE` |
| 推送被拒 `403 Resource not accessible by integration` | Contents 只给了 Read-only | 改成 Read and write 并重新安装 |
| App 一直收到 webhook 投递失败邮件 | 注册时没取消 Webhook → Active | 回 App 设置取消勾选 |

---

## 🚀 快速接入

### 后端 Go 服务示例

在项目 `.github/workflows/pipeline.yml` 中引用：

```yaml
name: Backend CI/CD

on:
  push:
    branches: [main]

jobs:
  code-quality:
    uses: kamalyes/deploy-pipeline/.github/workflows/code-quality.yml@master
    with:
      go-version: ${{ vars.GO_VERSION || '1.25.1' }}
      gotestsum-version: 'v1.13.0'
      goprivate: ${{ vars.GOPRIVATE || '' }}
      go-auth-method: ${{ vars.GO_AUTH_METHOD || 'app-token' }}
    secrets:
      APP_ID: ${{ secrets.APP_ID }}
      APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
      GIT_SSH_PRIVATE_KEY: ${{ secrets.GIT_SSH_PRIVATE_KEY }}

  build-binary:
    uses: kamalyes/deploy-pipeline/.github/workflows/backend-build-binary.yml@master
    with:
      go-version: ${{ vars.GO_VERSION || '1.25.1' }}
      binary-name: 'your-service'
      binary-source-dir: 'deployments'
      binary-output: 'deployments/your-service'
      version: '${{ github.sha }}'
      build-time: '${{ format('{0}_{1}', github.event.head_commit.timestamp, github.run_id) }}'
      git-commit: '${{ github.sha }}'
      goprivate: ${{ vars.GOPRIVATE || '' }}
      go-auth-method: ${{ vars.GO_AUTH_METHOD || 'app-token' }}
    secrets:
      APP_ID: ${{ secrets.APP_ID }}
      APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
      GIT_SSH_PRIVATE_KEY: ${{ secrets.GIT_SSH_PRIVATE_KEY }}

  build-docker:
    needs: [build-binary]
    uses: kamalyes/deploy-pipeline/.github/workflows/backend-build-docker.yml@master
    with:
      binary-name: 'your-service'
      version: '${{ github.sha }}'
      http-port: '8080'
      rpc-port: '9090'
      ws-port: '9091'        # WebSocket 端口（可选）
      pprof-port: '6060'
      docker-registry: ${{ vars.IMAGE_REGISTRY || 'ghcr.io' }}
      binary-source-dir: 'deployments'
      image-base: '${{ vars.IMAGE_PREFIX }}/your-service'
      image-name: '${{ vars.IMAGE_PREFIX }}/your-service:${{ github.sha }}'
    secrets:
      DOCKER_USERNAME: ${{ secrets.DOCKER_USERNAME }}
      DOCKER_PASSWORD: ${{ secrets.DOCKER_PASSWORD }}

  deploy-dev:
    needs: [build-docker]
    uses: kamalyes/deploy-pipeline/.github/workflows/backend-deploy-k8s.yml@master
    with:
      environment: 'dev'
      image-name: '${{ vars.IMAGE_PREFIX }}/your-service:${{ github.sha }}'
      binary-source-dir: 'deployments'
      binary-name: 'your-service'
      http-port: '8080'
      rpc-port: '9090'
      ws-port: '9091'        # WebSocket 端口（可选）
      pprof-port: '6060'
      notification-provider: 'feishu'
    environment: dev
    secrets:
      KUBECONFIG: ${{ secrets.KUBECONFIG }}
      FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
      WECOM_WEBHOOK_URL: ${{ secrets.WECOM_WEBHOOK_URL }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

### 前端应用示例

```yaml
name: Frontend CI/CD

on:
  push:
    branches: [main]

jobs:
  build-frontend:
    uses: kamalyes/deploy-pipeline/.github/workflows/frontend-build-frontend.yml@master
    with:
      node-version: ${{ vars.NODE_VERSION || '20' }}
      package-manager: 'pnpm'
      package-registry: ${{ vars.PACKAGE_REGISTRY || 'https://registry.npmmirror.com' }}
      version: '${{ github.sha }}'
      build-time: '${{ format('{0}_{1}', github.event.head_commit.timestamp, github.run_id) }}'
      git-commit: '${{ github.sha }}'
      output-dir: 'dist'

  build-docker:
    needs: [build-frontend]
    uses: kamalyes/deploy-pipeline/.github/workflows/frontend-build-docker.yml@master
    with:
      app-name: 'frontend-app'
      version: '${{ github.sha }}'
      http-port: '80'
      docker-registry: ${{ vars.IMAGE_REGISTRY || 'ghcr.io' }}
      image-base: '${{ vars.IMAGE_PREFIX }}/frontend-app'
      image-name: '${{ vars.IMAGE_PREFIX }}/frontend-app:${{ github.sha }}'
      nginx-config-path: 'deploy/nginx.conf'
    secrets:
      DOCKER_USERNAME: ${{ secrets.DOCKER_USERNAME }}
      DOCKER_PASSWORD: ${{ secrets.DOCKER_PASSWORD }}

  deploy-dev:
    needs: [build-docker]
    uses: kamalyes/deploy-pipeline/.github/workflows/frontend-deploy-k8s.yml@master
    with:
      environment: 'dev'
      image-name: '${{ vars.IMAGE_PREFIX }}/frontend-app:${{ github.sha }}'
      app-name: 'frontend-app'
      http-port: '80'
      ingress-path-prefix: '/app'
      notification-provider: 'feishu'
    environment: dev
    secrets:
      KUBECONFIG: ${{ secrets.KUBECONFIG }}
      FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
      WECOM_WEBHOOK_URL: ${{ secrets.WECOM_WEBHOOK_URL }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

### 前端应用示例

```yaml
name: Frontend CI/CD

on:
  push:
    branches: [main]

jobs:
  build-frontend:
    uses: kamalyes/deploy-pipeline/.github/workflows/frontend-build-frontend.yml@master
    with:
      node-version: ${{ vars.NODE_VERSION || '20' }}
      package-manager: 'pnpm'
      package-registry: ${{ vars.PACKAGE_REGISTRY || 'https://registry.npmmirror.com' }}
      version: '${{ github.sha }}'
      build-time: '${{ format('{0}_{1}', github.event.head_commit.timestamp, github.run_id) }}'
      git-commit: '${{ github.sha }}'
      output-dir: 'dist'
    secrets: inherit

  build-docker:
    needs: [build-frontend]
    uses: kamalyes/deploy-pipeline/.github/workflows/frontend-build-docker.yml@master
    with:
      app-name: 'frontend-app'
      version: '${{ github.sha }}'
      http-port: '80'
      docker-registry: ${{ vars.IMAGE_REGISTRY || 'ghcr.io' }}
      image-base: '${{ vars.IMAGE_PREFIX }}/frontend-app'
      image-name: '${{ vars.IMAGE_PREFIX }}/frontend-app:${{ github.sha }}'
      nginx-config-path: 'deploy/nginx.conf'  # 可选：项目自定义 nginx 配置
    secrets: inherit

  deploy-dev:
    needs: [build-docker]
    uses: kamalyes/deploy-pipeline/.github/workflows/frontend-deploy-k8s.yml@master
    with:
      environment: 'dev'
      image-name: '${{ vars.IMAGE_PREFIX }}/frontend-app:${{ github.sha }}'
      app-name: 'frontend-app'
      http-port: '80'
      ingress-path-prefix: '/app'
      notification-provider: 'feishu'
    environment: dev
    secrets: inherit
```

### 前端 Monorepo 示例（pnpm + Turbo）

多应用 monorepo 项目，每个应用独立 CI/CD 工作流：

```yaml
name: 🚀 App A CI/CD

on:
  push:
    branches: [main]
    paths:
      - 'apps/app-a/**'
      - 'packages/**'
      - 'pnpm-lock.yaml'
  workflow_dispatch:
    inputs:
      environment:
        description: '部署环境'
        default: 'dev'
        type: choice
        options: [dev, test, uat, prod]

env:
  NODE_VERSION: '20'
  PNPM_VERSION: '9'
  IMAGE_PREFIX: 'ghcr.io/${{ github.repository }}'

jobs:
  build:
    uses: kamalyes/deploy-pipeline/.github/workflows/frontend-build-frontend.yml@master
    with:
      node-version: ${{ env.NODE_VERSION }}
      pnpm-version: ${{ env.PNPM_VERSION }}
      package-manager: 'pnpm'
      build-command: 'pnpm run build:app-a'
      output-dir: 'apps/app-a/dist'
      artifact-name: 'app-a-dist'
      version: '${{ github.sha }}'
      build-time: '${{ format("{0}_{1}", github.event.head_commit.timestamp, github.run_id) }}'
      git-commit: '${{ github.sha }}'

  docker:
    needs: [build]
    uses: kamalyes/deploy-pipeline/.github/workflows/frontend-build-docker.yml@master
    with:
      app-name: 'my-project-app-a'
      version: '${{ github.sha }}'
      docker-registry: 'ghcr.io'
      image-base: '${{ env.IMAGE_PREFIX }}/my-project-app-a'
      image-name: '${{ env.IMAGE_PREFIX }}/my-project-app-a:${{ github.sha }}'
      build-output-dir: 'apps/app-a/dist'
      artifact-name: 'app-a-dist'
      nginx-config-path: 'deploy/nginx.conf'
    secrets:
      DOCKER_USERNAME: ${{ secrets.DOCKER_USERNAME }}
      DOCKER_PASSWORD: ${{ secrets.DOCKER_PASSWORD }}

  deploy:
    needs: [docker]
    uses: kamalyes/deploy-pipeline/.github/workflows/frontend-deploy-k8s.yml@master
    with:
      environment: ${{ inputs.environment || 'dev' }}
      image-name: '${{ env.IMAGE_PREFIX }}/my-project-app-a:${{ github.sha }}'
      app-name: 'my-project-app-a'
      http-port: '80'
      ingress-path-prefix: '/app-a'
      notification-provider: 'feishu'
    environment: ${{ inputs.environment || 'dev' }}
    secrets:
      KUBECONFIG: ${{ secrets.KUBECONFIG }}
      FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
      WECOM_WEBHOOK_URL: ${{ secrets.WECOM_WEBHOOK_URL }}
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

---

## 🏗️ 多架构构建（amd64 / arm64）

K8s 集群同时含 amd64 与 arm64 节点时，镜像 manifest 必须标记正确架构，否则调度到 arm64 节点会拉取失败（`no matching manifest for linux/arm64/v8`）。

本流水线采用**单架构参数化构建**——每次调用只产出一个架构镜像，由调用方按目标集群节点架构选择：

| 工作流 | 参数 | 默认值 | 取值 |
|--------|------|--------|------|
| `backend-build-binary` | `arch` | `amd64` | `amd64` / `arm64`（Go 交叉编译，无需 QEMU） |
| `backend-build-docker` | `os` / `arch` | `linux` / `amd64` | 需与 build-binary 一致；build-push-action 用 `${os}/${arch}` 标记 manifest |
| `frontend-build-docker` | `os` / `arch` | `linux` / `amd64` | `${os}/${arch}` 传给 platforms；前端纯静态文件，nginx:alpine 基础镜像本身支持多架构 |

### 调用示例：构建 arm64 镜像

```yaml
jobs:
  build-arm64:
    uses: kamalyes/deploy-pipeline/.github/workflows/backend-build-binary.yml@master
    with:
      binary-name: your-service
      arch: arm64         # 👈 关键：Go 交叉编译 linux/arm64 二进制
      # ...其他参数
  docker-arm64:
    needs: build-arm64
    uses: kamalyes/deploy-pipeline/.github/workflows/backend-build-docker.yml@master
    with:
      binary-name: your-service
      arch: arm64         # 👈 关键：与 build-binary 一致，manifest 标记为 linux/arm64
      # ...其他参数
```

> **注意**：`arch` 必须前后一致。binary workflow 用 arm64 交叉编译，docker workflow 也必须传 arm64，否则 manifest 架构标记与二进制实际架构不匹配，运行时会崩溃。

---

## 📣 通知与发布单

### 发布单（Release Note）

**无需单独接入**：调用方 pipeline 开启 tag 发布模式（push `v*` / `prod-*` tag → 仅构建推送镜像、跳过 K8s 部署），构建成功后 `release-note` job 自动运行：

- 自动定位上一 tag，收集区间内**全部 PR（含标题/作者/链接）与直接提交**
- 幂等创建/更新 GitHub Release 发布单（版本、镜像、变更统计、发布人、时间）
- 同步写入 Actions Summary，无需翻日志
- **零 secrets 依赖**（仅用 `GITHUB_TOKEN`），任何仓库接入即用

**调用方需要**：

| 项 | 说明 |
|---|---|
| 触发 | `on.push.tags: ['v*', 'prod-*']`（参考 ops-gateway 的 `pipeline.yml` 发布模式决策块） |
| 权限 | workflow 级声明 `permissions: contents: write`（创建 Release）+ `pull-requests: read`（收集 PR 明细） |
| 镜像 tag | 发布模式下镜像 tag = git tag 名（如 `v0.0.1`），且默认被镜像清理豁免 |

### PR 通知

PR 开启推送**变更文件摘要**、PR 合并推送**合并通知**，渠道由 `provider` 入参选择（默认 `feishu`）：

```yaml
# .github/workflows/pr-opened-notify.yml（PR 开启摘要）
on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  notify:
    if: github.event_name == 'pull_request'
    uses: kamalyes/deploy-pipeline/.github/workflows/pr-opened-notify.yml@master
    with:
      provider: wecom   # 可选，默认 feishu；feishu | wecom | telegram | all
    secrets: inherit    # 需对应渠道的 webhook secret
```

```yaml
# .github/workflows/pr-merged-notify.yml（PR 合并通知）
on:
  pull_request:
    types: [closed]

jobs:
  notify:
    if: github.event.pull_request.merged == true
    uses: kamalyes/deploy-pipeline/.github/workflows/pr-merged-notify.yml@master
    with:
      provider: wecom   # 可选，默认 feishu
    secrets: inherit
```

> 所选渠道的 webhook 未配置时静默跳过，不会把 job 判红。

### 通用通知 Action

任意 workflow 中发送自定义通知（标题 + 多行正文）：

```yaml
- name: Notify
  uses: kamalyes/deploy-pipeline/.github/actions/notify@master
  with:
    provider: all        # feishu | wecom | telegram | all | none（all 只发已配置的渠道）
    status: success      # success | failure | warning | info
    title: '📋 自定义标题'
    message: |
      **变更内容**
      - xxx
  env:
    FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
    WECOM_WEBHOOK_URL: ${{ secrets.WECOM_WEBHOOK_URL }}
    TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
    TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

### 企业微信群机器人

群设置 → 群机器人 → 添加，复制 Webhook 地址存为 secret `WECOM_WEBHOOK_URL`
（只想存 key 时用 `WECOM_WEBHOOK_KEY`，脚本会自行拼 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=`；两者都给时 URL 优先）。

```yaml
# 部署通知
with:
  notification-provider: 'wecom'
secrets:
  WECOM_WEBHOOK_URL: ${{ secrets.WECOM_WEBHOOK_URL }}
```

消息用群机器人的 `markdown` 类型。平台侧的硬限制与本脚本的处理：

| 限制 | 说明 | 脚本处理 |
|------|------|----------|
| 正文 ≤ 4096 字节 | 超限整条被拒收 | 按 UTF-8 字节裁剪并标注"内容过长已截断"，不会截出非法字符 |
| 频率 20 条/分钟 | 超限返回 `errcode: 45009` | 每次通知 1 条消息（配了手机号 @ 时 2 条） |
| 字体色只有 3 种 | `info`(绿) / `comment`(灰) / `warning`(橙红) | 从飞书色值映射：green→info、red/yellow→warning、其他→comment |
| `markdown` 不支持 @all、手机号 @ | 仅 `text` 类型支持 | 见下 |

**@ 成员**（可选，两个变量可同时用）：

```yaml
env:
  WECOM_MENTIONED_LIST: 'zhangsan,lisi'          # userid，内联 <@userid>，不额外占配额
  WECOM_MENTIONED_MOBILE_LIST: '@all'            # 手机号或 @all，需 text 类型，额外补发 1 条
```

`WECOM_MENTIONED_MOBILE_LIST` 补发的那条 text 只含标题；它失败不影响主通知的成败判定。

> 没选企业微信的 `markdown_v2`：v2 支持表格和代码块，但不支持字体色，也不支持 `<@userid>` 扩展语法，拿不到与飞书卡片一致的状态配色。

### proto 模块引用升级（update-proto-mod）

proto 仓库 master 更新后，业务仓库一键升级 `go.mod` 中 proto 模块引用并自动提 PR：

```yaml
- name: Update proto mod
  uses: kamalyes/deploy-pipeline/.github/actions/update-proto-mod@master
  with:
    token: ${{ secrets.GH_PAT }}
    proto-repo: 'your-org/demo-share-proto'
    proto-module: 'github.com/your-org/demo-share-proto'
```

---

## 📖 工作流详细说明


**跨仓库升级时序**：

```mermaid
sequenceDiagram
    autonumber
    participant OPS as 维护者
    participant SVC as 业务仓库 Workflow
    participant ACT as update-proto-mod Action
    participant SH as update-proto-mod.sh
    participant PROTO as proto 仓库
    participant GO as Go 模块代理

    OPS->>SVC: workflow_dispatch，填模块名与版本
    SVC->>ACT: 传 PROTO_READ_TOKEN 与 PUSH_TOKEN
    ACT->>SH: 执行脚本，参数为分支 / 模块 / 版本
    SH->>SH: git config url.insteadOf 注入 PROTO_READ_TOKEN
    SH->>SH: export GOPRIVATE=github.com/ORG/*
    SH->>GO: go get github.com/ORG/MODULE@REF
    GO->>PROTO: 拉取指定版本
    PROTO-->>GO: 返回模块
    GO-->>SH: 更新 go.mod 与 go.sum
    SH->>SH: go mod tidy
    alt go.mod 有变化
        SH->>SVC: 用 PUSH_TOKEN 提交并推送到目标分支
        SVC-->>OPS: 通知升级完成
    else 无变化
        SH-->>OPS: 直接退出，不产生空提交
    end
```

> 两个令牌职责不同：`PROTO_READ_TOKEN` 只需要对 proto 仓库有读权限，
> `PUSH_TOKEN` 需要对**当前仓库**有写权限。混用一个全权令牌能跑通，
> 但一旦泄露影响面要大得多。


### 共享工作流

#### Code Quality

代码质量检查：gofmt + go vet + 单元测试 + 覆盖率报告

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `go-version` | string | 否 | `1.25.1` | Go 版本 |
| `gotestsum-version` | string | 否 | `v1.13.0` | gotestsum 版本 |
| `goproxy` | string | 否 | `https://goproxy.cn\|direct` | Go 模块代理 |
| `goprivate` | string | 否 | `''` | 私有模块路径 |
| `go-auth-method` | string | 否 | `none` | 私有模块认证方式（同 Build Binary） |
| `retention-days` | string | 否 | `7` | 构建产物保留天数 |

**所需 Secrets（当 `goprivate` 非空时）：**

| Secret | 认证方式 | 说明 |
|--------|----------|------|
| `APP_ID` | `app-token` | GitHub App ID |
| `APP_PRIVATE_KEY` | `app-token` | GitHub App 私钥 |
| `GIT_SSH_PRIVATE_KEY` | `ssh` | SSH 私钥（Legacy） |

#### Cleanup

清理 Actions artifacts 和 GHCR 旧版本镜像，支持独立开关和按保留天数/版本数清理

**Artifacts 清理参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `cleanup-artifacts` | boolean | 否 | `true` | 是否清理 Actions artifacts |
| `keep-days` | string | 否 | `7` | 保留最近 N 天的 artifacts |

**GHCR 镜像清理参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `cleanup-images` | boolean | 否 | `false` | 是否清理 GHCR 镜像 |
| `project-owner` | string | 否 | `''` | GitHub 组织/用户名（cleanup-images 为 true 时必填） |
| `image-name` | string | 否 | `''` | 镜像名（逗号分隔多个，留空则自动扫描所有 container packages） |
| `keep-count` | string | 否 | `5` | 保留镜像版本数 |
| `tag-include-prefix` | string | 否 | `''` | 仅清理 tag 命中前缀的镜像版本（逗号分隔，如 `dev,uat,sit`）；每个前缀分组各自保留最新 `keep-count` 个；留空回退 `vars.CLEANUP_TAG_INCLUDE_PREFIX` |
| `tag-exclude-prefix` | string | 否 | `v,prod` | 永不清理 tag 命中前缀的镜像版本（逗号分隔），命中的版本也不占用保留名额；留空回退 `vars.CLEANUP_TAG_EXCLUDE_PREFIX`，仍未配置时默认豁免发布 tag 前缀 `v,prod`（发布镜像永不清理，保障可回滚） |

**调用示例：**

```yaml
# 只清理 artifacts
cleanup:
  uses: kamalyes/deploy-pipeline/.github/workflows/cleanup-artifacts.yml@master
  with:
    cleanup-artifacts: true
    keep-days: '7'

# 同时清理 artifacts + 指定镜像
cleanup:
  uses: kamalyes/deploy-pipeline/.github/workflows/cleanup-artifacts.yml@master
  with:
    cleanup-artifacts: true
    keep-days: '7'
    cleanup-images: true
    project-owner: 'your-org'
    image-name: 'your-service'
    keep-count: '5'

# 扫描清理所有镜像（image-name 留空）
cleanup:
  uses: kamalyes/deploy-pipeline/.github/workflows/cleanup-artifacts.yml@master
  with:
    cleanup-images: true
    project-owner: 'your-org'
    image-name: ''
    keep-count: '5'

# 清理多个指定镜像（逗号分隔）
cleanup:
  uses: kamalyes/deploy-pipeline/.github/workflows/cleanup-artifacts.yml@master
  with:
    cleanup-images: true
    project-owner: 'your-org'
    image-name: 'tenant-admin,ops-admin'
    keep-count: '5'
```

**Tag 前缀过滤说明：**

- 镜像 tag 由调用方在构建时拼接（如 `${branch}-${commit}-${timestamp}`），前缀即分支/环境名（dev/uat/sit…）
- `tag-include-prefix`（白名单）：仅清理命中前缀的版本，每个前缀各自保留最新 `keep-count` 个，避免高频环境把低频环境的保留名额挤掉；未命中任何前缀的版本（如 prod/master）不清理
- `tag-exclude-prefix`（黑名单）：命中前缀的版本永不清理；两者可同时配置，exclude 优先保护；**未配置时默认豁免发布 tag 前缀 `v,prod`，发布镜像（tag 发布模式产物）永不参与清理**
- untagged（dangling）版本不受前缀过滤影响，始终清理
- 配置优先级：`inputs > vars.CLEANUP_TAG_INCLUDE_PREFIX` / `vars.CLEANUP_TAG_EXCLUDE_PREFIX` > 默认值 `v,prod`（仅 exclude；仓库级 GitHub Variables 设置后无需修改 pipeline）

```yaml
# 只清理 dev/uat/sit 前缀的旧镜像，其他前缀（prod/master 等）不动
cleanup:
  uses: kamalyes/deploy-pipeline/.github/workflows/cleanup-artifacts.yml@master
  with:
    cleanup-images: true
    project-owner: 'your-org'
    keep-count: '5'
    tag-include-prefix: 'dev,uat,sit'

# 反向：清理所有旧镜像，但 prod/master 前缀的版本永不清理
cleanup:
  uses: kamalyes/deploy-pipeline/.github/workflows/cleanup-artifacts.yml@master
  with:
    cleanup-images: true
    project-owner: 'your-org'
    keep-count: '5'
    tag-exclude-prefix: 'prod,master'
```

### 分支管理工作流

周节奏 SOP 的分支工程化（详见复盘文档《CI-CD-Review》）：周三上线发布后 dev 回收重切（git 树每周归零）+ 新迭代从最新 master 切 feature。

#### Branch Recycle（dev 回收重切 + 已合并 feature 清理）

**动作序列**（每仓库）：

1. 前置检查（可选开启）：今日存在 `prod-YYYY.MM.DD*` tag（确认发布已完成，未发布自动跳过）
2. dev 状态检查：`compare master...dev` 为 `identical/behind`（无未发布内容）才回收；`ahead/diverged` 自动**拒绝**并列出领先提交数（其他项目未发布变更，人工决策）
3. 删 dev → 从 master 重切（`dev` 不存在则直接新建，首次接入场景）
4. feature 清理：删除已完全合入 master 的 `feature/*` 分支，未合并的保留并列出

**安全设计**：

| 机制 | 说明 |
|---|---|
| 干跑预告 | 周三 21:00（北京时间）schedule 自动**干跑**，推预告卡片到群；真执行必须人工 `workflow_dispatch` 以 `dry-run=false` 确认——删分支永不无人值守 |
| 拒绝即停 | dev 领先 master（有未发布内容）自动拒绝该仓库，不误删其他项目变更 |
| 发布完成检查 | `require-today-tag=true` 时，今日无 `prod-*` tag 的仓库跳过（晚发布自动顺延到下次） |
| 幂等 | `dev` 缺失自动新建；重复触发安全 |

**触发方式**：

| 方式 | 场景 | dry-run |
|---|---|---|
| `schedule`（周三 21:00） | 自动干跑推预告卡片到群 | 恒为 true |
| `workflow_dispatch` | 人工确认执行（`dry-run=false`）或手动干跑 | 按 input |
| `workflow_call` | 单仓库自助模式（调用方 GITHUB_TOKEN 操作自己分支） | 按 input |

**参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `repos` | string | 否 | `''` | 仓库列表（逗号分隔 owner/repo；留空回退 `vars.BRANCH_OPS_REPOS`，再回退当前仓库） |
| `source-branch` | string | 否 | `master` | 源分支（重切基点） |
| `target-branch` | string | 否 | `dev` | 回收目标分支 |
| `feature-prefix` | string | 否 | `feature/` | 要清理的 feature 分支前缀 |
| `clean-features` | boolean | 否 | `true` | 是否清理已合并 feature 分支 |
| `require-today-tag` | boolean | 否 | call: `false` / dispatch: `true` | 是否要求今日存在 `prod-YYYY.MM.DD*` tag 才回收 |
| `dry-run` | boolean | 否 | call: `true` / dispatch: `false` | 干跑模式（schedule 恒为干跑） |
| `notification-provider` | string | 否 | `feishu` | 通知渠道：feishu / telegram / all / none |

**配置要求**（中心批量模式，在 deploy-pipeline 仓库配置一次）：

| 项 | 级别 | 说明 |
|---|---|---|
| `BRANCH_OPS_REPOS` | Variables | 仓库列表（逗号分隔），schedule/dispatch 留空时使用 |s
| `BRANCH_OPS_TOKEN` | Secrets | 跨仓库操作 token（PAT 或 GitHub App token，需覆盖全部目标仓库） |
| `FEISHU_WEBHOOK_URL` / `TELEGRAM_*` | Secrets | 预告/结果通知 |

**调用示例**：

```yaml
# 单仓库自助模式（在业务仓库 pipeline.yml 中，用自己 GITHUB_TOKEN）
branch-recycle:
  uses: kamalyes/deploy-pipeline/.github/workflows/branch-recycle.yml@master
  with:
    clean-features: true
    dry-run: false
    notification-provider: 'feishu'
  secrets: inherit
```

```yaml
# 中心批量模式（在 deploy-pipeline 仓库手动 dispatch，或 schedule 自动预告）
# inputs.repos: core-service,access-control-service,ops-gateway,...（留空用 vars.BRANCH_OPS_REPOS）
# dry-run=false 执行；require-today-tag=true 检查发布完成
```

#### Branch Cut（批量切分支）

新迭代启动（周三发布后定下版本内容）：从最新 master 给一批仓库统一切出 `feature/xx`。

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `repos` | string | 否 | `''` | 仓库列表（逗号分隔；留空回退 `vars.BRANCH_OPS_REPOS`） |
| `new-branch` | string | 是 | - | 新分支名（如 `feature/xxx`） |
| `base-branch` | string | 否 | `master` | 基分支 |
| `dry-run` | boolean | 否 | `false` | 干跑模式 |
| `notification-provider` | string | 否 | `feishu` | 通知渠道 |

幂等：目标分支已存在的仓库自动跳过。切完自动推结果卡片，替代群里人肉喊"大家切分支了"。

```yaml
# 在 deploy-pipeline 仓库手动 dispatch：new-branch=feature/xxx，repos 留空用 vars
```

#### branch-ops.sh（脚本直用）

两个 workflow 共用 `scripts/branch-ops.sh`：

```bash
export GH_TOKEN="<PAT>"                      # 跨仓库用 PAT；单仓库用 GITHUB_TOKEN
export BRANCH_OPS_REPOS="org/repo-a,org/repo-b"
export BRANCH_OPS_DRY_RUN=true               # 干跑

bash scripts/branch-ops.sh recycle            # 回收模式
# 附加 env: BRANCH_OPS_SOURCE / BRANCH_OPS_TARGET / BRANCH_OPS_FEATURE_PREFIX /
#          BRANCH_OPS_CLEAN_FEATURES / BRANCH_OPS_REQUIRE_TAG

bash scripts/branch-ops.sh cut                # 切分支模式
# 附加 env: BRANCH_OPS_NEW_BRANCH=feature/xxx / BRANCH_OPS_BASE=master
```

### 后端工作流

#### Build Binary

编译 Go 二进制文件，支持交叉编译和 UPX 压缩

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `go-version` | string | 是 | - | Go 版本 |
| `binary-name` | string | 是 | - | 二进制文件名 |
| `binary-source-dir` | string | 是 | - | 二进制输出目录 |
| `binary-output` | string | 是 | - | 二进制完整输出路径 |
| `version` | string | 是 | - | 版本号 |
| `build-time` | string | 是 | - | 构建时间 |
| `git-commit` | string | 是 | - | Git commit hash |
| `goproxy` | string | 否 | `https://goproxy.cn\|direct` | Go 模块代理 |
| `goprivate` | string | 否 | `''` | 私有模块路径（如 `github.com/your-org/*`） |
| `go-auth-method` | string | 否 | `none` | 私有模块认证方式，详见下方说明 |
| `os` | string | 否 | `linux` | 目标操作系统 |
| `arch` | string | 否 | `amd64` | 目标架构（`amd64` 或 `arm64`，每次构建只产出对应架构一个二进制；K8s 集群含 arm64 节点时传 `arm64` 用 Go 交叉编译） |
| `upx-compress` | string | 否 | `false` | 是否启用 UPX 压缩 |
| `retention-days` | string | 否 | `7` | 构建产物保留天数 |

**所需 Secrets：**

| Secret | 认证方式 | 说明 |
|--------|----------|------|
| `APP_ID` | `app-token` | GitHub App ID |
| `APP_PRIVATE_KEY` | `app-token` | GitHub App 私钥 |
| `GIT_SSH_PRIVATE_KEY` | `ssh` | SSH 私钥（Legacy） |

---

#### 🔐 Go 私有模块认证（`go-auth-method`）

当项目依赖私有 Go 模块（通过 `goprivate` 指定）时，CI 需要认证才能拉取。`go-auth-method` 支持三种方式：

| 值 | 说明 | Secrets | 安全性 |
|-----|------|---------|--------|
| `app-token` | **推荐** — 使用 GitHub App 动态生成短期 Token | `APP_ID` + `APP_PRIVATE_KEY` | ⭐⭐⭐ Token 自动过期，权限可细粒度控制 |
| `ssh` | 传统方式 — 使用 SSH 私钥拉取 | `GIT_SSH_PRIVATE_KEY` | ⭐⭐ 私钥长期有效，需手动轮换 |
| `none` | 不需要认证（公开仓库或已有其他认证） | 无 | — |

**优先级链：** `input > vars.GO_AUTH_METHOD > 'none'`

**`app-token` 方式工作原理：**

1. 使用 `actions/create-github-app-token@v1` 由 `APP_ID` + `APP_PRIVATE_KEY` 生成临时 Token
2. 自动配置 `git config --global url."https://x-access-token:<token>@github.com/".insteadOf "https://github.com/"`
3. Token 仅在当前 Job 生命周期内有效，Job 结束自动失效

**GitHub App 配置步骤：**

1. 前往 GitHub → Settings → Developer settings → GitHub Apps → New GitHub App
2. 设置 Permissions：Contents → Read-only（拉取私有仓库）
3. 安装 App 到目标组织/仓库
4. 将 App ID 存入仓库 Secret `APP_ID`
5. 将 App 生成的 Private Key 存入仓库 Secret `APP_PRIVATE_KEY`

**`ssh` 方式工作原理：**

1. 使用 `webfactory/ssh-agent@v0.9.0` 加载 SSH 私钥
2. 配置 `git config --global url."git@github.com:".insteadOf "https://github.com/"`
3. 私钥永不过期，需定期手动轮换

**调用示例：**

```yaml
# 推荐：App Token 方式
build-binary:
  uses: kamalyes/deploy-pipeline/.github/workflows/backend-build-binary.yml@master
  with:
    binary-name: 'my-service'
    goprivate: 'github.com/my-org/*'
    go-auth-method: 'app-token'
  secrets:
    APP_ID: ${{ secrets.APP_ID }}
    APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}

# Legacy：SSH 方式
build-binary:
  uses: kamalyes/deploy-pipeline/.github/workflows/backend-build-binary.yml@master
  with:
    binary-name: 'my-service'
    goprivate: 'github.com/my-org/*'
    go-auth-method: 'ssh'
  secrets:
    GIT_SSH_PRIVATE_KEY: ${{ secrets.GIT_SSH_PRIVATE_KEY }}
```

#### Build Docker (Backend)

构建后端 Docker 镜像并推送到镜像仓库

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `binary-name` | string | 是 | - | 二进制文件名 |
| `version` | string | 是 | - | 版本号 |
| `http-port` | string | 是 | - | HTTP 端口 |
| `rpc-port` | string | 是 | - | gRPC 端口 |
| `ws-port` | string | 否 | `''` | WebSocket 端口（可选） |
| `ws-deployment-mode` | string | 否 | `combined` | WebSocket 部署模式：combined（混合）或 separate（独立） |
| `ws-app-name` | string | 否 | `${binary-name}-ws` | WebSocket 独立部署的应用名称 |
| `pprof-port` | string | 是 | - | Pprof 端口 |
| `docker-registry` | string | 是 | - | 镜像仓库地址 |
| `binary-source-dir` | string | 是 | - | 二进制文件目录 |
| `image-base` | string | 是 | - | 基础镜像名（不带 tag） |
| `image-name` | string | 是 | - | 完整镜像名（带 tag） |
| `arch` | string | 否 | `amd64` | 目标架构（`amd64` 或 `arm64`），需与 build-binary 一致；build-push-action 用 `linux/${arch}` 标记 manifest，K8s 才能调度到对应架构节点 |
| `extra-copy-dirs` | string | 否 | `''` | 额外拷贝到 Docker 镜像的目录（逗号分隔，支持 `src:dest` 格式，如 `proto` 或 `proto:./proto,locales:./locales`） |

**所需 Secrets：**

| Secret | 必填 | 说明 |
|--------|------|------|
| `DOCKER_USERNAME` | 否 | 镜像仓库用户名，默认 `github.actor` |
| `DOCKER_PASSWORD` | 否 | 镜像仓库密码/Token，默认 `GITHUB_TOKEN` |

#### Deploy K8s (Backend)

部署后端服务到 Kubernetes 集群

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `environment` | string | 是 | - | 部署环境（用作 K8s namespace） |
| `image-name` | string | 是 | - | 完整镜像名（带 tag） |
| `binary-name` | string | 是 | - | 服务名 |
| `kind` | string | 否 | `Deployment` | Kubernetes workload 类型：Deployment/StatefulSet/DaemonSet |
| `service-kind` | string | 否 | `Service` | Kubernetes Service kind 类型 |
| `service-type` | string | 否 | `ClusterIP` | Kubernetes Service 暴露类型：ClusterIP/NodePort/LoadBalancer |
| `http-port` | string | 是 | - | HTTP 端口 |
| `rpc-port` | string | 是 | - | gRPC 端口 |
| `ws-port` | string | 否 | `''` | WebSocket 端口（可选） |
| `pprof-port` | string | 是 | - | Pprof 端口 |
| `config-yaml` | string | 否 | `''` | 配置文件路径 |
| `configmap-name` | string | 否 | `''` | ConfigMap 名称 |
| `enable-hpa` | string | 否 | `true` | 是否启用 HPA |
| `ingress-path-prefix` | string | 否 | `''` | Ingress 路径前缀 |
| `notification-provider` | string | 否 | `none` | 通知方式：`none`/`feishu`/`wecom`/`telegram`/`all` |

**所需环境级 Secrets：**

| Secret | 必填 | 说明 |
|--------|------|------|
| `KUBECONFIG` | 是 | K8s 集群配置 |
| `FEISHU_WEBHOOK_URL` | 否 | 飞书通知 Webhook URL |
| `WECOM_WEBHOOK_URL` | 否 | 企业微信群机器人 Webhook URL（完整地址） |
| `WECOM_WEBHOOK_KEY` | 否 | 企业微信群机器人 key（与 URL 二选一） |
| `TELEGRAM_BOT_TOKEN` | 否 | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | 否 | Telegram 聊天群组 ID |

### 前端工作流

#### Build Frontend

前端构建，支持 npm、pnpm、yarn 三种包管理器

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `node-version` | string | 是 | - | Node.js 版本 |
| `version` | string | 是 | - | 构建版本号 |
| `build-time` | string | 是 | - | 构建时间戳 |
| `git-commit` | string | 是 | - | Git commit hash |
| `package-manager` | string | 否 | `npm` | 包管理器：npm/pnpm/yarn |
| `package-registry` | string | 否 | `https://registry.npmmirror.com` | 包管理器仓库地址 |
| `build-command` | string | 否 | `npm run build` | 构建命令（monorepo 可传 `pnpm run build:xxx`） |
| `output-dir` | string | 否 | `dist` | 构建输出目录 |
| `artifact-name` | string | 否 | `frontend-dist` | 构建产物 Artifact 名称（多应用并行构建时需区分） |
| `pnpm-version` | string | 否 | `8` | pnpm 版本（仅 package-manager 为 pnpm 时生效） |
| `retention-days` | string | 否 | `7` | 构建产物保留天数 |

#### Build Docker (Frontend)

构建前端 Docker 镜像（基于 Nginx）

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `app-name` | string | 是 | - | 前端应用名称 |
| `version` | string | 是 | - | 版本号 |
| `http-port` | string | 否 | `80` | HTTP 端口 |
| `docker-registry` | string | 是 | - | 镜像仓库地址 |
| `image-base` | string | 是 | - | 基础镜像名（不带 tag） |
| `image-name` | string | 是 | - | 完整镜像名（带 tag） |
| `base-image` | string | 否 | `nginx:1.25.3-alpine` | 基础 Nginx 镜像 |
| `nginx-config-path` | string | 否 | `''` | 项目自定义 nginx.conf 路径 |
| `artifact-name` | string | 否 | `frontend-dist` | 构建产物 Artifact 名称（需与 build-frontend 一致） |
| `os` | string | 否 | `linux` | 目标 OS |
| `arch` | string | 否 | `amd64` | 目标架构（`amd64` / `arm64`），与 `os` 拼成 `${os}/${arch}` 传给 build-push-action 的 platforms |

#### Deploy K8s (Frontend)

部署前端应用到 Kubernetes 集群

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `environment` | string | 是 | - | 部署环境（用作 K8s namespace） |
| `image-name` | string | 是 | - | 完整镜像名（带 tag） |
| `app-name` | string | 是 | - | 应用名称 |
| `http-port` | string | 否 | `80` | HTTP 端口 |
| `ingress-path-prefix` | string | 否 | `''` | Ingress 路径前缀 |
| `enable-hpa` | string | 否 | `true` | 是否启用 HPA |
| `notification-provider` | string | 否 | `none` | 通知方式：`none`/`feishu`/`wecom`/`telegram`/`all` |

**所需 Secrets：**

| Secret | 必填 | 说明 |
|--------|------|------|
| `DOCKER_USERNAME` | 否 | 镜像仓库用户名，默认 `github.actor` |
| `DOCKER_PASSWORD` | 否 | 镜像仓库密码/Token，默认 `GITHUB_TOKEN` |

#### Deploy K8s (Frontend)

部署前端应用到 Kubernetes 集群

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `environment` | string | 是 | - | 部署环境（用作 K8s namespace） |
| `image-name` | string | 是 | - | 完整镜像名（带 tag） |
| `app-name` | string | 是 | - | 应用名称 |
| `http-port` | string | 否 | `80` | HTTP 端口 |
| `ingress-path-prefix` | string | 否 | `''` | Ingress 路径前缀 |
| `enable-hpa` | string | 否 | `true` | 是否启用 HPA |
| `notification-provider` | string | 否 | `none` | 通知方式：`none`/`feishu`/`wecom`/`telegram`/`all` |

**所需环境级 Secrets：**

| Secret | 必填 | 说明 |
|--------|------|------|
| `KUBECONFIG` | 是 | K8s 集群配置 |
| `FEISHU_WEBHOOK_URL` | 否 | 飞书通知 Webhook URL |
| `WECOM_WEBHOOK_URL` | 否 | 企业微信群机器人 Webhook URL（完整地址） |
| `WECOM_WEBHOOK_KEY` | 否 | 企业微信群机器人 key（与 URL 二选一） |
| `TELEGRAM_BOT_TOKEN` | 否 | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | 否 | Telegram 聊天群组 ID |

---

## 📊 K8s 部署架构

### 后端服务架构

```mermaid
graph TD
    subgraph Namespace[命名空间: dev/prod]
        subgraph ConfigMap[ConfigMap]
            config[配置文件]
        end
        
        subgraph Workload[Deployment/StatefulSet/DaemonSet]
            subgraph Container[容器]
                http[HTTP: 8080]
                rpc[gRPC: 9090]
                ws[WebSocket: 9091]
                pprof[Pprof: 6060]
                probes[健康检查]
            end
        end
        
        subgraph Service[Service]
            service[ClusterIP/NodePort/LoadBalancer]
        end
        
        subgraph HPA[HorizontalPodAutoscaler]
            hpa[自动扩缩容]
        end
        
        subgraph Ingress[IngressRoute]
            ingress[Traefik]
        end
    end
    
    config -->|mount| Container
    Container -->|expose| service
    service -->|scale| hpa
    service -->|route| ingress
```

### 前端应用架构

```mermaid
graph TD
    subgraph Namespace[命名空间: dev/prod]
        subgraph Deployment[Deployment]
            subgraph Container[NGINX 容器]
                http[HTTP: 80]
                static[静态文件]
                probes[健康检查]
            end
        end
        
        subgraph Service[Service]
            service[ClusterIP]
        end
        
        subgraph HPA[HorizontalPodAutoscaler]
            hpa[自动扩缩容]
        end
        
        subgraph Ingress[IngressRoute]
            ingress[Traefik]
        end
    end
    
    Container -->|expose| service
    service -->|scale| hpa
    service -->|route| ingress
```

### CI/CD 流程图

```mermaid
flowchart TD
    subgraph GitHub[GitHub Repository]
        code[源代码]
        secrets[Secrets/Vars]
    end
    
    subgraph BackendPipeline[后端 CI/CD]
        B1[代码质量检查]
        B2[编译二进制]
        B3[构建 Docker]
        B4[部署到 K8s]
    end
    
    subgraph FrontendPipeline[前端 CI/CD]
        F1[安装依赖]
        F2[构建前端]
        F3[构建 Docker]
        F4[部署到 K8s]
    end
    
    subgraph Artifacts[构建产物]
        binary[Go 二进制]
        dist[前端 dist]
        image[Docker 镜像]
    end
    
    subgraph K8s[Kubernetes 集群]
        dev[Dev 环境]
        test[Test 环境]
        prod[Prod 环境]
    end
    
    code --> B1
    code --> F1
    secrets --> B1
    secrets --> F1
    
    B1 --> B2
    B2 --> binary
    binary --> B3
    B3 --> image
    image --> B4
    B4 --> dev
    B4 --> test
    B4 --> prod
    
    F1 --> F2
    F2 --> dist
    dist --> F3
    F3 --> image
    image --> F4
    F4 --> dev
    F4 --> test
    F4 --> prod
```

---


### CI/CD 调用时序

上面的流程图讲「有哪些环节」，这张图讲「谁在什么时候调了谁」——
可复用工作流的交接点、产物在哪一步落地、通知在哪一步发出。

```mermaid
sequenceDiagram
    autonumber
    participant DEV as 开发者
    participant REPO as 业务仓库
    participant PIPE as deploy-pipeline 可复用工作流
    participant RUNNER as Runner
    participant REG as 镜像仓库
    participant K8S as K8s 集群
    participant IM as 飞书 / 企业微信 / Telegram

    DEV->>REPO: push 或 workflow_dispatch
    REPO->>PIPE: uses: code-quality.yml
    PIPE->>RUNNER: gofmt + go vet + 单测 + 覆盖率
    RUNNER-->>PIPE: 上传覆盖率报告 artifact

    REPO->>PIPE: uses: backend-build-binary.yml
    PIPE->>RUNNER: 换 App 令牌，拉私有模块，交叉编译
    RUNNER-->>PIPE: 上传二进制 artifact

    REPO->>PIPE: uses: backend-build-docker.yml
    PIPE->>RUNNER: 下载二进制 artifact，docker buildx
    RUNNER->>REG: push 多架构镜像
    REG-->>RUNNER: digest

    REPO->>PIPE: uses: backend-deploy-k8s.yml
    PIPE->>RUNNER: checkout deploy-pipeline，envsubst 渲染模板
    RUNNER->>K8S: kubectl apply + rollout status
    alt 滚动更新成功
        K8S-->>RUNNER: 所有副本就绪
        RUNNER->>IM: 发部署成功卡片
    else 超时或失败
        K8S-->>RUNNER: rollout 超时
        RUNNER->>K8S: kubectl rollout undo 回滚
        RUNNER->>IM: 发失败卡片，附 Actions 链接
    end
```

> 每个 `uses:` 都是一次独立的 job 交接：产物只能通过 artifact 传递，环境变量与 Secret
> 不会自动跨 job 继承 —— 这就是为什么调用方必须显式写 `secrets:` 或 `secrets: inherit`。

---

## 📦 K8s 模板清单与占位符

`templates/k8s/` 下的 12 个 yaml 不是直接 apply 的成品，而是 **envsubst 模板**。
部署工作流会把本仓库 checkout 到 `_deploy-workflows/`，导出一堆环境变量，再逐个渲染。

```text
deploy-pipeline checkout → _deploy-workflows/templates/k8s/<backend|frontend>/*.yaml
        ↓ export APP_NAME / NAMESPACE / IMAGE_NAME / ...（来自 inputs、vars、计算结果）
        ↓ envsubst < 模板 > ${DEPLOY_DIR}/<资源>.yaml
        ↓ kubectl apply -f ${DEPLOY_DIR}/
```

> ⚠️ **Ingress 相关模板是 Traefik CRD**（`traefik.io/v1alpha1` 的 `IngressRoute` 与
> `Middleware`），不是 k8s 原生 `Ingress`。集群里没装 Traefik 的话，这几个资源
> apply 会失败 —— 要么装 Traefik，要么 fork 掉这几个模板换成自己的 Ingress 实现。

### 后端模板（templates/k8s/backend/）

| 模板 | 资源类型 | 渲染条件 | 作用 |
|------|---------|---------|------|
| `deployment.yaml` | `${KIND}`（Deployment / StatefulSet / DaemonSet）| 总是 | 主工作负载：镜像、探针、资源限额、端口、配置挂载、环境变量 |
| `service.yaml` | `${SERVICE_KIND}`（默认 Service）| 总是 | 服务暴露，端口按 HTTP / RPC / WS / pprof / node-grpc 分块拼装 |
| `hpa.yaml` | HorizontalPodAutoscaler | `ENABLE_HPA=true` | CPU + 内存双指标自动扩缩容 |
| `ingress.yaml` | Traefik IngressRoute | `INGRESS_HOST` 非空 | 主路由，按需带 TLS、StripPrefix 中间件、WebSocket 子路由 |
| `ingress-http.yaml` | Traefik IngressRoute | `INGRESS_TLS=true` 且 `INGRESS_REDIRECT_HTTP_TO_HTTPS=true` | 额外的 80 端口路由，专门用来跳转到 HTTPS |
| `middleware.yaml` | Traefik Middleware | `ingress-path-prefix` 非空 | StripPrefix，把 `/svc-a/xxx` 剥成 `/xxx` 再转给服务 |
| `middleware-redirect.yaml` | Traefik Middleware | 同 `ingress-http.yaml` | redirectScheme，HTTP → HTTPS |

### 前端模板（templates/k8s/frontend/）

| 模板 | 资源类型 | 渲染条件 | 作用 |
|------|---------|---------|------|
| `deployment.yaml` | Deployment | 总是 | 静态站点容器（Nginx），只有一个 HTTP 端口 |
| `service.yaml` | Service | 总是 | 单端口暴露 |
| `hpa.yaml` | HorizontalPodAutoscaler | `ENABLE_HPA=true` | 仅 CPU 指标 |
| `ingress.yaml` | Traefik IngressRoute | `INGRESS_HOST` 非空 | 主路由，按需带 TLS 与 StripPrefix |
| `middleware.yaml` | Traefik Middleware | `ingress-path-prefix` 非空 | StripPrefix |

后端比前端多出 `ingress-http.yaml` 与 `middleware-redirect.yaml`，因为后端对外常要同时
保留 80（跳转）和 443（服务）；前端站点一般由上层网关统一接管跳转。

### 占位符从哪来

占位符分三类，排查「某个值怎么不对」时先认清它属于哪一类：

| 类别 | 例子 | 来源 |
|------|------|------|
| **标量值** | `APP_NAME`、`NAMESPACE`、`IMAGE_NAME`、`REPLICAS`、`CPU_LIMIT`、`MEMORY_LIMIT`、`RUN_MODE` | 工作流 inputs → 环境 vars → 默认值，逐级回退 |
| **结构块**（以 `_BLOCK` 结尾）| `DEPLOY_HTTP_PORT_BLOCK`、`SVC_RPC_PORT_BLOCK`、`LIVENESS_PROBE_BLOCK`、`TLS_BLOCK`、`MIDDLEWARES_BLOCK`、`EXTRA_ENV_BLOCK` | 工作流里按条件拼出的**多行 YAML 片段**；对应能力关闭时拼成空串，整段消失 |
| **计算值** | `INGRESS_MATCH_RULE`、`INGRESS_STRIP_PREFIX`、`GOMEMLIMIT_ENV_BLOCK` | 由其他值推导，如 `INGRESS_MATCH_RULE` 由 host 与 path-prefix 拼成 Traefik 规则 |

完整占位符清单：

| 模板 | 占位符 |
|------|--------|
| backend/deployment.yaml | `KIND` `APP_NAME` `NAMESPACE` `IMAGE_NAME` `IMAGE_PULL_POLICY` `IMAGE_PULL_SECRETS` `REPLICAS` `REVISION_HISTORY_LIMIT` `RUN_MODE` `CPU_REQUEST` `CPU_LIMIT` `MEMORY_REQUEST` `MEMORY_LIMIT` `CONFIGMAP_NAME` `CONFIG_MOUNT_PATH` `TERMINATION_GRACE_PERIOD_SECONDS` `ANNOTATIONS_BLOCK` `DEPLOY_HTTP_PORT_BLOCK` `DEPLOY_RPC_PORT_BLOCK` `DEPLOY_WS_PORT_BLOCK` `DEPLOY_PPROF_PORT_BLOCK` `DEPLOY_NODE_GRPC_PORT_BLOCK` `LIVENESS_PROBE_BLOCK` `READINESS_PROBE_BLOCK` `STARTUP_PROBE_BLOCK` `EXTRA_ENV_BLOCK` `GOMEMLIMIT_ENV_BLOCK` |
| backend/service.yaml | `SERVICE_KIND` `SERVICE_TYPE` `APP_NAME` `NAMESPACE` `SVC_HTTP_PORT_BLOCK` `SVC_RPC_PORT_BLOCK` `SVC_WS_PORT_BLOCK` `SVC_PPROF_PORT_BLOCK` `SVC_NODE_GRPC_PORT_BLOCK` |
| backend/hpa.yaml | `APP_NAME` `NAMESPACE` `MIN_REPLICAS` `MAX_REPLICAS` `TARGET_CPU_UTILIZATION` `TARGET_MEMORY_UTILIZATION` |
| backend/ingress.yaml | `APP_NAME` `NAMESPACE` `INGRESS_ENTRY_POINT` `INGRESS_MATCH_RULE` `INGRESS_PORT` `INGRESS_WS_ROUTE_BLOCK` `MIDDLEWARES_BLOCK` `TLS_BLOCK` |
| backend/ingress-http.yaml | `APP_NAME` `NAMESPACE` `INGRESS_MATCH_RULE` `INGRESS_PORT` `INGRESS_WS_ROUTE_BLOCK` `INGRESS_REDIRECT_MIDDLEWARE` |
| backend/middleware.yaml | `NAMESPACE` `INGRESS_STRIP_NAME` `INGRESS_STRIP_PREFIX` |
| backend/middleware-redirect.yaml | `APP_NAME` `NAMESPACE` |
| frontend/deployment.yaml | `APP_NAME` `NAMESPACE` `IMAGE_NAME` `IMAGE_PULL_POLICY` `IMAGE_PULL_SECRETS` `REPLICAS` `REVISION_HISTORY_LIMIT` `RUN_MODE` `HTTP_PORT` `CPU_REQUEST` `CPU_LIMIT` `MEMORY_REQUEST` `MEMORY_LIMIT` `CONFIGMAP_NAME` `CONFIG_MOUNT_PATH` `LIVENESS_DELAY` `LIVENESS_PERIOD` `READINESS_DELAY` `READINESS_PERIOD` |
| frontend/service.yaml | `APP_NAME` `NAMESPACE` `HTTP_PORT` |
| frontend/hpa.yaml | `APP_NAME` `NAMESPACE` `MIN_REPLICAS` `MAX_REPLICAS` `TARGET_CPU_UTILIZATION` |
| frontend/ingress.yaml | `APP_NAME` `NAMESPACE` `HTTP_PORT` `INGRESS_ENTRY_POINT` `INGRESS_MATCH_RULE` `MIDDLEWARES_BLOCK` `TLS_BLOCK` |
| frontend/middleware.yaml | `NAMESPACE` `INGRESS_STRIP_NAME` `INGRESS_STRIP_PREFIX` |

### 渲染与部署时序

```mermaid
sequenceDiagram
    autonumber
    participant WF as backend-deploy-k8s.yml
    participant DP as deploy-pipeline 仓库
    participant ENV as 环境变量
    participant ES as envsubst
    participant KC as kubectl
    participant K8S as K8s 集群

    WF->>DP: checkout 到 _deploy-workflows/
    WF->>ENV: 导出标量：APP_NAME / NAMESPACE / IMAGE_NAME / REPLICAS ...
    WF->>ENV: 按条件拼结构块：探针、端口、TLS、中间件
    Note over WF,ENV: 能力关闭时对应 _BLOCK 拼成空串<br/>渲染后整段消失，而不是留下空字段

    WF->>ES: deployment.yaml
    ES-->>WF: 写入 DEPLOY_DIR
    WF->>ES: service.yaml
    ES-->>WF: 写入 DEPLOY_DIR
    alt ENABLE_HPA=true
        WF->>ES: hpa.yaml
    end
    alt INGRESS_HOST 非空
        alt ingress-path-prefix 非空
            WF->>ES: middleware.yaml，StripPrefix
        end
        alt INGRESS_TLS 且 REDIRECT_HTTP_TO_HTTPS
            WF->>ES: middleware-redirect.yaml + ingress-http.yaml
        end
        WF->>ES: ingress.yaml
    end

    WF->>WF: 上传渲染结果作为 artifact
    WF->>KC: kubectl apply -f DEPLOY_DIR/
    KC->>K8S: 创建或更新资源
    KC->>K8S: rollout status 等待就绪
    K8S-->>KC: 就绪或超时
```

### 怎么定制

| 想做什么 | 做法 |
|---------|------|
| 只调数值（副本数、限额、探针延迟）| 配对应的 input 或环境 Variable，别动模板 |
| 加一个固定的 annotation / label | 用 `ANNOTATIONS_BLOCK` 之类的块输入，仍然不用动模板 |
| 换 Ingress 实现（Nginx Ingress / Gateway API）| fork 本仓库改 `templates/k8s/*/ingress*.yaml` 与 `middleware*.yaml`，业务侧把 `uses:` 指到你的 fork |
| 加一种全新资源（如 ServiceMonitor）| 在 `templates/k8s/<side>/` 加模板，并在对应 deploy 工作流里补 envsubst 与 apply |

调试建议：部署工作流会把渲染后的 yaml 作为 artifact 上传，出问题时先下载它看实际渲染结果 ——
绝大多数「apply 报错」都是某个 `_BLOCK` 拼出来缩进不对，直接看成品比猜变量快。

---

## 🧩 Composite Actions

`.github/actions/` 下四个复合 Action，可在自己的工作流里单独引用。

### normalize-image-name

把 Docker 镜像名规范化，并按需生成 tag 列表。

**为什么需要它**：Docker 对镜像名有两条硬规则 —— repository 部分必须全小写，tag 里不能出现 `/`。
而 GitHub 的组织名常带大写（`HitGameAI`），分支名常带斜杠（`feature/turnover`），直接拼进镜像名
会在 push 阶段报 `invalid reference format`。这个 Action 把 repository 转小写、把 tag 里的 `/` 换成 `-`，
同时**不碰 registry 端口号**（`registry:5000/app:v1` 里 `/` 之前的冒号不会被当成 tag 分隔符）。

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
  uses: kamalyes/deploy-pipeline/.github/actions/normalize-image-name@master
  with:
    image-base: ghcr.io/${{ github.repository_owner }}/${{ github.event.repository.name }}
    image-name: ghcr.io/${{ github.repository_owner }}/${{ github.event.repository.name }}:${{ github.ref_name }}
    generate-tags: 'true'
- run: echo "${{ steps.img.outputs.tags }}"
```

### print-runner-info

打印 runner 的内核、架构、CPU 型号与核数、内存、磁盘。无入参无出参。

排查这几类问题时很省事：构建突然变慢（换了规格更低的 runner）、交叉编译结果不对
（架构与预期不符）、`go build` 被 OOM kill（内存不够）、磁盘写满导致 docker build 失败。

```yaml
- uses: kamalyes/deploy-pipeline/.github/actions/print-runner-info@master
```

### notify

统一的通知 Action，支持飞书 / 企业微信 / Telegram，详见
[通知与发布单](#-通知与发布单) 一节的「通用通知 Action」。

### update-proto-mod

下游仓库升级 proto 模块引用，详见
[proto 模块引用升级](#proto-模块引用升级update-proto-mod) 一节。

---

## 📜 公共脚本

### backend/build-linux.sh

Go 二进制构建脚本

```bash
bash scripts/backend/build-linux.sh \
  --version "v1.0.0" \
  --build-time "2026-01-01_00:00:00" \
  --git-commit "abc12356" \
  --binary-name "your-service" \
  --output-dir "deployments" \
  --os linux --arch amd64 \
  --upx-compress false
```

### frontend/build-frontend.sh

前端构建脚本，支持多种包管理器

```bash
bash scripts/frontend/build-frontend.sh \
  --version "v1.0.0" \
  --build-time "2026-01-01_00:00:00" \
  --git-commit "abc12356" \
  --node-version "20" \
  --package-manager "pnpm" \
  --package-registry "https://registry.npmmirror.com" \
  --output-dir "dist"
```

### common.sh

被其他脚本 `source` 的公共函数库，自己不单独执行。所有输出都 `tee` 进 `${LOG_FILE}`，
所以构建日志既能在 Actions 里实时看到，也会落到文件里随 artifact 一起上传。

```bash
source "$(dirname "$0")/../common.sh"     # 相对路径按调用方位置调整
```

| 函数 | 用法 | 说明 |
|------|------|------|
| `log_info` | `log_info "正在构建"` | ℹ️ 普通信息 |
| `log_success` | `log_success "构建完成"` | ✅ 成功 |
| `log_warn` | `log_warn "跳过 UPX 压缩"` | ⚠️ 警告，不中断 |
| `log_error` | `log_error "找不到 go"` | ❌ 错误，需调用方自己决定是否 exit |
| `log_section` | `log_section "环境准备"` | 带分隔线的大标题，用于划分阶段 |
| `log_step` | `log_step "下载依赖"` | ▶️ 阶段内的小步骤 |
| `run_command` | `run_command "go build ./..." "编译"` | 执行命令并打印描述与结果，失败返回非零 |
| `check_env` | `check_env VERSION GIT_COMMIT` | 批量校验环境变量非空，缺哪个报哪个 |
| `ensure_directory` | `ensure_directory deployments` | 目录不存在则创建 |
| `show_directory_tree` | `show_directory_tree deployments 2` | 打印目录结构，第二个参数是深度（默认 2）|

写新脚本时优先用这些函数而不是裸 `echo`：日志格式统一，出问题时在几百行 Actions
输出里能一眼找到阶段边界。

### update-proto-mod.sh

下游仓库升级 proto 模块引用的核心脚本，由
[`update-proto-mod`](#proto-模块引用升级update-proto-mod) Action 调用，也可本地手动跑。

```bash
bash scripts/update-proto-mod.sh <目标分支> <模块名> <模块版本>
# 例：bash scripts/update-proto-mod.sh master demo-share-proto master
```

| 参数 | 必填 | 说明 |
|------|------|------|
| 1. 目标分支 | ✅ | 升级后推回哪个分支 |
| 2. 模块名 | ✅ | proto 仓库名，组织前缀由 `GITHUB_REPOSITORY` 推导 |
| 3. 模块版本 | 否（默认 `master`）| 可填分支、tag 或 commit hash |

| 环境变量 | 说明 |
|---------|------|
| `PROTO_READ_TOKEN` | 能读 proto 仓库的令牌（GitHub App token）|
| `PUSH_TOKEN` | 能推当前仓库的令牌（一般用 `GITHUB_TOKEN`）|
| `GITHUB_REPOSITORY` | `owner/repo`，Actions 自动注入；本地跑要手动导出 |

执行流程：配置 `url.insteadOf` 让 go 带令牌拉私有库 → `go get <module>@<ref>` →
`go mod tidy` → 有 diff 才提交推送（无变化直接退出，不会产生空提交）。

> 本地手动跑之前先确认 `GOPRIVATE` 覆盖了该组织，否则 go 会走公共代理拿不到私有模块。

### notify.sh

通用通知脚本，支持飞书 / 企业微信 / Telegram（单通道失败不影响其他通道）

```bash
export NOTIFICATION_PROVIDER=feishu  # feishu | wecom | telegram | all | none
export NOTIFICATION_TYPE=deployment  # deployment | rollback | custom
export STATUS=success                # success | failure | warning | info
export ENVIRONMENT=dev
export BINARY_NAME=your-service
export WORKFLOW_URL=https://github.com/...
export FEISHU_WEBHOOK_URL=https://open.feishu.cn/...
bash scripts/notify.sh

# 企业微信
export NOTIFICATION_PROVIDER=wecom
export WECOM_WEBHOOK_URL='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'
# export WECOM_WEBHOOK_KEY=xxx                   # 与上面二选一
# export WECOM_MENTIONED_LIST=zhangsan,lisi      # 可选，内联 <@userid>
# export WECOM_MENTIONED_MOBILE_LIST=@all        # 可选，额外补发 1 条 text
bash scripts/notify.sh

# custom 类型：自定义标题与正文（飞书 lark_md / 企业微信 markdown / Telegram Markdown，支持多行）
export NOTIFICATION_TYPE=custom
export CUSTOM_TITLE="📋 自定义标题"
export CUSTOM_MESSAGE="**变更内容**
- PR #12 xxx
- PR #13 yyy"
bash scripts/notify.sh
```

---

## 🔄 工作流脚本引用机制

公共 workflow 通过 `actions/checkout` 额外检出本仓库来获取脚本：

- **backend-build-binary.yml**: 优先使用项目自带 `scripts/backend/build-linux.sh`，不存在则使用公共仓库的
- **frontend-build-frontend.yml**: 优先使用项目自带 `scripts/frontend/build-frontend.sh`，不存在则使用公共仓库的
- **frontend-build-docker.yml**: 优先使用项目自定义 `nginx.conf`，不存在则使用默认配置
- **backend-deploy-k8s.yml / frontend-deploy-k8s.yml**: 通知功能使用公共仓库的 `scripts/notify.sh`

---

## ✅ 自测清单

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
