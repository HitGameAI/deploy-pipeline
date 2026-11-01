#!/bin/bash
# ============================================================================
# 把本仓库整份同步到同一项目在另一个组织下的副本 | Sync this repository to its fork
#
# 为什么有两份：私有仓库默认不对其它仓库开放 uses: 引用，调用方会收到
# "workflow was not found"（放开办法是 Settings → Actions → General → Access）。
# 所以同一套工作流在两个组织下各留一份，调用方指向能访问的那一个。
#
# 两份内容完全相同，唯一的差别是「自引用的组织前缀」：
#   uses: <org>/deploy-pipeline/.github/...     repository: <org>/deploy-pipeline
# 文档里的示例也一样只带前缀，不带任何项目内部信息 —— 内容本身就是通用的，
# 所以同步只是「换前缀」，哪一份当源头都行，不存在一份要脱敏、一份不用的问题。
#
# 两边的组织名都从各自的 git remote 推出来，脚本里不写死任何组织名：
# 因此这个脚本在两份副本里都是同一份，反向同步就是把参数换成对面的路径。
#
# 用法
#   bash scripts/sync-fork.sh <对面仓库路径> [--check] [--force]
#     --check   只报告会改哪些文件，不落盘
#     --force   对面多出来的文件一并删掉（整份重新生成，比增量打补丁清楚）
#
# 落盘后对面仓库是普通的工作区改动，自己看 diff 再提交；历史不由这个脚本碰。
#
# [EN] Sync this repository to the copy of the same project living under another organization.
# Two copies exist because a private repository is not referenceable by other repositories by
# default, so callers point at whichever one they can reach. The copies are identical except for
# the organization prefix in their own self-references, and the documentation carries no
# project-internal identifiers either, so syncing is a prefix swap and either side can be the
# source. Both organization names are derived from the respective git remotes, so this script
# hardcodes none and is itself identical in both copies; reversing the direction just means
# pointing it at the other path.
# ============================================================================
set -euo pipefail

SELF="scripts/sync-fork.sh"

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST=""
CHECK_ONLY=""
FORCE=""
for arg in "$@"; do
  case "${arg}" in
    --check) CHECK_ONLY="1" ;;
    --force) FORCE="1" ;;
    -*)      echo "❌ 未知参数 ${arg}；只认 --check 与 --force" >&2; exit 2 ;;
    *)       DEST="${arg}" ;;
  esac
done

if [ -z "${DEST}" ]; then
  echo "用法: bash ${SELF} <对面仓库路径> [--check] [--force]" >&2
  exit 2
fi
if [ ! -d "${DEST}/.git" ]; then
  echo "❌ ${DEST} 不是 git 仓库；先把对面那份 clone 下来" >&2
  exit 1
fi
DEST="$(cd "${DEST}" && pwd)"

# 从 remote URL 里取 owner：git@github.com:owner/repo.git 或 https://github.com/owner/repo.git
# [EN] Take the owner out of the remote URL, in either the SSH or the HTTPS form
owner_of() {
  local url
  url="$(cd "$1" && git remote get-url origin 2>/dev/null || true)"
  if [ -z "${url}" ]; then
    echo "❌ $1 没有 origin remote，取不到组织名" >&2
    return 1
  fi
  printf '%s\n' "${url}" \
    | sed -E 's|^git@[^:]+:||; s|^https?://[^/]+/||; s|\.git$||' \
    | cut -d/ -f1
}

# 仓库名也要取：替换必须是 <org>/<repo> 这个整体，不能只换裸组织名
#
# 只换裸组织名会连带改掉内容里正当的同组织引用 本仓库的
# docs/12-dependency-graph.md 与 scripts/dep-graph.py 里就有
# github.com/<org>/go-dbsync 这类真实模块路径，dep-graph.py 的 startswith 判断
# 更是功能性的 —— 一刀切换完，依赖图会去扫一个不存在的组织，而且不报错
#
# [EN] The repository name is taken as well, because the replacement has to be the whole
# <org>/<repo> and not the bare organization name: swapping the bare name also rewrites
# legitimate same-organization references in the content, such as the real module paths
# github.com/<org>/go-dbsync in docs/12-dependency-graph.md and scripts/dep-graph.py, whose
# startswith test is functional rather than cosmetic, so a blanket swap silently points the
# dependency graph at an organization that does not exist
repo_of() {
  local url
  url="$(cd "$1" && git remote get-url origin 2>/dev/null || true)"
  printf '%s\n' "${url}" \
    | sed -E 's|^git@[^:]+:||; s|^https?://[^/]+/||; s|\.git$||' \
    | cut -d/ -f2
}

FROM_ORG="$(owner_of "${SRC}")"
TO_ORG="$(owner_of "${DEST}")"
FROM_REF="${FROM_ORG}/$(repo_of "${SRC}")"
TO_REF="${TO_ORG}/$(repo_of "${DEST}")"
if [ -z "${FROM_ORG}" ] || [ -z "${TO_ORG}" ]; then
  exit 1
fi
if [ "${FROM_ORG}" = "${TO_ORG}" ]; then
  echo "❌ 两边 origin 的组织名都是 ${FROM_ORG}，这俩是同一个仓库？" >&2
  exit 1
fi

echo "📦 来源 : ${SRC}  (${FROM_ORG})"
echo "📦 去向 : ${DEST}  (${TO_ORG})"
echo "🔁 改写 : ${FROM_REF} → ${TO_REF}"
[ -n "${FORCE}" ] && echo "💣 强制 : 对面多出来的文件会被删掉"
[ -n "${CHECK_ONLY}" ] && echo "👀 模式 : 只检查，不落盘"
echo ""

# 要同步的文件清单：已跟踪的 + 未跟踪但没被 ignore 的（新加的文件还没 add 也要带上）
# [EN] Tracked files plus untracked ones that are not ignored, so a newly added file is included
targets=()
while IFS= read -r f; do [ -n "$f" ] && targets+=("$f"); done < <(
  cd "${SRC}" && { git ls-files; git ls-files --others --exclude-standard; } | sort -u
)
if [ "${#targets[@]}" -eq 0 ]; then
  echo "❌ 来源仓库里没取到文件清单" >&2
  exit 1
fi

changed=0
added=0
for rel in "${targets[@]}"; do
  src_file="${SRC}/${rel}"
  [ -f "${src_file}" ] || continue
  dest_file="${DEST}/${rel}"

  # 只对文本文件换前缀，其它按字节复制
  # [EN] The prefix swap applies to text files; everything else is copied byte for byte
  case "${rel}" in
    *.yml|*.yaml|*.md|*.sh|*.py|*.json|*.txt)
      new_content="$(sed "s|${FROM_REF}|${TO_REF}|g" "${src_file}")"
      ;;
    *)
      new_content="$(cat "${src_file}")"
      ;;
  esac

  if [ ! -f "${dest_file}" ]; then
    echo "  + ${rel}"
    added=$((added + 1))
  elif [ "${new_content}" != "$(cat "${dest_file}")" ]; then
    echo "  ~ ${rel}"
    changed=$((changed + 1))
  else
    continue
  fi

  if [ -z "${CHECK_ONLY}" ]; then
    mkdir -p "$(dirname "${dest_file}")"
    # 末尾补回换行：$(...) 会吃掉最后一个换行符
    # [EN] Command substitution strips the trailing newline, so it is written back
    printf '%s\n' "${new_content}" > "${dest_file}"
    [ -x "${src_file}" ] && chmod +x "${dest_file}"
  fi
done

echo ""
echo "📊 新增 ${added} 个，更新 ${changed} 个"
echo ""

# 对面多出来的文件：默认只列出来，--force 才删
# [EN] Files only on the other side are listed by default and deleted only under --force
only_dest="$(
  comm -13 \
    <(printf '%s\n' "${targets[@]}" | sort -u) \
    <(cd "${DEST}" && { git ls-files; git ls-files --others --exclude-standard; } | sort -u)
)"
if [ -n "${only_dest}" ]; then
  if [ -n "${FORCE}" ]; then
    echo "💣 对面多出来的文件，--force 下删除 | deleted under --force:"
    for extra in ${only_dest}; do
      echo "   - ${extra}"
      [ -z "${CHECK_ONLY}" ] && rm -f "${DEST}/${extra}"
    done
  else
    echo "⚠️  只在对面有（本次没动；确认要删就加 --force，想留就先同步回来）:"
    printf '   %s\n' ${only_dest}
  fi
else
  echo "✅ 两边文件集合一致"
fi

# 换前缀漏网检查：同步完对面不该再出现指向来源仓库的自引用
#
# 查的是 <org>/<repo> 而不是裸组织名：后者会把内容里正当的同组织引用也判成残留，
# 逼着替换一刀切 —— 校验和缺陷互相掩护，两边一起错
#
# [EN] The check looks for <org>/<repo> rather than the bare organization name, which would
# flag legitimate same-organization references as residue and force the blanket swap that
# causes the corruption, the check and the defect covering for each other
if [ -z "${CHECK_ONLY}" ]; then
  echo ""
  leftover="$(cd "${DEST}" && grep -rln "${FROM_REF}" --exclude-dir=.git . || true)"
  if [ -n "${leftover}" ]; then
    echo "❌ ${DEST} 里还残留 ${FROM_REF}：" >&2
    printf '     %s\n' ${leftover} >&2
    echo "二进制文件或没列进扩展名白名单的文件会漏，检查一下那几个" >&2
    exit 1
  fi
  echo "✅ 对面已无 ${FROM_REF} 残留"
  echo ""
  echo "下一步：cd ${DEST} && git diff --stat && git add -A && git commit"
fi
