#!/usr/bin/env bash
# 分支工程化脚本（周节奏 SOP 自动化）
# 模式:
#   recycle - dev 回收重切（前置检查: 今日发布 tag + dev 无未发布内容 → 删 dev → 从 master 重切）
#             + 已完全合入 master 的 feature/* 分支清理
#   cut     - 从指定基分支批量切新分支（新迭代启动: 从最新 master 切 feature/xx）
# 由 deploy-pipeline .github/workflows/branch-recycle.yml / branch-cut.yml 调用
#
# 公共 env:
#   GH_TOKEN                  - API token（中心批量模式用跨仓库 PAT/App token；单仓库调用 GITHUB_TOKEN 即可）
#   BRANCH_OPS_REPOS          - 逗号分隔仓库列表（owner/repo），留空=当前仓库
#   BRANCH_OPS_DRY_RUN        - 干跑模式（true 只输出动作清单不执行，默认 true）
# recycle 专属 env:
#   BRANCH_OPS_SOURCE         - 源分支（默认 master）
#   BRANCH_OPS_TARGET         - 回收目标分支（默认 dev）
#   BRANCH_OPS_FEATURE_PREFIX - feature 分支前缀（默认 feature/）
#   BRANCH_OPS_CLEAN_FEATURES - 是否清理已合并 feature（true/false，默认 true）
#   BRANCH_OPS_REQUIRE_TAG    - 是否要求今日存在 prod-YYYY.MM.DD* tag 才回收（true/false，默认 false）
# cut 专属 env:
#   BRANCH_OPS_NEW_BRANCH     - 新分支名（必填，如 feature/xxx）
#   BRANCH_OPS_BASE           - 基分支（默认 master）
set -uo pipefail

MODE="${1:?用法: branch-ops.sh <recycle|cut>}"
DRY_RUN="${BRANCH_OPS_DRY_RUN:-true}"

REPOS="${BRANCH_OPS_REPOS:-}"
if [ -z "$REPOS" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
  REPOS="${GITHUB_REPOSITORY}"
fi
if [ -z "$REPOS" ]; then
  echo "❌ 未指定仓库列表（BRANCH_OPS_REPOS）且不在 Actions 环境"
  exit 1
fi

DRY_TAG=""
[ "$DRY_RUN" = "true" ] && DRY_TAG="[DRY-RUN] "

api() { gh api -H "Accept: application/vnd.github+json" "$@"; }

get_sha() {
  api "/repos/$1/git/ref/heads/$2" --jq '.object.sha' 2>/dev/null || echo ""
}

compare() {
  api "/repos/$1/compare/$2...$3" --jq '.status' 2>/dev/null || echo "MISSING"
}

delete_branch() {
  api --method DELETE "/repos/$1/git/ref/heads/$2" 2>/dev/null
}

create_branch() {
  api --method POST "/repos/$1/git/refs" -f ref="refs/heads/$2" -f sha="$3" 2>/dev/null
}

TOTAL_OK=0
TOTAL_SKIP=0
TOTAL_FAIL=0
SUMMARY_LINES=""

add_summary() {
  SUMMARY_LINES="${SUMMARY_LINES}- **${1}**：${2}"$'\n'
}

# ============================================================
# recycle 模式: dev 回收重切 + 已合并 feature 清理
# ============================================================
do_recycle() {
  local SOURCE="${BRANCH_OPS_SOURCE:-master}"
  local TARGET="${BRANCH_OPS_TARGET:-dev}"
  local FEATURE_PREFIX="${BRANCH_OPS_FEATURE_PREFIX:-feature/}"
  local CLEAN_FEATURES="${BRANCH_OPS_CLEAN_FEATURES:-true}"
  local REQUIRE_TAG="${BRANCH_OPS_REQUIRE_TAG:-false}"
  local TODAY_TAG_PREFIX="prod-$(date +%Y.%m.%d)"

  echo "================================================"
  echo "  ${DRY_TAG}dev 回收重切 + feature 清理"
  echo "  模式: $([ "$DRY_RUN" = "true" ] && echo "干跑（预告，未执行）" || echo "执行")"
  echo "  ${SOURCE} → 重切 ${TARGET}；feature 清理: ${CLEAN_FEATURES}"
  echo "  今日 tag 检查: ${REQUIRE_TAG}（${TODAY_TAG_PREFIX}*）"
  echo "  仓库: ${REPOS}"
  echo "================================================"

  for REPO in $(echo "$REPOS" | tr ',' ' '); do
    REPO=$(echo "$REPO" | xargs)
    [ -z "$REPO" ] && continue
    echo ""
    echo "📦 处理仓库: ${REPO}"

    # ---- 前置检查 1: 源分支存在 ----
    local SOURCE_SHA
    SOURCE_SHA=$(get_sha "$REPO" "$SOURCE")
    if [ -z "$SOURCE_SHA" ]; then
      echo "  ❌ 源分支 ${SOURCE} 不存在，跳过"
      add_summary "$REPO" "❌ 跳过（源分支 ${SOURCE} 不存在）"
      TOTAL_FAIL=$((TOTAL_FAIL + 1))
      continue
    fi
    echo "  ${SOURCE} @ ${SOURCE_SHA:0:8}"

    # ---- 前置检查 2: 今日发布 tag（require-tag 时）----
    if [ "$REQUIRE_TAG" = "true" ]; then
      local TODAY_TAG
      TODAY_TAG=$(api "/repos/$REPO/tags?per_page=100" --jq '.[].name' 2>/dev/null | grep "^${TODAY_TAG_PREFIX}" | head -1)
      if [ -z "$TODAY_TAG" ]; then
        echo "  ⚠️ 今日无 ${TODAY_TAG_PREFIX}* tag（发布未完成），跳过回收"
        add_summary "$REPO" "⚠️ 跳过（今日无 ${TODAY_TAG_PREFIX}* tag，发布未完成）"
        TOTAL_SKIP=$((TOTAL_SKIP + 1))
        continue
      fi
      echo "  今日 tag: ${TODAY_TAG} ✅"
    fi

    # ---- 前置检查 3 + 回收: dev 状态判定 ----
    local RECYCLE_NOTE=""
    local TARGET_STATUS
    TARGET_STATUS=$(compare "$REPO" "$SOURCE" "$TARGET")
    case "$TARGET_STATUS" in
      identical|behind)
        # dev 无未发布内容（一致或落后于 master），安全回收
        RECYCLE_NOTE="${TARGET} 状态 ${TARGET_STATUS}，无未发布内容"
        if [ "$DRY_RUN" = "true" ]; then
          echo "  ${DRY_TAG}将执行: 删 ${TARGET} → 从 ${SOURCE}@${SOURCE_SHA:0:8} 重切"
        else
          if delete_branch "$REPO" "$TARGET" && create_branch "$REPO" "$TARGET" "$SOURCE_SHA"; then
            echo "  ✅ ${TARGET} 已回收重切 @ ${SOURCE_SHA:0:8}"
          else
            echo "  ❌ ${TARGET} 回收失败"
            add_summary "$REPO" "❌ ${TARGET} 回收失败（API 执行失败）"
            TOTAL_FAIL=$((TOTAL_FAIL + 1))
            continue
          fi
        fi
        ;;
      ahead|diverged)
        # dev 有未合入 master 的内容（其他项目未发布变更），拒绝回收
        local AHEAD_BY
        AHEAD_BY=$(api "/repos/$REPO/compare/${SOURCE}...${TARGET}" --jq '.ahead_by' 2>/dev/null || echo "?")
        echo "  ⚠️ 拒绝回收: ${TARGET} 领先 ${SOURCE} ${AHEAD_BY} 个提交（存在未发布内容）"
        RECYCLE_NOTE="拒绝: ${TARGET} 领先 ${AHEAD_BY} 提交未进 ${SOURCE}，先合入或迁移再回收"
        add_summary "$REPO" "⚠️ ${RECYCLE_NOTE}"
        TOTAL_SKIP=$((TOTAL_SKIP + 1))
        continue
        ;;
      MISSING)
        # dev 不存在（首次接入或已回收），直接从 master 建
        RECYCLE_NOTE="${TARGET} 原不存在，从 ${SOURCE} 新建"
        if [ "$DRY_RUN" = "true" ]; then
          echo "  ${DRY_TAG}将执行: ${TARGET} 不存在，从 ${SOURCE}@${SOURCE_SHA:0:8} 新建"
        else
          if create_branch "$REPO" "$TARGET" "$SOURCE_SHA"; then
            echo "  ✅ ${TARGET} 已从 ${SOURCE} 新建"
          else
            echo "  ❌ ${TARGET} 新建失败"
            add_summary "$REPO" "❌ ${TARGET} 新建失败（API 执行失败）"
            TOTAL_FAIL=$((TOTAL_FAIL + 1))
            continue
          fi
        fi
        ;;
      *)
        echo "  ❌ 无法获取 ${SOURCE}...${TARGET} 状态（${TARGET_STATUS}）"
        add_summary "$REPO" "❌ 状态未知（compare 返回 ${TARGET_STATUS}）"
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
        continue
        ;;
    esac

    # ---- feature 清理: 已完全合入 master 的分支删除 ----
    local FEATURE_NOTE="feature 清理未启用"
    if [ "$CLEAN_FEATURES" = "true" ]; then
      local DELETED_LIST="" KEPT_LIST=""
      local PAGE=1 ALL_BRANCHES=""
      while true; do
        local RESPONSE
        RESPONSE=$(api "/repos/$REPO/branches?per_page=100&page=$PAGE" 2>/dev/null || echo "")
        local NAMES
        NAMES=$(echo "$RESPONSE" | jq -r '.[].name' 2>/dev/null)
        [ -z "$NAMES" ] && break
        ALL_BRANCHES="${ALL_BRANCHES}${NAMES}"$'\n'
        local COUNT
        COUNT=$(echo "$RESPONSE" | jq 'length' 2>/dev/null || echo "0")
        [ "$COUNT" -lt 100 ] 2>/dev/null && break
        PAGE=$((PAGE + 1))
      done

      for BRANCH in $(echo "$ALL_BRANCHES" | sed '/^$/d'); do
        [ "$BRANCH" = "$SOURCE" ] || [ "$BRANCH" = "$TARGET" ] && continue
        case "$BRANCH" in
          "$FEATURE_PREFIX"*)
            local F_STATUS
            F_STATUS=$(compare "$REPO" "$SOURCE" "$BRANCH")
            case "$F_STATUS" in
              identical|behind)
                if [ "$DRY_RUN" = "true" ]; then
                  echo "  ${DRY_TAG}将删除已合并分支: ${BRANCH}（${F_STATUS}）"
                else
                  if delete_branch "$REPO" "$BRANCH"; then
                    echo "  🗑️ 已删除已合并分支: ${BRANCH}"
                  else
                    echo "  ✗ 删除失败（可能为保护分支）: ${BRANCH}"
                    KEPT_LIST="${KEPT_LIST}${BRANCH}（删除失败） "
                    continue
                  fi
                fi
                DELETED_LIST="${DELETED_LIST}${BRANCH} "
                ;;
              *)
                echo "  🛡️ 保留未合并分支: ${BRANCH}（${F_STATUS}）"
                KEPT_LIST="${KEPT_LIST}${BRANCH} "
                ;;
            esac
            ;;
        esac
      done

      local DELETED_COUNT KEPT_COUNT
      DELETED_COUNT=$(echo "$DELETED_LIST" | xargs | wc -w)
      KEPT_COUNT=$(echo "$KEPT_LIST" | xargs | wc -w)
      FEATURE_NOTE="无已合并分支可清"
      if [ "$DELETED_COUNT" -gt 0 ]; then
        FEATURE_NOTE="🗑️ 清理 ${DELETED_COUNT} 个: $(echo "$DELETED_LIST" | xargs)"
      fi
      if [ "$KEPT_COUNT" -gt 0 ]; then
        FEATURE_NOTE="${FEATURE_NOTE}；保留未合并 ${KEPT_COUNT} 个: $(echo "$KEPT_LIST" | xargs)"
      fi
    fi

    add_summary "$REPO" "✅ ${TARGET} 回收重切（${RECYCLE_NOTE}）；${FEATURE_NOTE}"
    TOTAL_OK=$((TOTAL_OK + 1))
  done

  SUMMARY_TITLE="🔄 ${DRY_TAG}dev 回收汇总（${SOURCE} → 重切 ${TARGET}）"
}

# ============================================================
# cut 模式: 从基分支批量切新分支（新迭代启动）
# ============================================================
do_cut() {
  local NEW_BRANCH="${BRANCH_OPS_NEW_BRANCH:?缺少 BRANCH_OPS_NEW_BRANCH（如 feature/xxx）}"
  local BASE="${BRANCH_OPS_BASE:-master}"

  echo "================================================"
  echo "  ${DRY_TAG}批量切分支"
  echo "  模式: $([ "$DRY_RUN" = "true" ] && echo "干跑（预告，未执行）" || echo "执行")"
  echo "  ${BASE} → ${NEW_BRANCH}"
  echo "  仓库: ${REPOS}"
  echo "================================================"

  for REPO in $(echo "$REPOS" | tr ',' ' '); do
    REPO=$(echo "$REPO" | xargs)
    [ -z "$REPO" ] && continue
    echo ""
    echo "📦 处理仓库: ${REPO}"

    local BASE_SHA
    BASE_SHA=$(get_sha "$REPO" "$BASE")
    if [ -z "$BASE_SHA" ]; then
      echo "  ❌ 基分支 ${BASE} 不存在，跳过"
      add_summary "$REPO" "❌ 跳过（基分支 ${BASE} 不存在）"
      TOTAL_FAIL=$((TOTAL_FAIL + 1))
      continue
    fi

    # 幂等: 已存在则跳过
    local EXISTING_SHA
    EXISTING_SHA=$(get_sha "$REPO" "$NEW_BRANCH")
    if [ -n "$EXISTING_SHA" ]; then
      echo "  ⏭️ ${NEW_BRANCH} 已存在 @ ${EXISTING_SHA:0:8}，跳过（幂等）"
      add_summary "$REPO" "⏭️ ${NEW_BRANCH} 已存在，跳过"
      TOTAL_SKIP=$((TOTAL_SKIP + 1))
      continue
    fi

    if [ "$DRY_RUN" = "true" ]; then
      echo "  ${DRY_TAG}将执行: 从 ${BASE}@${BASE_SHA:0:8} 切 ${NEW_BRANCH}"
      add_summary "$REPO" "${DRY_TAG}将切 ${NEW_BRANCH} @ ${BASE_SHA:0:8}"
    else
      if create_branch "$REPO" "$NEW_BRANCH" "$BASE_SHA"; then
        echo "  ✅ ${NEW_BRANCH} 已创建 @ ${BASE_SHA:0:8}"
        add_summary "$REPO" "✅ ${NEW_BRANCH} 已从 ${BASE}@${BASE_SHA:0:8} 创建"
      else
        echo "  ❌ ${NEW_BRANCH} 创建失败"
        add_summary "$REPO" "❌ ${NEW_BRANCH} 创建失败"
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
        continue
      fi
    fi
    TOTAL_OK=$((TOTAL_OK + 1))
  done

  SUMMARY_TITLE="🌿 ${DRY_TAG}切分支汇总（${BASE} → ${NEW_BRANCH}）"
}

case "$MODE" in
  recycle) do_recycle ;;
  cut)     do_cut ;;
  *) echo "❌ 未知模式: ${MODE}（支持: recycle | cut）"; exit 1 ;;
esac

echo ""
echo "================================================"
echo "  ${DRY_TAG}汇总: 成功 ${TOTAL_OK}，跳过/拒绝 ${TOTAL_SKIP}，失败 ${TOTAL_FAIL}"
echo "  本地同步: git remote prune origin && git fetch origin"
echo "================================================"

SUMMARY_MD="## ${SUMMARY_TITLE}

**模式**: $([ "$DRY_RUN" = "true" ] && echo "干跑（预告，未执行——确认无误后 workflow_dispatch 以 dry-run=false 执行）" || echo "执行")
**本地同步**: 各开发执行 \`git remote prune origin && git fetch origin\`

${SUMMARY_LINES}"

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  echo "$SUMMARY_MD" >> "$GITHUB_STEP_SUMMARY"
fi
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "summary<<BRANCH_OPS_EOF"
    echo "$SUMMARY_MD"
    echo "BRANCH_OPS_EOF"
  } >> "$GITHUB_OUTPUT"
  echo "ok=${TOTAL_OK}" >> "$GITHUB_OUTPUT"
  echo "skipped=${TOTAL_SKIP}" >> "$GITHUB_OUTPUT"
  echo "failed=${TOTAL_FAIL}" >> "$GITHUB_OUTPUT"
  if [ "$TOTAL_FAIL" -gt 0 ]; then
    echo "status=failure" >> "$GITHUB_OUTPUT"
  elif [ "$TOTAL_SKIP" -gt 0 ]; then
    echo "status=warning" >> "$GITHUB_OUTPUT"
  else
    echo "status=success" >> "$GITHUB_OUTPUT"
  fi
fi

if [ "$TOTAL_FAIL" -gt 0 ]; then
  exit 1
fi
