#!/usr/bin/env bash
# 预检：go.mod 里那些私有模块，当前凭据到底看不看得见
#
# 不预检的后果是 go mod download 报这么一句：
#   github.com/acme/foo@v0.0.0-...: invalid version:
#   git ls-remote ... exit status 128: remote: Repository not found.
# 读起来像仓库名写错了或者仓库被删了，而真实原因几乎总是另一个：
# 那个 GitHub App 的仓库清单里没有它
#
# GitHub 对无权访问的私有仓库返回 404 而不是 403（不泄漏仓库是否存在），
# 所以「看不见」和「不存在」在报错里长得一模一样 —— 这是整件事难查的全部原因
#
# 只检 GOPRIVATE 匹配到的那些：公共依赖本来就能拉，逐个问一遍纯属浪费
#
# [EN] Preflight for the private modules in go.mod: can the current credentials actually see
# them? Without it, go mod download reports "remote: Repository not found.", which reads like a
# wrong name or a deleted repository while the real cause is almost always that the GitHub App's
# repository list does not include it. GitHub answers 404 rather than 403 for a private
# repository you cannot see, so "invisible" and "nonexistent" look identical -- which is the whole
# reason this is hard to diagnose. Only the repositories matched by GOPRIVATE are checked, since
# public dependencies resolve anyway.
#
# 用法 | Usage
#   GOPRIVATE='github.com/acme/*' GH_TOKEN=<token> bash check-private-modules.sh [go.mod 路径]
#
# 退出码：0 都看得见（或没有私有依赖）/ 1 有看不见的 / 2 缺前置条件
set -uo pipefail

MOD="${1:-go.mod}"
[ -f "${MOD}" ] || { echo "找不到 ${MOD}" >&2; exit 2; }

PRIV="${GOPRIVATE:-}"
if [ -z "${PRIV//[[:space:],]/}" ]; then
  echo "GOPRIVATE 为空，没有私有依赖要检"
  exit 0
fi
[ -n "${GH_TOKEN:-}" ] || { echo "没有 GH_TOKEN，跳过可达性预检" >&2; exit 0; }
command -v curl >/dev/null 2>&1 || { echo "找不到 curl" >&2; exit 2; }

# 本模块自己不用检：它就是当前仓库，checkout 已经成功了
SELF="$(awk '/^module /{print $2; exit}' "${MOD}")"

# GOPRIVATE 形如 github.com/acme/*,github.com/other/* —— 去掉尾部的 /* 当前缀用
prefixes=""
while IFS= read -r pat; do
  pat="$(printf '%s' "${pat}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s#/\*$##')"
  [ -n "${pat}" ] && prefixes="${prefixes} ${pat}"
done < <(printf '%s\n' "${PRIV}" | tr ',' '\n')

# 从 go.mod 里取出模块路径，按前缀筛，再归约到 owner/repo
# （去掉 /v2 这类主版本后缀与子目录：GitHub 仓库只有前两段）
# [EN] Module paths are reduced to owner/repo, dropping a /vN major suffix and any subdirectory,
# since a GitHub repository is only the first two segments
repos=""
while IFS= read -r line; do
  path="$(printf '%s' "${line}" | awk '{print $1}')"
  case "${path}" in
    github.com/*) : ;;
    *) continue ;;
  esac
  [ "${path}" = "${SELF}" ] && continue
  hit=0
  for p in ${prefixes}; do
    case "${path}" in "${p}/"*) hit=1; break ;; esac
  done
  [ "${hit}" = "1" ] || continue
  slug="$(printf '%s' "${path}" | cut -d/ -f2,3)"
  case " ${repos} " in *" ${slug} "*) : ;; *) repos="${repos} ${slug}" ;; esac
done < <(grep -oE '^[[:space:]]*github\.com/[A-Za-z0-9._/-]+[[:space:]]+v[0-9]' "${MOD}" | sed 's/^[[:space:]]*//')

if [ -z "${repos// /}" ]; then
  echo "go.mod 里没有匹配 GOPRIVATE 的依赖"
  exit 0
fi

# 两级探测：这两件事都让 git 说 Repository not found，但修法完全不同
#
#   1) /repos/<slug>            只要 Metadata 权限 → 404 说明仓库不在 App 的清单里
#   2) /repos/<slug>/commits    要 Contents 权限   → 非 200 说明装了但读不了文件
#
# 只做第 1 步是不够的：App 装在仓库上、却没给 Contents 时，第 1 步返回 200，
# 而 go mod download 走的 git 照样拿不到东西 —— 预检打了勾、构建仍然红，
# 这种假阴性比不检更坏 实际踩到过：同一个 App 两个仓库都是这个情况
#
# [EN] Two probes, because both conditions make git say "Repository not found" while the fixes
# differ: /repos/<slug> needs only Metadata, so a 404 means the repository is outside the App's
# list; /repos/<slug>/commits needs Contents, so a non-200 means the App is installed but cannot
# read files. Probing only the first is not enough -- an App installed without Contents answers
# 200 there while git still gets nothing, so the preflight would pass and the build still fail,
# which is worse than not checking at all
API="${GH_API_BASE:-https://api.github.com}"

probe() {
  curl -sS -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "${API}/$1" 2>/dev/null
}

missing=""   # 不在 App 的仓库清单里
unreadable="" # 在清单里，但没有 Contents 权限
for slug in ${repos}; do
  code="$(probe "repos/${slug}")"
  if [ "${code}" != "200" ]; then
    echo "  ❌ ${slug}（HTTP ${code}）—— 不在 App 的仓库清单里"
    missing="${missing} ${slug}"
    continue
  fi
  # 409 是空仓库：Contents 权限没问题，只是还没有提交
  # [EN] 409 means an empty repository: Contents is fine, there is simply no commit yet
  ccode="$(probe "repos/${slug}/commits?per_page=1")"
  if [ "${ccode}" = "200" ] || [ "${ccode}" = "409" ]; then
    echo "  ✅ ${slug}"
  else
    echo "  ❌ ${slug}（清单里有，但读文件 HTTP ${ccode}）—— 缺 Contents 权限"
    unreadable="${unreadable} ${slug}"
  fi
done

if [ -z "${missing// /}" ] && [ -z "${unreadable// /}" ]; then
  echo "私有依赖都看得见"
  exit 0
fi

# 修法页面在组织名下 —— 取第一个出问题的仓库的 owner 就够了
# [EN] The settings pages live under the organization, so the first failing repo's owner suffices
first="$(printf '%s' "${missing} ${unreadable}" | tr ' ' '\n' | grep -v '^$' | head -1)"
OWNER="${first%%/*}"
APPS_URL="https://github.com/organizations/${OWNER}/settings/apps"
INST_URL="https://github.com/organizations/${OWNER}/settings/installations"

echo "::error::GitHub 对读不到的私有仓库返回 404 而不是 403，所以 go mod download 那句 remote: Repository not found. 读起来像仓库名写错，其实不是" >&2

for slug in ${missing}; do
  echo "::error::${slug} 不在 App 的仓库清单里 —— 到 ${INST_URL} 把它加进 Repository access" >&2
done
for slug in ${unreadable}; do
  echo "::error::${slug} 装了 App 但没有 Contents 权限 —— 到 ${APPS_URL} 选中 go.app_id 那个 App → Permissions & events → Repository permissions → Contents 给 Read-only" >&2
done
[ -n "${unreadable// /}" ] && echo "::error::改完权限组织里会多一条待批准的变更，在 ${INST_URL} 批准后才对已装好的安装生效" >&2

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "## 🚨 私有依赖拉不到"
    echo ""
    echo "### 为什么报错像「仓库不存在」"
    echo ""
    echo "GitHub 对读不到的私有仓库返回 **404 而不是 403**（不泄漏仓库是否存在），"
    echo "所以 \`go mod download\` 那句 \`remote: Repository not found.\` 读起来像名字写错或仓库被删"
    echo ""
    echo "下面两种情况报的是同一句话，但修法不一样 ——"
    if [ -n "${missing// /}" ]; then
      echo ""
      echo "### ① 不在 App 的仓库清单里"
      echo ""
      for slug in ${missing}; do echo "- \`${slug}\`"; done
      echo ""
      echo "到 <${INST_URL}> → 该 App 的 Configure → Repository access 里把它们加进去"
    fi
    if [ -n "${unreadable// /}" ]; then
      echo ""
      echo "### ② 装了 App，但没有 Contents 权限"
      echo ""
      for slug in ${unreadable}; do echo "- \`${slug}\`"; done
      echo ""
      echo "这几个仓库 App 已经装上了，**Repository access 那页不用再改** 缺的是读文件的权限："
      echo ""
      echo "1. 到 <${APPS_URL}>，选中 \`go.app_id\` 对应那个 App"
      echo "2. Permissions & events → Repository permissions → **Contents** 给 Read-only"
      echo "3. 到 <${INST_URL}> 批准那条待审的权限变更 —— 不批准对已经装好的安装不生效"
    fi
    echo ""
    echo "> 要改的是**同一个** App：流水线用的是 \`go.app_id\` / \`go.app_private_key\` 这一对"
  } >> "${GITHUB_STEP_SUMMARY}"
fi
exit 1
