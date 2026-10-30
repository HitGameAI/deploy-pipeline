#!/bin/bash
# ============================================================================
# 服务流水线变量计算 | Compute a service pipeline's variables
#
# 这段逻辑原先是每个服务仓库 pipeline.yml 里的 setup 步骤，三个仓库各 158 行、
# 逐字节相同，只差一个默认 Secret 名。搬到这里之后调用方只写真正动态的东西
# （触发条件、入口包、手动开关），其余由配置表 service 节驱动。
#
# 算出来的东西
#   1. 产物与镜像：名字、版本、镜像名、镜像基址、构建时间、commit
#   2. 部署决策：这次要不要构建镜像、要不要部署（push / dispatch / tag 三种触发各有规则）
#   3. 配置与启动：ConfigMap 名、配置文件路径、写进镜像 CMD 的启动参数
#   4. 容器 env 列表：明文变量来自配置表 env 节，密钥名从源码与服务配置里自动抽，
#      渲染成 secretKeyRef（值只在集群的 Secret 里，不经流水线明文）
#
# 入参全走环境变量：大部分由 resolve-config 从聚合配置里解析好写进 GITHUB_ENV，
# 剩下几个按次运行才知道的由 resolve-service-vars action 的入参传进来。
# 结果写 $GITHUB_OUTPUT。
#
# [EN] This logic used to live in each service repository's setup step, 158 byte-identical lines
# per repository differing only in a default secret name. Having it here means a caller writes
# only what is genuinely dynamic while the rest is driven by the service section of the
# configuration table. It computes the artifact and image identity, the build/deploy decisions
# for push, dispatch and tag triggers, the ConfigMap and container arguments, and the container
# env list: plaintext values from the env section, secret names derived from the source and the
# service config and rendered as secretKeyRef so no secret passes through the pipeline.
# ============================================================================
set -euo pipefail

# ---------- 入参 | Inputs ----------
BINARY_NAME="${BINARY_NAME:-}"
BINARY_SOURCE_DIR="${BINARY_SOURCE_DIR:-deployments}"
DOCKER_REGISTRY="${DOCKER_REGISTRY:-ghcr.io}"
DOCKER_NAMESPACE="${DOCKER_NAMESPACE:-}"
ALLOWED_ENVS="${ALLOWED_ENVS:-}"
DEPLOY_BRANCHES="${DEPLOY_BRANCHES:-}"
CONFIG_PATH_TEMPLATE="${CONFIG_PATH_TEMPLATE:-}"
CMD_ARGS_TEMPLATE="${CMD_ARGS_TEMPLATE:-}"
# 带花括号的默认值不能写进 ${VAR:-默认} —— 默认值里的 } 会被当成展开式的结尾，
# 于是 "{name}-secrets" 被切成 "{name" + 字面量 "-secrets}"，拼出来的名字是错的
# [EN] A default containing braces cannot go inside ${VAR:-default}: the closing brace of
# {name} terminates the expansion, so "{name}-secrets" is cut into "{name" plus a literal
# "-secrets}" and the resulting name is wrong
CONFIGMAP_NAME_TEMPLATE="${CONFIGMAP_NAME_TEMPLATE:-}"
[ -n "${CONFIGMAP_NAME_TEMPLATE}" ] || CONFIGMAP_NAME_TEMPLATE='{name}-config-{env}'
K8S_SECRET_NAME="${K8S_SECRET_NAME:-}"
[ -n "${K8S_SECRET_NAME}" ] || K8S_SECRET_NAME='{name}-secrets'
SECRET_SCAN_DIRS="${SECRET_SCAN_DIRS:-internal}"
ENABLE_HPA="${ENABLE_HPA:-true}"
PIPELINE_ENV_JSON="${PIPELINE_ENV_JSON:-}"

SERVICE_ENVIRONMENT="${SERVICE_ENVIRONMENT:-}"
SERVICE_BUILD_DOCKER="${SERVICE_BUILD_DOCKER:-}"
SERVICE_DEPLOY_K8S="${SERVICE_DEPLOY_K8S:-}"
SERVICE_CODE_QUALITY="${SERVICE_CODE_QUALITY:-false}"
SERVICE_NOTIFICATION_PROVIDER="${SERVICE_NOTIFICATION_PROVIDER:-}"

out() { echo "$1=$2" >> "${GITHUB_OUTPUT:-/dev/stdout}"; }

# 仓库名与组织名从 GITHUB_REPOSITORY 取，调用方不用传
# [EN] The repository and owner names come from GITHUB_REPOSITORY, so a caller passes neither
REPO_NAME="${GITHUB_REPOSITORY##*/}"
REPO_OWNER="${GITHUB_REPOSITORY%%/*}"
[ -n "${BINARY_NAME}" ] || BINARY_NAME="${REPO_NAME}"
[ -n "${DOCKER_NAMESPACE}" ] || DOCKER_NAMESPACE="${REPO_OWNER}"

BRANCH="${GITHUB_REF_NAME:-}"
SHORT_COMMIT="$(git rev-parse --short HEAD)"
TIMESTAMP="$(date -u '+%Y%m%d%H%M%S')"
BUILD_TIME="$(date -u '+%Y-%m-%d_%H:%M:%S')"
DEPLOY_ENV="${SERVICE_ENVIRONMENT:-dev}"

# 模板展开：{name} 服务名、{env} 环境、{configfile} 配置文件名、{repo} 仓库名
# [EN] Expand the template placeholders
expand() {
  printf '%s' "$1" \
    | sed -e "s|{name}|${BINARY_NAME}|g" \
          -e "s|{env}|${DEPLOY_ENV}|g" \
          -e "s|{repo}|${REPO_NAME}|g" \
          -e "s|{configfile}|${2:-}|g"
}

K8S_SECRET_NAME="$(expand "${K8S_SECRET_NAME}")"

echo "::group::⚙️ 服务变量 | Service variables"
echo "服务        : ${BINARY_NAME}  (仓库 ${GITHUB_REPOSITORY})"
echo "部署环境    : ${DEPLOY_ENV}"
echo "分支 / 事件 : ${BRANCH} / ${GITHUB_EVENT_NAME:-}"

# ---------- 1. 环境白名单 | Environment allow list ----------
# 不在名单里的环境只跑检查，不构建镜像也不部署
# [EN] An environment outside the allow list runs checks only, with no image and no deployment
IS_ALLOWED="false"
if [ -z "${ALLOWED_ENVS}" ]; then
  IS_ALLOWED="true"
else
  for e in $(printf '%s' "${ALLOWED_ENVS}" | tr ',;' ' '); do
    [ "$(echo "$e" | xargs)" = "${DEPLOY_ENV}" ] && IS_ALLOWED="true" && break
  done
fi
echo "环境白名单  : ${ALLOWED_ENVS:-<未设置，全部允许>} → ${DEPLOY_ENV} $([ "${IS_ALLOWED}" = true ] && echo 允许 || echo 不允许)"

# ---------- 2. 配置文件与 ConfigMap | Config file and ConfigMap ----------
# 配置文件不存在就直接失败，而不是让部署工作流静默跳过 ConfigMap ——
# 那样服务会起来但读不到配置，排查成本远高于在这里挡住。
# config_path 配成空表示这个服务没有配置文件，整块跳过。
# [EN] A missing config file fails here instead of letting the deploy workflow silently skip the
# ConfigMap, which would start the service with no config and cost far more to debug. An empty
# config_path means the service has no config file and skips this block.
K8S_CONFIG_YAML=""
K8S_CONFIGMAP_NAME=""
CMD_ARGS=""
if [ -n "${CONFIG_PATH_TEMPLATE}" ]; then
  K8S_CONFIG_YAML="$(expand "${CONFIG_PATH_TEMPLATE}")"
  if [ ! -f "${K8S_CONFIG_YAML}" ]; then
    echo "::error::缺少配置文件 ${K8S_CONFIG_YAML}，无法为 ${DEPLOY_ENV} 生成 ConfigMap | config file not found"
    exit 1
  fi
  K8S_CONFIGMAP_NAME="$(expand "${CONFIGMAP_NAME_TEMPLATE}")"
  # 容器启动参数：ConfigMap 挂在 <工作目录>/resources/ 下、文件名不变，
  # 而 Dockerfile 的 WORKDIR 就是工作目录，所以相对路径即可
  # [EN] The ConfigMap mounts under <workdir>/resources/ keeping the file name, and the
  # Dockerfile's WORKDIR is that workdir, so a relative path works
  [ -n "${CMD_ARGS_TEMPLATE}" ] && CMD_ARGS="$(expand "${CMD_ARGS_TEMPLATE}" "$(basename "${K8S_CONFIG_YAML}")")"
  echo "配置文件    : ${K8S_CONFIG_YAML} → ConfigMap ${K8S_CONFIGMAP_NAME}"
  echo "启动参数    : ${CMD_ARGS:-<无>}"
else
  echo "配置文件    : <service.config_path 为空，跳过 ConfigMap>"
fi

# ---------- 3. 密钥环境变量清单 | Secret environment variables ----------
# 从源码与配置里自动抽，不写死清单。两套读环境变量的机制都要覆盖：
#   1. go-zero 的 `,env=XXX` struct tag —— 环境变量优先于 yaml 里的值
#   2. yaml 里写环境变量的「名字」（*Env: 结尾的配置项），代码再 os.Getenv 取值
# 自动抽而不是维护清单：加了新的密钥配置，下次部署自动带上，不会漏。
#
# [EN] Derived from the source and the config rather than hardcoded, covering both ways the
# service reads the environment: go-zero's `,env=XXX` struct tag, where the environment wins over
# the yaml value, and a yaml setting whose name ends in Env holding the name of an environment
# variable that the code resolves with os.Getenv. Deriving it means a newly added secret is
# injected automatically on the next deploy instead of being forgotten.
scan_dirs=""
for d in $(printf '%s' "${SECRET_SCAN_DIRS}" | tr ',;' ' '); do
  [ -d "${d}" ] && scan_dirs="${scan_dirs} ${d}"
done
SECRET_ENV_NAMES="$(
  {
    [ -n "${scan_dirs}" ] && grep -rhoE ',env=[A-Z_][A-Z0-9_]*' --include='*.go' ${scan_dirs} 2>/dev/null | sed 's/,env=//'
    [ -n "${K8S_CONFIG_YAML}" ] && grep -oE "^[[:space:]]*[A-Za-z]+Env:[[:space:]]*[A-Z_][A-Z0-9_]*" "${K8S_CONFIG_YAML}" 2>/dev/null | awk '{print $NF}'
    true
  } | sort -u | grep . || true
)"
SECRET_COUNT="$(printf '%s' "${SECRET_ENV_NAMES}" | grep -c . || true)"
echo "密钥变量    : ${SECRET_COUNT} 个（Secret ${K8S_SECRET_NAME}，扫${scan_dirs:- <无目录>}）"
[ -n "${SECRET_ENV_NAMES}" ] && printf '%s\n' "${SECRET_ENV_NAMES}" | sed 's/^/              /'

# ---------- 4. 容器 env 列表 | Container env list ----------
# 明文变量（配置表 env 节）走 value:，密钥走 valueFrom.secretKeyRef。
# optional: true 的用意：Secret 还没建好时 Pod 仍能启动，由服务自己报缺哪个密钥，
# 比 Pod 卡在 CreateContainerConfigError 更容易定位。
# Secret 的 key 就用环境变量名本身，建 Secret 时 --from-literal=<名字>=<值> 即可。
# 两边撞名时以密钥为准：明文覆盖密钥等于把密钥写进 manifest。
#
# [EN] Plaintext values from the env section use value:, secrets use valueFrom.secretKeyRef.
# optional: true lets the Pod start before the Secret exists so the service itself reports which
# secret is missing, which is easier to diagnose than a Pod stuck in CreateContainerConfigError.
# The Secret key is the variable name itself. On a name collision the secret wins, since letting
# a plaintext entry win would write the secret into the manifest.
ENV_BLOCK="$(
  SECRET_ENV_NAMES="${SECRET_ENV_NAMES}" \
  SECRET_NAME="${K8S_SECRET_NAME}" \
  PIPELINE_ENV_JSON="${PIPELINE_ENV_JSON}" \
  python3 -I - <<'PY'
import json, os, sys

secret_names = [n for n in os.environ.get("SECRET_ENV_NAMES", "").split("\n") if n.strip()]
secret_name = os.environ.get("SECRET_NAME", "")
try:
    plain = json.loads(os.environ.get("PIPELINE_ENV_JSON") or "{}")
except json.JSONDecodeError as exc:
    print("::error::PIPELINE_ENV_JSON 不是合法 JSON（%s）" % exc, file=sys.stderr)
    sys.exit(1)

lines = []
for name in sorted(plain):
    if name in secret_names:
        print("::warning::env.%s 与自动抽出的密钥同名，按密钥处理（明文那份忽略）"
              % name, file=sys.stderr)
        continue
    value = plain[name]
    if isinstance(value, bool):
        value = "true" if value else "false"
    # 一律加引号：数字、true、yes 这些在 YAML 里不加引号会变成非字符串，
    # 而 K8s 的 env value 必须是字符串，不加引号 apply 会直接被拒
    # [EN] Always quote: a bare number or true/yes is not a string in YAML, and a K8s env value
    # must be a string, so an unquoted one is rejected on apply
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    lines.append('- name: %s\n  value: "%s"' % (name, text))

for name in secret_names:
    lines.append("- name: %s\n  valueFrom:\n    secretKeyRef:\n      name: %s\n      key: %s\n"
                 "      optional: true" % (name, secret_name, name))

print("\n".join(lines))
PY
)"

# ---------- 5. 构建与部署决策 | Build and deploy decisions ----------
IS_TAG_PUSH="false"
case "${GITHUB_REF:-}" in refs/tags/*) IS_TAG_PUSH="true" ;; esac

SHOULD_BUILD_DOCKER="${SERVICE_BUILD_DOCKER}"
SHOULD_DEPLOY_K8S="${SERVICE_DEPLOY_K8S}"

# push 事件没有 dispatch 输入：一律构建镜像，但只有名单里的分支才部署
# [EN] A push carries no dispatch inputs: always build the image, but deploy only from the
# branches on the list
if [ "${GITHUB_EVENT_NAME:-}" = "push" ]; then
  SHOULD_BUILD_DOCKER="true"
  SHOULD_DEPLOY_K8S="false"
  for b in $(printf '%s' "${DEPLOY_BRANCHES}" | tr ',;' ' '); do
    [ "$(echo "$b" | xargs)" = "${BRANCH}" ] && SHOULD_DEPLOY_K8S="true" && break
  done
fi

if [ "${IS_ALLOWED}" = "false" ]; then
  SHOULD_BUILD_DOCKER="false"
  SHOULD_DEPLOY_K8S="false"
elif [ "${SHOULD_DEPLOY_K8S}" = "true" ]; then
  # 要部署就必须有镜像
  # [EN] Deploying requires an image
  SHOULD_BUILD_DOCKER="true"
fi

# 发布模式（推 tag 触发）：只构建推送镜像，跳过部署
# [EN] Release mode (triggered by a tag push): build and push the image only, no deploy
if [ "${IS_TAG_PUSH}" = "true" ]; then
  SHOULD_BUILD_DOCKER="true"
  SHOULD_DEPLOY_K8S="false"
fi

echo "构建/部署   : docker=${SHOULD_BUILD_DOCKER} k8s=${SHOULD_DEPLOY_K8S} release=${IS_TAG_PUSH} hpa=${ENABLE_HPA}"

# ---------- 6. 版本与镜像 | Version and image ----------
IMAGE_BASE="${DOCKER_REGISTRY}/${DOCKER_NAMESPACE}/${BINARY_NAME}"
if [ "${IS_TAG_PUSH}" = "true" ]; then
  VERSION="${BRANCH}"
  IMAGE_NAME="${IMAGE_BASE}:${BRANCH}"
else
  VERSION="${BRANCH}-${SHORT_COMMIT}"
  IMAGE_NAME="${IMAGE_BASE}:${BRANCH}-${SHORT_COMMIT}-${TIMESTAMP}"
fi
echo "镜像        : ${IMAGE_NAME}"
echo "::endgroup::"

# ---------- 7. 输出 | Outputs ----------
out binary-name "${BINARY_NAME}"
out binary-source-dir "${BINARY_SOURCE_DIR}"
out binary-output "${BINARY_SOURCE_DIR}/${BINARY_NAME}"
out binary-build-dir "${BINARY_BUILD_DIR:-}"
out version "${VERSION}"
out build-time "${BUILD_TIME}"
out git-commit "${SHORT_COMMIT}"
out image-base "${IMAGE_BASE}"
out image-name "${IMAGE_NAME}"
out release-mode "${IS_TAG_PUSH}"
out deploy-environment "${DEPLOY_ENV}"
out should-build-docker "${SHOULD_BUILD_DOCKER}"
out should-deploy-k8s "${SHOULD_DEPLOY_K8S}"
out code-quality "${SERVICE_CODE_QUALITY}"
# 调用方没传渠道（push 事件没有 dispatch 输入）就回退到配置里的 notify.provider
# [EN] With no channel passed, as on a push that carries no dispatch inputs, fall back to
# notify.provider from the configuration
out notification-provider "${SERVICE_NOTIFICATION_PROVIDER:-${NOTIFICATION_PROVIDER:-}}"
out k8s-config-yaml "${K8S_CONFIG_YAML}"
out k8s-configmap-name "${K8S_CONFIGMAP_NAME}"
out k8s-enable-hpa "${ENABLE_HPA}"
out k8s-secret-name "${K8S_SECRET_NAME}"
out cmd-args "${CMD_ARGS}"
out extra-copy-dirs "${EXTRA_COPY_DIRS:-}"
# 多行值走 heredoc；分隔符带随机串，免得正好撞上值里的某一行
# [EN] A multiline value needs the heredoc form, with a random delimiter that cannot collide
delim="SERVICE_VARS_EOF_$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')"
{
  echo "extra-env-block<<${delim}"
  printf '%s\n' "${ENV_BLOCK}"
  echo "${delim}"
} >> "${GITHUB_OUTPUT:-/dev/stdout}"
