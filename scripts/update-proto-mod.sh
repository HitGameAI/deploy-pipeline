#!/usr/bin/env bash
# 下游仓库手动升级 proto 模块引用的核心脚本
# 由 deploy-pipeline .github/actions/update-proto-mod/action.yml 调用
# 用法: update-proto-mod.sh <目标分支> <模块名> <模块版本>
# 依赖 env:
#   PROTO_READ_TOKEN  - 可读取 proto 仓库的 token（GitHub App token）
#   PUSH_TOKEN        - 推送当前仓库的 token（通常为 GITHUB_TOKEN）
#   GITHUB_REPOSITORY - 当前仓库（owner/repo，Actions 注入）
set -euo pipefail

BRANCH="${1:?用法: update-proto-mod.sh <目标分支> <模块名> <模块版本>}"
MODULE="${2:?缺少模块名参数}"
MODULE_REF="${3:-master}"

ORG="${GITHUB_REPOSITORY%%/*}"

echo "🔄 升级 github.com/${ORG}/${MODULE}@${MODULE_REF} → ${GITHUB_REPOSITORY}@${BRANCH}"

git config --global url."https://x-access-token:${PROTO_READ_TOKEN}@github.com/".insteadOf "https://github.com/"
export GOPRIVATE="github.com/${ORG}/*"

if ! go get "github.com/${ORG}/${MODULE}@${MODULE_REF}"; then
  echo "❌ go get github.com/${ORG}/${MODULE}@${MODULE_REF} 失败"
  exit 1
fi
go mod tidy

if git diff --quiet; then
  echo "✅ ${MODULE} 引用已是最新，无需更新"
  echo "updated=false" >> "$GITHUB_OUTPUT"
  echo "summary=✅ ${MODULE} 引用已是最新，无需更新（分支 ${BRANCH}）" >> "$GITHUB_OUTPUT"
  exit 0
fi

echo "📋 变更内容："
git diff --stat

if ! go build ./...; then
  echo "⚠️ go build 失败，跳过推送（需人工适配接口变更后重跑）"
  exit 2
fi

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git commit --quiet -am "📦 deps: 手动升级 ${MODULE} 至 ${MODULE_REF} 最新（update-proto-mod）"
# insteadOf 会劫持 github.com 的 URL，push 需显式携带本仓库凭证
git push --quiet "https://x-access-token:${PUSH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "HEAD:${BRANCH}"
echo "🚀 已推送升级 commit 到 ${BRANCH}"
echo "updated=true" >> "$GITHUB_OUTPUT"
echo "summary=🚀 ${MODULE} 已升级至 ${MODULE_REF} 最新并推送（分支 ${BRANCH}）" >> "$GITHUB_OUTPUT"
