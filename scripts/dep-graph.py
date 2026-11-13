#!/usr/bin/env python3
# ============================================================================
# 依赖层级生成器 | Dependency layer generator
#
# 把 kamalyes 系 Go 仓库的真实依赖关系算成分层图，输出 markdown
#
# 为什么要有这个脚本而不是手写一张表：这张表一旦手写就会脱节，
# 而它恰好是「发 tag 的顺序」的依据 —— 顺序错了就会出现下游拿着旧上游去编译
# 判定规则与 go-trigger-downstream.yml 的自动推导完全一致，故意保持一致：
# 只认直接 require，带 // indirect 的不算，注释掉的与 replace 行不算
#
# 用法：
#   python3 scripts/dep-graph.py --workspace ~/Workspaces/GoProjects
#   python3 scripts/dep-graph.py --workspace ~/w --extra-mods /tmp/gomods -o docs/12-dependency-graph.md
#
# --extra-mods 指向一个目录，里面是 <repo>.mod 文件（从 GitHub 取下来的 go.mod），
# 用来把本地没 clone 的仓库也算进图里 —— 不算的话下游数会偏小
#
# [EN] Computes the real dependency layers of the kamalyes Go repositories and emits markdown.
# A hand-written table would drift, and this one is the basis for release ordering, where a
# wrong order means a downstream compiling against a stale upstream. The rules match the
# automatic derivation in go-trigger-downstream.yml on purpose: only direct requirements count,
# entries marked // indirect do not, and neither do commented lines or replace directives.
# ============================================================================

import argparse
import io
import os
import re
import sys
from collections import defaultdict

KAMALYES = re.compile(r'(github\.com/kamalyes/[A-Za-z0-9._/-]+)\s+v')


def parse_gomod(text):
    """返回 (module, 直接依赖的 kamalyes 模块集合)"""
    m = re.search(r'^module\s+(\S+)', text, re.M)
    module = m.group(1) if m else None
    deps = set()
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('//'):
            continue
        if '// indirect' in line:
            continue
        if stripped.startswith('replace') or '=>' in stripped:
            continue
        if stripped.startswith('module'):
            continue
        hit = KAMALYES.match(stripped)
        if hit:
            deps.add(hit.group(1))
    return module, deps


def collect(workspace, extra_mods):
    repos = {}

    for name in sorted(os.listdir(workspace)):
        path = os.path.join(workspace, name)
        gomod = os.path.join(path, 'go.mod')
        if not os.path.isdir(os.path.join(path, '.git')) or not os.path.exists(gomod):
            continue
        module, deps = parse_gomod(io.open(gomod, encoding='utf-8', errors='replace').read())
        if not module:
            continue
        repos[name] = {'module': module, 'raw_deps': deps, 'local': True}

    if extra_mods and os.path.isdir(extra_mods):
        for f in sorted(os.listdir(extra_mods)):
            if not f.endswith('.mod'):
                continue
            name = f[:-4]
            if name in repos:
                continue
            module, deps = parse_gomod(io.open(os.path.join(extra_mods, f), encoding='utf-8', errors='replace').read())
            # 只收 kamalyes 名下的模块；fork 来的（模块路径不是 kamalyes）不算本系
            if not module or not module.startswith('github.com/kamalyes/'):
                continue
            repos[name] = {'module': module, 'raw_deps': deps, 'local': False}

    mod2repo = {v['module']: k for k, v in repos.items()}
    for name, info in repos.items():
        info['deps'] = {mod2repo[d] for d in info['raw_deps'] if d in mod2repo and mod2repo[d] != name}
        # 依赖了 kamalyes 的模块但那个仓库不在图里，单独记下来，否则这条边会无声消失
        info['unresolved'] = sorted(d for d in info['raw_deps'] if d not in mod2repo)
    return repos


def layer(repos):
    assigned = {}
    pending = dict(repos)
    level = 0
    while pending:
        ready = [n for n, v in pending.items() if not (v['deps'] - set(assigned))]
        if not ready:
            return assigned, sorted(pending)
        for n in sorted(ready):
            assigned[n] = level
        for n in ready:
            pending.pop(n)
        level += 1
    return assigned, []


def downstreams(repos):
    down = defaultdict(set)
    for name, info in repos.items():
        for dep in info['deps']:
            down[dep].add(name)
    return down


def transitive_downstreams(repos, down):
    """传递闭包：发一个 tag 最终会波及多少仓库"""
    out = {}
    for start in repos:
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            for nxt in down.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        out[start] = seen
    return out


def pubnames(repos, names):
    """列名时跳过内部仓库，但把被跳过的个数如实写出来

    计数照旧用完整集合，只是不把名字印出来 —— 这样公开文档里的数字仍然是真的，
    不会因为脱敏而让人低估改动的影响面
    [EN] Names of internal repositories are omitted while their number is stated, so the figures
    in a public document stay true and nobody underestimates the blast radius because of it
    """
    pub = [n for n in names if not repos.get(n, {}).get('internal')]
    hidden = len(names) - len(pub)
    out = ' '.join('`%s`' % n for n in pub)
    if hidden:
        out = (out + ' ' if out else '') + '_内部服务 ×%d_' % hidden
    return out or '—'


def render(repos, assigned, cycles, down, trans):
    L = []
    w = L.append
    bylevel = defaultdict(list)
    for n, lv in assigned.items():
        bylevel[lv].append(n)

    total_edges = sum(len(v['deps']) for v in repos.values())
    local_n = sum(1 for v in repos.values() if v['local'])

    w('# 依赖层级')
    w('')
    w('> 本文是 [deploy-pipeline](../README.md) 文档的一部分')
    w('')
    w('## 🧬 依赖层级')
    w('')
    w('本文由 [`scripts/dep-graph.py`](../scripts/dep-graph.py) 从各仓库真实的 `go.mod` 生成，不是手写的')
    w('手写的表一定会脱节，而这张表恰好是**发 tag 顺序**的依据 —— 顺序错了就会出现下游拿着旧上游去编译')
    w('')
    w('重新生成：')
    w('')
    w('```bash')
    w('python3 scripts/dep-graph.py --workspace ~/Workspaces/GoProjects \\')
    w('    --extra-mods /tmp/gomods -o docs/12-dependency-graph.md')
    w('```')
    w('')
    w('### 判定规则')
    w('')
    w('与 [Go Trigger Downstream](02-workflows.md) 的自动推导**完全一致**，故意保持一致，')
    w('这样「文档里看到的下游」和「发版时真正被通知的下游」不会是两回事：')
    w('')
    w('| 算 | 不算 |')
    w('|---|---|')
    w('| `go.mod` 里直接 `require` 的 kamalyes 模块 | 带 `// indirect` 的传递依赖 |')
    w('| | 被 `//` 注释掉的行 |')
    w('| | `replace` 指令指向的目标 |')
    w('| | 模块路径不在 `github.com/kamalyes/` 下的（fork 来的仓库） |')
    w('')
    w('带 `// indirect` 的不算，是因为那些依赖由它们**自己的直接上游**在各自那一跳通知到，')
    w('重复通知只会让同一个仓库被反复 bump')
    w('')
    w('### 规模')
    w('')
    w('| 项 | 值 |')
    w('|---|---|')
    w('| 纳入的仓库 | %d 个（本地 %d 个，仅远端 %d 个） |' % (len(repos), local_n, len(repos) - local_n))
    w('| 直接依赖边 | %d 条 |' % total_edges)
    w('| 层数 | %d 层 |' % (max(bylevel) + 1 if bylevel else 0))
    if cycles:
        w('| ⚠️ 循环依赖 | %s |' % ' '.join(cycles))
    w('')
    w('只在远端的那些仓库也算进来了：不算的话下游数会偏小，')
    w('而发版联动是按远端真实情况跑的，本地有没有 clone 并不影响它被通知')
    w('')

    w('---')
    w('')
    w('## 📚 分层')
    w('')
    w('第 0 层不依赖本系任何仓库，是**最底层，改动与发版从这里开始**')
    w('往上每一层都只依赖更低的层，所以按层序发 tag 就不会出现「下游先发、上游后发」')
    w('')
    for lv in sorted(bylevel):
        names = sorted(bylevel[lv])
        shown = [n for n in names if not repos[n].get('internal')]
        hidden = len(names) - len(shown)
        w('### 第 %d 层（%d 个）' % (lv, len(names)))
        w('')
        w('| 仓库 | 直接依赖 | 一级下游 | 传递影响 | 本地 |')
        w('|---|---|---|---|---|')
        for n in shown:
            deps = sorted(repos[n]['deps'])
            w('| `%s` | %s | %d | %d | %s |' % (
                n,
                pubnames(repos, deps),
                len(down.get(n, ())),
                len(trans.get(n, ())),
                '✅' if repos[n]['local'] else '仅远端',
            ))
        if hidden:
            w('| _内部服务 ×%d_ | — | — | — | — |' % hidden)
        w('')

    w('---')
    w('')
    w('## 💥 影响面')
    w('')
    w('「一级下游」是发 tag 后**直接**收到 `upstream-updated` 的仓库数')
    w('「传递影响」是沿依赖边一路传下去最终会被波及的总数 —— 这个数字才是改动风险的真实量级')
    w('')
    w('| 仓库 | 一级下游 | 传递影响 | 一级下游清单 |')
    w('|---|---|---|---|')
    ranked = sorted(repos, key=lambda n: (-len(trans.get(n, ())), -len(down.get(n, ())), n))
    for n in ranked:
        d1 = sorted(down.get(n, ()))
        if not d1:
            continue
        if repos[n].get('internal'):
            continue
        w('| `%s` | **%d** | **%d** | %s |' % (
            n, len(d1), len(trans.get(n, ())),
            pubnames(repos, d1),
        ))
    w('')
    leaves = sorted(n for n in repos if not down.get(n) and not repos[n].get('internal'))
    w('没有任何下游的 %d 个（改它们不会联动到别人）：' % len(leaves))
    w('')
    w(' '.join('`%s`' % n for n in leaves))
    w('')

    w('---')
    w('')
    w('## 🚦 发版顺序')
    w('')
    w('按层序从第 0 层往上，每层内部互不依赖、可以并行')
    w('每发一层先等下游的 bump 流水线跑完再发下一层 —— `go-bump-upstream` 的三道闸门')
    w('（build / vet / test）会在这里拦住不兼容的升级，没过就开 PR 不碰主干')
    w('')
    for lv in sorted(bylevel):
        names = sorted(n for n in bylevel[lv] if down.get(n) and not repos[n].get('internal'))
        if not names:
            continue
        w('%d. **第 %d 层**：%s' % (lv + 1, lv, ' '.join('`%s`' % n for n in names)))
    w('')
    w('只在这一层出现、且没有下游的仓库不在上面的序列里，它们什么时候发都不影响别人')
    w('')

    unresolved = {n: v['unresolved'] for n, v in repos.items()
                  if v['unresolved'] and not v.get('internal')}
    if unresolved:
        w('---')
        w('')
        w('## ⚠️ 图里缺的边')
        w('')
        w('这些仓库 require 了 kamalyes 名下的模块，但那个模块对应的仓库没被纳入本图')
        w('（既不在本地、也没通过 `--extra-mods` 提供），所以这几条依赖边在上面的分层里是缺失的：')
        w('')
        w('| 仓库 | 没解析到的模块 |')
        w('|---|---|')
        for n in sorted(unresolved):
            w('| `%s` | %s |' % (n, ' '.join('`%s`' % m for m in unresolved[n])))
        w('')

    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workspace', required=True, help='放着各仓库的目录')
    ap.add_argument('--extra-mods', default='', help='放着 <repo>.mod 的目录，补上本地没 clone 的仓库')
    ap.add_argument('-o', '--output', default='', help='输出文件，留空打到标准输出')
    ap.add_argument('--internal-regex', default='',
                    help='匹配到的仓库只计数不列名，渲染成「内部服务」'
                         '；用于不该出现在公开文档里的仓库名')
    args = ap.parse_args()

    internal = re.compile(args.internal_regex) if args.internal_regex else None
    repos = collect(args.workspace, args.extra_mods)
    for name, info in repos.items():
        info['internal'] = bool(internal and internal.search(name))
    if not repos:
        print('在 %s 下没找到任何带 go.mod 的 git 仓库' % args.workspace, file=sys.stderr)
        return 1

    assigned, cycles = layer(repos)
    down = downstreams(repos)
    trans = transitive_downstreams(repos, down)
    text = render(repos, assigned, cycles, down, trans)

    # 兜底：内部仓库名一个都不许进产物
    # 只靠渲染时过滤不够 —— 漏一处就泄一处，所以在写出之前再整篇扫一遍
    # [EN] A final guard: not one internal repository name may reach the output. Filtering at
    # render time is not enough, because a single missed path leaks, so the whole document is
    # scanned once more before it is written
    if internal:
        leaked = sorted({n for n in repos if repos[n]['internal'] and n in text})
        if leaked:
            print('内部仓库名泄漏进了产物：%s' % ' '.join(leaked), file=sys.stderr)
            return 1

    if '。' in text:
        print('生成的文档里出现了中文句号，不该有', file=sys.stderr)
        return 1

    if args.output:
        io.open(args.output, 'w', encoding='utf-8').write(text)
        print('已写出 %s（%d 行，%d 个仓库，%d 层）' % (
            args.output, len(text.split('\n')), len(repos), max(assigned.values()) + 1), file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
