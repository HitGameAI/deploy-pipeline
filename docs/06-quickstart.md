# 快速接入与多架构构建

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## 🚀 快速接入

第一次从零落地先读 [README 的 0 → 7 步骤](../README.md)，先跑通一个 dev 服务
配置内容由现有的 [Generate Pipeline Config](../.github/workflows/generate-config.yml) 生成，本页只保留工作流调用示例

### 后端 Go 服务示例

先到流水线仓库 Actions → **⚙️ Generate Pipeline Config** → Run workflow，选择 `mode: generate`
从运行 Summary 复制明文配置与密钥骨架，分别保存到业务仓库的 Variable `PIPELINE_CONFIG` 和 Secret `PIPELINE_SECRETS`
完整配置项和默认值以生成结果为准，不再维护独立的 YAML 模板文件
字段用途与首次部署应修改的项目见 [README 第 4 节](../README.md#4-在业务仓库保存两份聚合-yaml)

将下面的调用示例保存为业务仓库 `.github/workflows/pipeline.yml`
迁移组织时替换所有流水线仓库引用，按服务情况调整分支与环境
它通过 resolve-service-vars 计算产物、镜像、部署条件和容器 env，再连接构建、推镜像与部署

```yaml
name: Backend CI/CD

on:
  push:
    branches: [dev]
    tags: ['v*']
  workflow_dispatch:
    inputs:
      environment:
        description: 部署环境
        type: choice
        options: [dev]
        default: dev

# 这是本次运行的 GITHUB_TOKEN 权限，与 GitHub App 后台权限分开
# 镜像工作流内嵌 release-note，调用方也要允许下面的权限
permissions:
  contents: write
  packages: write
  pull-requests: read

concurrency:
  group: pipeline-${{ github.workflow }}-${{ github.ref }}-${{ inputs.environment || 'dev' }}
  cancel-in-progress: false

jobs:
  setup:
    runs-on: ubuntu-latest
    environment: ${{ inputs.environment || 'dev' }}
    outputs:
      binary-name: ${{ steps.vars.outputs.binary-name }}
      binary-source-dir: ${{ steps.vars.outputs.binary-source-dir }}
      binary-output: ${{ steps.vars.outputs.binary-output }}
      binary-build-dir: ${{ steps.vars.outputs.binary-build-dir }}
      version: ${{ steps.vars.outputs.version }}
      build-time: ${{ steps.vars.outputs.build-time }}
      git-commit: ${{ steps.vars.outputs.git-commit }}
      image-base: ${{ steps.vars.outputs.image-base }}
      image-name: ${{ steps.vars.outputs.image-name }}
      deploy-environment: ${{ steps.vars.outputs.deploy-environment }}
      should-build-docker: ${{ steps.vars.outputs.should-build-docker }}
      should-deploy-k8s: ${{ steps.vars.outputs.should-deploy-k8s }}
      k8s-config-yaml: ${{ steps.vars.outputs.k8s-config-yaml }}
      k8s-configmap-name: ${{ steps.vars.outputs.k8s-configmap-name }}
      cmd-args: ${{ steps.vars.outputs.cmd-args }}
      extra-copy-dirs: ${{ steps.vars.outputs.extra-copy-dirs }}
      extra-env-block: ${{ steps.vars.outputs.extra-env-block }}
    steps:
      - uses: actions/checkout@v6
      - id: vars
        uses: HitGameAI/deploy-pipeline/.github/actions/resolve-service-vars@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
        with:
          environment: ${{ inputs.environment || 'dev' }}
          vars-json: ${{ toJSON(vars) }}
          inputs-json: ${{ toJSON(inputs) }}
          secrets-json: ${{ toJSON(secrets) }}
          build-docker: 'true'
          deploy-k8s: 'true'

  build-binary:
    needs: setup
    uses: HitGameAI/deploy-pipeline/.github/workflows/backend-build-binary.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
    with:
      environment: ${{ needs.setup.outputs.deploy-environment }}
      binary-name: ${{ needs.setup.outputs.binary-name }}
      binary-source-dir: ${{ needs.setup.outputs.binary-source-dir }}
      binary-output: ${{ needs.setup.outputs.binary-output }}
      binary-build-dir: ${{ needs.setup.outputs.binary-build-dir }}
      version: ${{ needs.setup.outputs.version }}
      build-time: ${{ needs.setup.outputs.build-time }}
      git-commit: ${{ needs.setup.outputs.git-commit }}
    secrets:
      PIPELINE_SECRETS: ${{ secrets.PIPELINE_SECRETS }}

  build-docker:
    needs: [setup, build-binary]
    if: ${{ needs.setup.outputs.should-build-docker == 'true' }}
    uses: HitGameAI/deploy-pipeline/.github/workflows/backend-build-docker.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
    with:
      environment: ${{ needs.setup.outputs.deploy-environment }}
      binary-name: ${{ needs.setup.outputs.binary-name }}
      binary-source-dir: ${{ needs.setup.outputs.binary-source-dir }}
      version: ${{ needs.setup.outputs.version }}
      image-base: ${{ needs.setup.outputs.image-base }}
      image-name: ${{ needs.setup.outputs.image-name }}
      cmd-args: ${{ needs.setup.outputs.cmd-args }}
      extra-copy-dirs: ${{ needs.setup.outputs.extra-copy-dirs }}
    secrets:
      PIPELINE_SECRETS: ${{ secrets.PIPELINE_SECRETS }}

  deploy-k8s:
    needs: [setup, build-docker]
    if: ${{ needs.setup.outputs.should-deploy-k8s == 'true' }}
    uses: HitGameAI/deploy-pipeline/.github/workflows/backend-deploy-k8s.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
    with:
      environment: ${{ needs.setup.outputs.deploy-environment }}
      image-name: ${{ needs.setup.outputs.image-name }}
      binary-name: ${{ needs.setup.outputs.binary-name }}
      binary-source-dir: ${{ needs.setup.outputs.binary-source-dir }}
      config-yaml: ${{ needs.setup.outputs.k8s-config-yaml }}
      configmap-name: ${{ needs.setup.outputs.k8s-configmap-name }}
      extra-env-block: ${{ needs.setup.outputs.extra-env-block }}
    secrets:
      PIPELINE_SECRETS: ${{ secrets.PIPELINE_SECRETS }}
```

默认配置文件是业务仓库的 `config/config.dev.yaml`，集中配置仓库见 [README](../README.md#82-集中配置仓库)
没有私有 Go 模块时显式设 `builder.go_auth_method: none`，无需 App 或 SSH
示例显式传递 `PIPELINE_SECRETS`，其中 app 节业务密钥也会一起传递
分散的旧业务 Secrets 应先迁移到 app 节，配置作用域见[配置指南](03-configuration.md)

下面前端示例保留了 inherit / 独立 Secret 的调用形式
前端新接入同样以 `PIPELINE_CONFIG` / `PIPELINE_SECRETS` 为配置来源，并显式映射聚合 Secret：

```yaml
secrets:
  PIPELINE_SECRETS: ${{ secrets.PIPELINE_SECRETS }}
```

### 前端应用示例

```yaml
name: Frontend CI/CD

on:
  push:
    branches: [main]

jobs:
  build-frontend:
    uses: HitGameAI/deploy-pipeline/.github/workflows/frontend-build-frontend.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
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
    uses: HitGameAI/deploy-pipeline/.github/workflows/frontend-build-docker.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
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
    uses: HitGameAI/deploy-pipeline/.github/workflows/frontend-deploy-k8s.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
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
    uses: HitGameAI/deploy-pipeline/.github/workflows/frontend-build-frontend.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
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
    uses: HitGameAI/deploy-pipeline/.github/workflows/frontend-build-docker.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
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
    uses: HitGameAI/deploy-pipeline/.github/workflows/frontend-deploy-k8s.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
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
    uses: HitGameAI/deploy-pipeline/.github/workflows/backend-build-binary.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
    with:
      binary-name: your-service
      arch: arm64         # 👈 关键：Go 交叉编译 linux/arm64 二进制
      # ...其他参数
  docker-arm64:
    needs: build-arm64
    uses: HitGameAI/deploy-pipeline/.github/workflows/backend-build-docker.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
    with:
      binary-name: your-service
      arch: arm64         # 👈 关键：与 build-binary 一致，manifest 标记为 linux/arm64
      # ...其他参数
```

> **注意**：`arch` 必须前后一致
> binary workflow 用 arm64 交叉编译，docker workflow 也必须传 arm64，否则 manifest 架构标记与二进制实际架构不匹配，运行时会崩溃

---
