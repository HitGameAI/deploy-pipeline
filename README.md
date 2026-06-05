# deploy-pipeline

可复用的 GitHub Actions 工作流集合，用于 Go 项目的 CI/CD 和 Kubernetes 部署

## 目录结构

```bash
deploy-pipeline/
├── .github/workflows/       # 可复用工作流
│   ├── build-binary.yml     # 编译 Go 二进制
│   ├── build-docker.yml     # 构建推送 Docker 镜像
│   ├── code-quality.yml     # 代码质量检查
│   ├── cleanup-images.yml   # GHCR 旧镜像清理
│   └── deploy-k8s.yml       # K8s 部署
└── scripts/                 # 公共脚本
    ├── build-linux.sh       # Go 构建脚本
    ├── common.sh            # 通用函数库
    └── notify.sh            # 飞书/Telegram 通知
```

## 工作流列表

| 工作流 | 文件 | 说明 |
|--------|------|------|
| Code Quality | `code-quality.yml` | 代码格式检查、静态分析、单元测试 |
| Build Binary | `build-binary.yml` | 编译 Go 二进制文件（支持 UPX 压缩） |
| Build Docker | `build-docker.yml` | 构建并推送 Docker 镜像 |
| Deploy K8s | `deploy-k8s.yml` | 部署到 Kubernetes（Deployment + HPA + Service + Ingress + ConfigMap） |
| Cleanup Images | `cleanup-images.yml` | 清理 GHCR 旧版本镜像 |

## 快速接入

在你的项目 `.github/workflows/pipeline.yml` 中引用：

```yaml
jobs:
  code-quality:
    uses: kamalyes/deploy-pipeline/.github/workflows/code-quality.yml@master
    with:
      go-version: '1.25.1'
      gotestsum-version: 'v1.13.0'
    secrets: inherit

  build-binary:
    uses: kamalyes/deploy-pipeline/.github/workflows/build-binary.yml@master
    with:
      go-version: '1.25.1'
      binary-name: 'your-service'
      binary-source-dir: 'deployments'
      binary-output: 'deployments/your-service'
      version: 'v1.0.0'
      build-time: '2026-01-01_00:00:00'
      git-commit: 'abc12356'
    secrets: inherit

  build-docker:
    uses: kamalyes/deploy-pipeline/.github/workflows/build-docker.yml@master
    with:
      binary-name: 'your-service'
      version: 'v1.0.0'
      http-port: '8080'
      rpc-port: '9090'
      pprof-port: '6060'
      docker-registry: 'ghcr.io'
      binary-source-dir: 'deployments'
      image-base: 'ghcr.io/your-org/your-service'
      image-name: 'ghcr.io/your-org/your-service:main-abc12356'
    secrets: inherit

  deploy-k8s:
    uses: kamalyes/deploy-pipeline/.github/workflows/deploy-k8s.yml@master
    with:
      environment: 'dev'
      image-name: 'ghcr.io/your-org/your-service:main-abc12356'
      binary-source-dir: 'deployments'
      binary-name: 'your-service'
      http-port: '8080'
      rpc-port: '9090'
      pprof-port: '6060'
    secrets: inherit
```

---

## 工作流详细说明

### Code Quality

代码质量检查：gofmt + go vet + 单元测试 + 覆盖率报告

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `go-version` | string | 是 | - | Go 版本 |
| `gotestsum-version` | string | 是 | - | gotestsum 版本 |

### Build Binary

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
| `goproxy` | string | 否 | `https://goproxy.cn,direct` | Go 模块代理 |
| `goprivate` | string | 否 | `''` | 私有模块路径 |
| `os` | string | 否 | `linux` | 目标操作系统 |
| `arch` | string | 否 | `amd64` | 目标架构 |
| `upx-compress` | string | 否 | `false` | 是否启用 UPX 压缩 |
| `build-script` | string | 否 | `scripts/build-linux.sh` | 构建脚本路径 |

**所需 Secrets：**

- `GIT_SSH_PRIVATE_KEY`（当 `goprivate` 非空时需要）

### Build Docker

构建 Docker 镜像并推送到镜像仓库

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `binary-name` | string | 是 | - | 二进制文件名 |
| `version` | string | 是 | - | 版本号 |
| `http-port` | string | 是 | - | HTTP 端口 |
| `rpc-port` | string | 是 | - | gRPC 端口 |
| `pprof-port` | string | 是 | - | Pprof 端口 |
| `docker-registry` | string | 是 | - | 镜像仓库地址 |
| `binary-source-dir` | string | 是 | - | 二进制文件目录 |
| `image-base` | string | 是 | - | 基础镜像名（不带 tag） |
| `image-name` | string | 是 | - | 完整镜像名（带 tag） |
| `docker-run-workdir` | string | 否 | `/usr/local/services` | 容器工作目录 |
| `environment` | string | 否 | `dev` | 部署环境 |
| `base-image` | string | 否 | `alpine:3.19.1` | 基础构建镜像 |
| `timezone` | string | 否 | `Asia/Shanghai` | 容器时区 |

**所需 Secrets：**

- `DOCKER_USERNAME`（可选，默认 `github.actor`）
- `DOCKER_PASSWORD`（可选，默认 `GITHUB_TOKEN`）

### Deploy K8s

部署到 Kubernetes 集群，自动生成 Deployment + Service + HPA + Ingress + ConfigMap

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `environment` | string | 是 | - | 部署环境（用作 K8s namespace） |
| `image-name` | string | 是 | - | 完整镜像名（带 tag） |
| `binary-source-dir` | string | 是 | - | 部署清单输出目录 |
| `binary-name` | string | 是 | - | 服务名 |
| `http-port` | string | 是 | - | HTTP 端口 |
| `rpc-port` | string | 是 | - | gRPC 端口 |
| `pprof-port` | string | 是 | - | Pprof 端口 |
| `config-yaml` | string | 否 | `''` | 配置文件路径（为空则不创建 ConfigMap） |
| `configmap-name` | string | 否 | `''` | ConfigMap 名称 |
| `docker-workdir` | string | 否 | `/usr/local/services` | 容器工作目录 |
| `image-pull-secrets` | string | 否 | `kamalyes.ghcr.io` | 镜像拉取密钥 |
| `readiness-delay-seconds` | string | 否 | `5` | 就绪探针初始延迟 |
| `readiness-period-seconds` | string | 否 | `10` | 就绪探针检查周期 |
| `liveness-delay-seconds` | string | 否 | `15` | 存活探针初始延迟 |
| `liveness-period-seconds` | string | 否 | `20` | 存活探针检查周期 |
| `cpu-request` | string | 否 | `300m` | CPU 请求 |
| `memory-request` | string | 否 | `250Mi` | 内存请求 |
| `cpu-limit` | string | 否 | `600m` | CPU 限制 |
| `memory-limit` | string | 否 | `500Mi` | 内存限制 |
| `min-replicas` | string | 否 | `1` | HPA 最小副本数 |
| `max-replicas` | string | 否 | `10` | HPA 最大副本数 |
| `deployment-replicas` | string | 否 | `1` | 初始副本数 |
| `target-cpu-utilization` | string | 否 | `80` | CPU 使用率阈值 |
| `target-memory-utilization` | string | 否 | `80` | 内存使用率阈值 |
| `scale-up-percent` | string | 否 | `100` | 扩容最大百分比 |
| `scale-down-percent` | string | 否 | `50` | 缩容最大百分比 |
| `scale-down-stabilization-seconds` | string | 否 | `300` | 缩容稳定窗口（秒） |
| `enable-hpa` | string | 否 | `true` | 是否启用 HPA |
| `ingress-path-prefix` | string | 否 | `''` | Ingress 路径前缀（为空则不创建 Ingress） |
| `ingress-entry-point` | string | 否 | `web` | Ingress 入口点 |
| `notification-provider` | string | 否 | `none` | 通知方式（none/feishu/telegram/all） |

**所需 Secrets：**

- `KUBECONFIG`（必需，K8s 集群访问凭证）
- `FEISHU_WEBHOOK_URL`（feishu/all 通知时需要）
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`（telegram/all 通知时需要）

### Cleanup Images

清理 GHCR 旧版本镜像

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project-owner` | string | 是 | - | GitHub 组织/用户名 |
| `image-name` | string | 是 | - | 镜像名（包名） |
| `keep-count` | string | 否 | `5` | 保留版本数 |

---

## K8s 部署架构

```
┌─────────────────────────────────────────────┐
│                  Namespace                    │
│              (e.g. dev, prod)                 │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │           ConfigMap (optional)        │   │
│  │   gateway-core-{env}.yaml            │   │
│  └──────────────┬───────────────────────┘   │
│                 │ mount                       │
│  ┌──────────────▼───────────────────────┐   │
│  │           Deployment                  │   │
│  │  ┌────────────────────────────────┐  │   │
│  │  │  Container                      │  │   │
│  │  │  - HTTP  (8182)                 │  │   │
│  │  │  - gRPC  (9181)                 │  │   │
│  │  │  - Pprof (6160)                 │  │   │
│  │  │  - Readiness/Liveness Probe     │  │   │
│  │  │  - Resource Limits              │  │   │
│  │  └────────────────────────────────┘  │   │
│  └──────────────────────────────────────┘   │
│                 │                             │
│  ┌──────────────▼───────────────────────┐   │
│  │           Service (ClusterIP)         │   │
│  │  http:8182  rpc:9181  pprof:6160      │   │
│  └──────────────────────────────────────┘   │
│                 │                             │
│  ┌──────────────▼───────────────────────┐   │
│  │           HPA (optional)              │   │
│  │  CPU/Memory → auto scale 1~10 pods    │   │
│  └──────────────────────────────────────┘   │
│                 │                             │
│  ┌──────────────▼───────────────────────┐   │
│  │     IngressRoute (optional, Traefik)  │   │
│  │  PathPrefix → StripPrefix → Service   │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

---

## 公共脚本

### build-linux.sh

Go 二进制构建脚本，支持参数化构建

```bash
bash scripts/build-linux.sh \
  --version "v1.0.0" \
  --build-time "2026-01-01_00:00:00" \
  --git-commit "abc12356" \
  --binary-name "your-service" \
  --output-dir "deployments" \
  --os linux --arch amd64 \
  --upx-compress false
```

### notify.sh

通用通知脚本，支持飞书和 Telegram通过环境变量配置：

```bash
export NOTIFICATION_PROVIDER=feishu  # feishu | telegram | all | none
export NOTIFICATION_TYPE=deployment  # deployment | rollback
export STATUS=success                # success | failure
export ENVIRONMENT=dev
export BINARY_NAME=your-service
export WORKFLOW_URL=https://github.com/...
bash scripts/notify.sh
```

### common.sh

通用函数库，提供日志、命令执行、环境检查等工具函数被 `notify.sh` 引用

---

## 工作流脚本引用机制

公共 workflow 通过 `actions/checkout` 额外检出本仓库来获取脚本：

- **build-binary.yml**: 优先使用项目自带 `scripts/build-linux.sh`，不存在则使用公共仓库的
- **deploy-k8s.yml**: 通知功能使用公共仓库的 `scripts/notify.sh`

项目如需自定义构建逻辑，只需在项目中保留自己的 `scripts/build-linux.sh` 即可
