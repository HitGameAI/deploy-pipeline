#!/usr/bin/env bash
# 盯指定三方模块的升级，并给出**内容** diff
#
# 为什么不靠 dependabot 就够：dependabot 只说「v1.2.3 → v1.2.4」，而 16 个仓库的
# automerge 把 minor 与 patch 直接合掉，没人看过里面改了什么
# 对多数依赖这没问题，对少数依赖不行 —— 典型是把对端协议版本写死在代码里的那种，
# 上游一个 patch 里改掉那个常量，行为就全变了
#
# 所以这里做三件事，都由配置驱动，脚本里不认识任何具体模块：
#   1. pinned 版本 vs 上游最新版本
#   2. 内容 diff：优先只看**本仓库 import 到的包**（一次上游提交动几十个文件，
#      真正影响你的通常只有几个），再加上配置指定的重点文件全文 diff
#   3. 值探针：按正则从源码里抠一个值，比较两版之间变没变 ——
#      覆盖「某个常量才是关键」的情形，比读整份 diff 直接
#
# [EN] Watches specified third-party modules and reports a content diff. dependabot only says
# "v1.2.3 to v1.2.4", and the automerge in 16 repositories merges minor and patch without anyone
# reading what changed. That is fine for most dependencies and not for a few -- typically those
# hardcoding a peer protocol version, where one upstream patch to that constant changes
# everything. Three things, all config-driven, with no module-specific knowledge in the script:
# the version comparison, a content diff that prefers the packages this repository actually
# imports plus full diffs of configured focus files, and value probes that extract a value by
# regex and compare it across the two versions.
#
# 用法 | Usage
#   GOLIB_WATCH="$(cat <<'YAML'
#   - module: example.com/upstream-sdk
#     focus:
#       - store/payload.go
#     probes:
#       - name: 客户端协议版本
#         file: store/payload.go
#         regex: 'ProtocolVersion\{[0-9, ]+\}'
#   - module: github.com/example/transport
#     focus:
#       - transport/client.go
#   YAML
#   )" \
#   bash dep-watch.sh <输出目录>
#
# 退出码：0 没有要报的 / 2 有（调用方据此告警）/ 1 自身出错
set -uo pipefail

OUT="${1:-}"
[ -n "${OUT}" ] || { echo "用法：dep-watch.sh <输出目录>" >&2; exit 1; }
mkdir -p "${OUT}"
REPORT="${OUT}/report.md"
# 判定写成文件，不靠 grep 正文：循环跑在子 shell 里，变量传不回来，
# 而按提示文字去 grep，改一句文案就把判定改坏了
# [EN] The verdict is a file, not a grep over prose: the loop runs in a subshell so a variable
# cannot come back, and matching on wording means rewording silently breaks the verdict
VERDICT="${OUT}/verdict"
: > "${REPORT}"
: > "${VERDICT}"

WATCH="${GOLIB_WATCH:-}"
MAX_STAT="${GOLIB_WATCH_MAX_STAT:-60}"
MAX_DIFF="${GOLIB_WATCH_MAX_DIFF:-120}"

if [ -z "${WATCH//[[:space:]]/}" ]; then
  echo "golib.watch 为空，跳过"
  exit 0
fi
command -v go >/dev/null 2>&1 || { echo "找不到 go" >&2; exit 1; }

# 配置是 YAML 列表，用 python 摊成**制表符分隔**的行协议再交给 bash
#
# 为什么不用「逗号分隔的字符串」那种配置：探针的值是正则，里面本来就带逗号、分号、
# 冒号，任何单字符分隔符都会撞上 —— 这不是假想，第一版就是这么坏的：
# 探针写成 模块=文件:正则 用逗号分隔，而正则里的 [0-9, ] 把一条切成了两半，
# 表现是探针静默取不到值、不报错
# 制表符不会出现在模块路径、文件路径或正则里，所以行协议用它做分隔
#
# [EN] The config is a YAML list, flattened by python into a tab-separated line protocol. A
# comma-separated string does not work here because a probe value is a regex and routinely
# contains commas, semicolons and colons -- which is how the first version broke: [0-9, ] inside
# a probe cut it in half and the probe silently yielded nothing. A tab appears in neither a module
# path, a file path nor a regex, so it is the separator for the protocol.
PLAN="${OUT}/plan.tsv"
if ! printf '%s\n' "${WATCH}" | python3 -c '
import sys

try:
    import yaml
except ImportError:
    sys.exit("::error::需要 PyYAML 来解析 golib.watch")

try:
    items = yaml.safe_load(sys.stdin.read())
except yaml.YAMLError as exc:
    sys.exit("::error::golib.watch 不是合法 YAML（%s）" % exc)

if items is None:
    items = []
if not isinstance(items, list):
    sys.exit("::error::golib.watch 要写成 YAML 列表，每项一个模块")

out = []
for n, item in enumerate(items, 1):
    if not isinstance(item, dict):
        sys.exit("::error::golib.watch 第 %d 项不是映射 —— 每项要有 module，可选 focus 与 probes" % n)
    mod = str(item.get("module") or "").strip()
    if not mod:
        sys.exit("::error::golib.watch 第 %d 项缺 module" % n)
    out.append("module\t%s" % mod)
    focus = item.get("focus") or []
    if isinstance(focus, str):
        focus = [focus]
    for f in focus:
        f = str(f).strip()
        if f:
            out.append("focus\t%s\t%s" % (mod, f))
    for p in (item.get("probes") or []):
        if not isinstance(p, dict):
            sys.exit("::error::%s 的 probes 每项要是映射，含 file 与 regex" % mod)
        pf = str(p.get("file") or "").strip()
        rx = str(p.get("regex") or "").strip()
        if not pf or not rx:
            sys.exit("::error::%s 的 probes 每项都要有 file 与 regex" % mod)
        name = str(p.get("name") or pf).strip()
        if "\t" in rx or "\t" in pf:
            sys.exit("::error::%s 的 probe 里不能含制表符" % mod)
        out.append("probe\t%s\t%s\t%s\t%s" % (mod, pf, rx, name))

print("\n".join(out))
' > "${PLAN}" 2>"${OUT}/plan.err"; then
  sed 's/^/  /' "${OUT}/plan.err" >&2
  exit 1
fi

if [ ! -s "${PLAN}" ]; then
  echo "golib.watch 解析后没有任何条目，跳过"
  exit 0
fi
echo "要盯 $(grep -c '^module	' "${PLAN}") 个模块"

# 某个模块某个版本在本机的解包目录：只下载不编译，所以不需要能编过
# [EN] The unpacked directory of one module version: download only, so the repository need not build
module_dir() {
  go mod download -json "$1@$2" 2>/dev/null | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("Dir", ""))
except Exception:
    print("")'
}
# 本仓库真正 import 到这个模块里的哪些包 —— 输出模块内的相对目录
#
# 这是整个脚本里最有用的一层：上游一次提交常动几十个文件，而其中绝大多数
# 本仓库根本用不到 只看 import 到的那些，才能回答「这次升级影响我吗」
# [EN] The packages of the module this repository actually imports, as paths relative to the
# module root. This is the most useful filter here: an upstream commit touches dozens of files and
# most are never reached from this repository, so restricting the diff to imported packages is
# what answers whether an upgrade affects you.
imported_dirs() {
  local mod="$1"
  go list -deps ./... 2>/dev/null \
    | grep -E "^${mod}(/|\$)" \
    | sed "s|^${mod}/\{0,1\}||" \
    | grep -v '^$' | sort -u
}

# 逐个模块：plan 里 module 开头的行
# [EN] One module per `module` line in the plan
grep '^module	' "${PLAN}" | cut -f2 | while IFS= read -r mod; do
  [ -n "${mod}" ] || continue
  echo "── ${mod}"

  cur="$(go list -m -f '{{.Version}}' "${mod}" 2>/dev/null)"
  if [ -z "${cur}" ]; then
    echo "   ⚠️ 本仓库没有依赖它，跳过"
    {
      echo "### \`${mod}\`"
      echo ""
      echo "⚠️ 本仓库 go.mod 里没有这个模块 —— 检查 \`golib.watch_modules\` 是否写错"
      echo ""
    } >> "${REPORT}"
    echo "not-a-dependency ${mod}" >> "${VERDICT}"
    continue
  fi

  latest="$(go list -m -u -f '{{if .Update}}{{.Update.Version}}{{end}}' "${mod}" 2>/dev/null)"
  echo "   pinned ${cur}"
  echo "   最新   ${latest:-<已是最新>}"
  {
    echo "### \`${mod}\`"
    echo ""
    echo "| 项 | 值 |"
    echo "| --- | --- |"
    echo "| 本仓库 pinned | \`${cur}\` |"
    echo "| 上游最新 | \`${latest:-已是最新}\` |"
  } >> "${REPORT}"

  curdir="$(module_dir "${mod}" "${cur}")"
  newdir=""
  [ -n "${latest}" ] && newdir="$(module_dir "${mod}" "${latest}")"
  [ -n "${latest}" ] && echo "newer-version ${mod} ${cur} -> ${latest}" >> "${VERDICT}"

  # 值探针：模块=文件:正则
  # 正则里可以带冒号，所以按**第一个**冒号切，不是最后一个
  # [EN] Probes are module=file:regex and a regex may contain colons, so the split is on the first
  grep "^probe	${mod}	" "${PLAN}" | while IFS=$'\t' read -r _kind _mod pfile prx pname; do
    [ -n "${pfile}" ] && [ -n "${prx}" ] || continue
    before=""
    after=""
    [ -f "${curdir}/${pfile}" ] && before="$(grep -ohE "${prx}" "${curdir}/${pfile}" 2>/dev/null | head -1)"
    [ -n "${newdir}" ] && [ -f "${newdir}/${pfile}" ] && \
      after="$(grep -ohE "${prx}" "${newdir}/${pfile}" 2>/dev/null | head -1)"
      # 分两步拼，不要把 $(...) 塞在引号串中间
      # "前半"$(...)"后半" 这种写法会在 $(...) 处**关掉引号**，命令替换的结果于是被词分割
      # —— 探针名或正则里只要有空格，这一行就散成好几段（shellcheck SC2027 / SC2046）
      # [EN] Assembled in steps instead of embedding $(...) inside a quoted string:
      # "head"$(...)"tail" closes the quote at the substitution, so its output is word-split and a
      # probe name or regex containing a space breaks the row apart (SC2027 / SC2046)
      row="| 探针 \`${pname:-${pfile}}\` | 本仓库 \`${before:-匹配不到}\`"
      if [ -n "${newdir}" ]; then
        row="${row} ／ 上游最新 \`${after:-匹配不到}\`"
      fi
      printf '%s |\n' "${row}" >> "${REPORT}"
    # 显示要分清「没有更新版本可比」与「正则在新版里匹配不到」—— 两者 after 都是空，
    # 混成一句话会让人以为探针好着
    # [EN] The display distinguishes "no newer version to compare" from "the regex matched nothing
    # in the new version": after is empty either way, and conflating them hides a broken probe
    if [ -z "${newdir}" ]; then
      shown="（没有更新版本可比）"
    else
      shown="${after:-匹配不到}"
    fi
    echo "   探针 ${pname:-${pfile}}: ${before:-匹配不到} → ${shown}"
    # 只要两边不同就报，而不是「两边都非空才报」
    #
    # 三种不同各有含义，尤其第三种：探针目标消失说明它从此**失明** ——
    # 常量被改名或挪走了，而探针还配着老正则，以后永远取不到值也永远不报警
    # 这正是最该发现的一类，只比「两边都非空」那一种更隐蔽
    # [EN] Any difference is reported rather than only when both sides are non-empty. The three
    # cases differ in meaning, and the third matters most: a probe target that disappeared means
    # the probe has gone blind -- the constant was renamed or moved while the probe still carries
    # the old regex, so it will never match and never warn again
    # 两版都匹配不到：探针从一开始就配错了，是个永远不会响的检查
    # 这比「值变了」更该报 —— 它会让人以为「没报警就是没事」
    # [EN] Matching nothing in either version means the probe was misconfigured from the start: a
    # check that can never fire, which is worse than a changed value because it reads as "no news"
    if [ -z "${before}" ] && [ -z "${after}" ] && [ -n "${newdir}" ]; then
      echo "probe-never-matched ${mod} ${pfile} ${prx}" >> "${VERDICT}"
      {
        echo ""
        echo "🚨 探针 **${pname:-${pfile}}** 在**两个版本里都匹配不到**"
        echo ""
        echo "正则 \`${prx}\` 从一开始就没命中过 —— 这是个永远不会响的检查，"
        echo "而它不响会被当成「没事」 核对 \`golib.watch\` 里这条的 file 与 regex"
      } >> "${REPORT}"
    fi

    # 没有新版本就没有「两版」可比 —— 不加这个判断，after 恒为空，
    # 于是每个取到值的探针都会被报成「新版里匹配不到、探针失明」
    # 实测踩过：被监视的模块已是最新，却报出 probe-blind
    # [EN] Without a newer version there are no two versions to compare: after is always empty,
    # so every probe that matched would be reported as having gone blind -- measured, a watched module
    # was up to date and still raised probe-blind
    if [ -n "${newdir}" ] && [ "${before}" != "${after}" ]; then
      if [ -n "${before}" ] && [ -n "${after}" ]; then
        echo "probe-changed ${mod} ${pfile} ${before} -> ${after}" >> "${VERDICT}"
        {
          echo ""
          echo "🚨 探针 **${pname:-${pfile}}** 的值变了：\`${before}\` → \`${after}\`"
          echo ""
          echo "它变了说明这次升级动的是**被盯住的那个值**，不是无关改动"
        } >> "${REPORT}"
      elif [ -z "${before}" ] && [ -n "${after}" ]; then
        echo "probe-appeared ${mod} ${pfile} ${after}" >> "${VERDICT}"
        {
          echo ""
          echo "ℹ️ 探针 **${pname:-${pfile}}** 在旧版里匹配不到、新版里匹配到了：\`${after}\`"
          echo ""
          echo "多半是上游新增了这个东西"
        } >> "${REPORT}"
      else
        echo "probe-blind ${mod} ${pfile} ${before}" >> "${VERDICT}"
        {
          echo ""
          echo "🚨 探针 **${pname:-${pfile}}** 在新版里**匹配不到了**（旧版是 \`${before}\`）"
          echo ""
          echo "被盯的东西改名或挪走了 —— 探针从此失明，再也不会报警"
          echo "要么更新 \`golib.watch\` 里这条探针的正则，要么确认它已经不需要盯了"
        } >> "${REPORT}"
      fi
    fi
  done

  if [ -z "${newdir}" ] || [ -z "${curdir}" ]; then
    echo "" >> "${REPORT}"
    continue
  fi

  # 内容 diff：先只看 import 到的包
  dirs="$(imported_dirs "${mod}")"
  {
    echo ""
    if [ -n "${dirs}" ]; then
      echo "本仓库 import 到 $(printf '%s\n' "${dirs}" | grep -c .) 个包，先看这些："
      echo ""
      echo '```text'
      printf '%s\n' "${dirs}" | while IFS= read -r d; do
        [ -n "${d}" ] || continue
        [ -d "${curdir}/${d}" ] || continue
        if diff -rq "${curdir}/${d}" "${newdir}/${d}" >/dev/null 2>&1; then
          echo "  未变  ${d}"
        else
          echo "  已变  ${d}"
        fi
      done
      echo '```'
    else
      echo "（本仓库没有直接 import 这个模块的包，可能是间接依赖）"
    fi
    echo ""
    echo "<details><summary>整个模块的变更文件</summary>"
    echo ""
    echo '```text'
    diff -rq "${curdir}" "${newdir}" 2>/dev/null \
      | sed "s|${curdir}/\{0,1\}||g; s|${newdir}/\{0,1\}||g" | head -"${MAX_STAT}"
    echo '```'
    echo ""
    echo "</details>"
  } >> "${REPORT}"

  # 重点文件的完整 diff
  grep "^focus	${mod}	" "${PLAN}" | cut -f3 | while IFS= read -r rel; do
    [ -n "${rel}" ] || continue
    [ -f "${curdir}/${rel}" ] || continue
    {
      echo ""
      echo "<details><summary>重点文件 <code>${rel}</code></summary>"
      echo ""
      echo '```diff'
      diff -u "${curdir}/${rel}" "${newdir}/${rel}" 2>/dev/null \
        | sed "s|${curdir}/\{0,1\}||g; s|${newdir}/\{0,1\}||g" | head -"${MAX_DIFF}"
      echo '```'
      echo ""
      echo "</details>"
    } >> "${REPORT}"
  done
  echo "" >> "${REPORT}"
done

if [ -s "${VERDICT}" ]; then
  echo ""
  echo "需要关注 $(grep -c . "${VERDICT}") 项："
  sed 's/^/  /' "${VERDICT}"
  exit 2
fi
echo ""
echo "没有要报的"
exit 0
