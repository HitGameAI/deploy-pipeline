# 快速接入与多架构构建

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## 🚀 快速接入

> 下面的示例用 `secrets: inherit` 图省事
> **调用方与本仓库在同一账号下时它是有效的**；
> 跨账号（例如本仓库在个人账号、调用方在组织）必须改成逐个显式映射，
> 原因见[那条说明](#inherit-cross-account)


### 后端 Go 服务示例

调用方的 `pipeline.yml` 只写两类东西：**触发条件**与**本仓特有的决策**
产物名、版本号、镜像名、要不要部署、ConfigMap、容器 env 列表这些，
由 [resolve-service-vars](#resolve-service-vars) 一步算出来

```yaml
name: 🚀 CI/CD Pipeline

on:
  push:
    branches: [dev]
    tags: ['v*']          # 打 tag 走发布模式：只构建推送镜像，不部署
  workflow_dispatch:
    inputs:
      environment:
        type: choice
        options: [dev, staging]
        default: dev
      code-quality:
        type: boolean
        default: false
      build-docker:
        type: boolean
        default: true
      deploy-k8s:
        type: boolean
        default: true
      enable-hpa:
        type: boolean
        default: true

permissions:
  contents: write         # release-note 建 GitHub Release 要写
  packages: write         # 推 GHCR 镜像要写
  pull-requests: read     # release-note 收集 PR 明细要读

jobs:
  setup:
    name: ⚙️ Setup
    runs-on: ubuntu-latest
    environment: ${{ inputs.environment || 'dev' }}
    outputs:
      binary-name: ${{ steps.vars.outputs.binary-name }}
      binary-source-dir: ${{ steps.vars.outputs.binary-source-dir }}
      binary-output: ${{ steps.vars.outputs.binary-output }}
      version: ${{ steps.vars.outputs.version }}
      build-time: ${{ steps.vars.outputs.build-time }}
      git-commit: ${{ steps.vars.outputs.git-commit }}
      image-base: ${{ steps.vars.outputs.image-base }}
      image-name: ${{ steps.vars.outputs.image-name }}
      should-build-docker: ${{ steps.vars.outputs.should-build-docker }}
      should-deploy-k8s: ${{ steps.vars.outputs.should-deploy-k8s }}
      code-quality: ${{ steps.vars.outputs.code-quality }}
      deploy-environment: ${{ steps.vars.outputs.deploy-environment }}
      notification-provider: ${{ steps.vars.outputs.notification-provider }}
      k8s-config-yaml: ${{ steps.vars.outputs.k8s-config-yaml }}
      k8s-configmap-name: ${{ steps.vars.outputs.k8s-configmap-name }}
      k8s-enable-hpa: ${{ steps.vars.outputs.k8s-enable-hpa }}
      cmd-args: ${{ steps.vars.outputs.cmd-args }}
      extra-env-block: ${{ steps.vars.outputs.extra-env-block }}
    steps:
      # 必须先 checkout：下一步要读服务配置文件、扫源码抽密钥名
      # [EN] Check out first: the next step reads the config file and scans the source
      - uses: actions/checkout@v6

      - name: ⚙️ Setup Variables
        id: vars
        uses: kamalyes/deploy-pipeline/.github/actions/resolve-service-vars@master
        with:
          environment: ${{ inputs.environment || 'dev' }}
          vars-json: ${{ toJSON(vars) }}
          inputs-json: ${{ toJSON(inputs) }}
          secrets-json: ${{ toJSON(secrets) }}
          build-docker: ${{ inputs.build-docker }}
          deploy-k8s: ${{ inputs.deploy-k8s }}
          code-quality: ${{ inputs.code-quality }}
          notification-provider: ${{ inputs.notification-provider }}

  code-quality:
    needs: setup
    if: ${{ needs.setup.outputs.code-quality == 'true' }}
    uses: kamalyes/deploy-pipeline/.github/workflows/code-quality.yml@master
    secrets: inherit

  build-binary:
    needs: setup
    uses: kamalyes/deploy-pipeline/.github/workflows/backend-build-binary.yml@master
    with:
      binary-name: ${{ needs.setup.outputs.binary-name }}
      binary-source-dir: ${{ needs.setup.outputs.binary-source-dir }}
      binary-output: ${{ needs.setup.outputs.binary-output }}
      version: ${{ needs.setup.outputs.version }}
      build-time: ${{ needs.setup.outputs.build-time }}
      git-commit: ${{ needs.setup.outputs.git-commit }}
      # 入口包不在仓库根时才需要传；其余构建参数走 builder 配置节
      # [EN] Only needed when the main package is not at the repository root
      binary-build-dir: ./cmd/server
    secrets: inherit

  build-docker:
    needs: [setup, build-binary]
    if: ${{ needs.setup.outputs.should-build-docker == 'true' }}
    uses: kamalyes/deploy-pipeline/.github/workflows/backend-build-docker.yml@master
    with:
      binary-name: ${{ needs.setup.outputs.binary-name }}
      binary-source-dir: ${{ needs.setup.outputs.binary-source-dir }}
      version: ${{ needs.setup.outputs.version }}
      image-base: ${{ needs.setup.outputs.image-base }}
      image-name: ${{ needs.setup.outputs.image-name }}
      environment: ${{ needs.setup.outputs.deploy-environment }}
      cmd-args: ${{ needs.setup.outputs.cmd-args }}
    secrets: inherit

  deploy-k8s:
    needs: [setup, build-docker]
    if: ${{ needs.setup.outputs.should-deploy-k8s == 'true' }}
    uses: kamalyes/deploy-pipeline/.github/workflows/backend-deploy-k8s.yml@master
    with:
      environment: ${{ needs.setup.outputs.deploy-environment }}
      image-name: ${{ needs.setup.outputs.image-name }}
      binary-name: ${{ needs.setup.outputs.binary-name }}
      binary-source-dir: ${{ needs.setup.outputs.binary-source-dir }}
      config-yaml: ${{ needs.setup.outputs.k8s-config-yaml }}
      configmap-name: ${{ needs.setup.outputs.k8s-configmap-name }}
      enable-hpa: ${{ needs.setup.outputs.k8s-enable-hpa }}
      notification-provider: ${{ needs.setup.outputs.notification-provider }}
      extra-env-block: ${{ needs.setup.outputs.extra-env-block }}
    # 必须 inherit 而不是逐个列举：显式映射只把列出的那几个送进公共工作流，
    # 业务密钥就看不见，集群 Secret 同步会整步跳过
    # [EN] Inherit rather than listing: an explicit map forwards only the named secrets, so the
    # business ones stay invisible and the in-cluster Secret sync skips entirely
    secrets: inherit
```

端口、资源限制、镜像仓库、产物布局这些**一行都不用写在这里**，全在
`vars.PIPELINE_CONFIG` 里
要配哪些、默认值是什么，手动触发
[⚙️ Generate Pipeline Config](#generate-pipeline-config) 就会整份打印出来

一个后端服务典型的配置：

```yaml
# vars.PIPELINE_CONFIG
ports:
  http: 8080
  rpc: 9181
service:
  binary_build_dir: ./cmd/server
  extra_copy_dirs: docs
kube:
  namespace: business
  resources:
    limits: { cpu: 1, memory: 1Gi }
  ingress:
    host: api.example.com
    tls: true
env:
  LOG_LEVEL: info
```

```yaml
# secrets.PIPELINE_SECRETS
kube:
  kubeconfig: |
    apiVersion: v1
    ...
app:                      # 业务密钥，键名即容器里的环境变量名
  DB_PASSWORD: xxx
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

K8s 集群同时含 amd64 与 arm64 节点时，镜像 manifest 必须标记正确架构，否则调度到 arm64 节点会拉取失败（`no matching manifest for linux/arm64/v8`）

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

> **注意**：`arch` 必须前后一致
> binary workflow 用 arm64 交叉编译，docker workflow 也必须传 arm64，否则 manifest 架构标记与二进制实际架构不匹配，运行时会崩溃

---
