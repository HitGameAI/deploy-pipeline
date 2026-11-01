#!/usr/bin/env python3
# ============================================================================
# 流水线自检 | Pipeline self-test
#
# docs/11-self-check.md 原先是一张手工打勾的清单，勾是人填的、没人回去重跑，
# 于是「每个 workflow 用到的 env 都由它请求的节导出」这种条目永远是 ✅ 而从不验证
# 这个脚本把那些条目变成真跑的断言，由 self-test.yml 在每次 push 时执行
#
# 本地跑：python3 scripts/self-test.py        需要 PyYAML
# [EN] The self-check list used to be ticked by hand, so entries such as "every env the
# workflows use is exported by the sections they request" stayed green without ever being
# verified. This script turns those entries into assertions that run on every push.
# ============================================================================

import io
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF_DIR = os.path.join(ROOT, ".github", "workflows")
SCHEMA = os.path.join(ROOT, "scripts", "config", "schema.yaml")
RESOLVE = os.path.join(ROOT, "scripts", "resolve-config.py")

# runner 与 github 自带的变量，不该由配置表导出
RUNNER_PROVIDED = {
    "CI", "GITHUB_ACTIONS", "GITHUB_ACTOR", "GITHUB_ENV", "GITHUB_EVENT_NAME",
    "GITHUB_OUTPUT", "GITHUB_REF_NAME", "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_OWNER",
    "GITHUB_SERVER_URL", "GITHUB_SHA", "GITHUB_STEP_SUMMARY", "GITHUB_TOKEN",
    "GITHUB_WORKSPACE", "HOME", "IFS", "PATH", "RUNNER_OS", "RUNNER_TEMP",
}

failures = []
checks = 0


def check(ok, title, detail=""):
    global checks
    checks += 1
    if ok:
        print("  ✅ %s" % title)
        return True
    print("  ❌ %s" % title)
    if detail:
        for line in str(detail).rstrip().split("\n"):
            print("       %s" % line)
    failures.append(title)
    return False


def section(name):
    print("\n── %s" % name)


def run(args, env=None, cwd=ROOT):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(args, capture_output=True, text=True, env=merged, cwd=cwd)


def load_schema(yaml_mod):
    return yaml_mod.safe_load(io.open(SCHEMA, encoding="utf-8"))


def workflow_files():
    return sorted(
        os.path.join(WF_DIR, f) for f in os.listdir(WF_DIR) if f.endswith((".yml", ".yaml"))
    )


# ==================== 配置表自身 | The schema itself ====================

def check_schema_round_trip(yaml_mod):
    section("配置表：示例能通过自己的校验")

    r = run([sys.executable, RESOLVE, "--print-example"])
    if not check(r.returncode == 0, "--print-example 退出码为 0", r.stderr):
        return
    example = r.stdout
    check(len(example.strip()) > 0, "--print-example 有输出")

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        fh.write(example)
        path = fh.name
    try:
        r = run([sys.executable, RESOLVE, "--check", path])
        check(r.returncode == 0, "生成的示例能通过 --check", r.stdout + r.stderr)
    finally:
        os.unlink(path)

    # 逐节：每一节单独生成、单独校验，避免某一节只有在总表里才合法
    #
    # 契约要分清：--print-example <sec> 产出的是**总表形状**（带 sec: 外层），
    # 所以要不带 --section 校验；而 --check --section <sec> 要的是**分节形状**
    # （不带外层，即写进 vars.<SEC>_CONFIG 的那份内容）
    # 两者都要测到，否则分节变量这条路可能坏了而没人发现
    #
    # [EN] Two shapes, two contracts: --print-example <sec> emits the aggregate shape with the
    # section key, so it validates without --section, while --check --section expects the bare
    # body that goes into the per-section variable. Both paths are exercised here, because the
    # per-section route can break without anyone noticing
    schema = load_schema(yaml_mod)
    for sec in (schema.get("sections") or {}):
        r = run([sys.executable, RESOLVE, "--print-example", sec])
        if r.returncode != 0:
            check(False, "单节 %s 能生成示例" % sec, r.stderr)
            continue

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
            fh.write(r.stdout)
            wrapped = fh.name
        # 剥掉外层，得到分节变量该写的形状
        body = []
        for line in r.stdout.split("\n"):
            if line.startswith("#") or not line.strip():
                continue
            if line.startswith(sec + ":"):
                continue
            body.append(line[2:] if line.startswith("  ") else line)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(body) + "\n")
            bare = fh.name
        try:
            r2 = run([sys.executable, RESOLVE, "--check", wrapped])
            check(r2.returncode == 0, "单节 %s 的总表形状示例能通过 --check" % sec,
                  r2.stdout + r2.stderr)
            if body:
                r3 = run([sys.executable, RESOLVE, "--check", bare, "--section", sec])
                check(r3.returncode == 0, "单节 %s 的分节形状能通过 --check --section" % sec,
                      r3.stdout + r3.stderr)
        finally:
            os.unlink(wrapped)
            os.unlink(bare)


def check_validate_loop_covers_all_sections(yaml_mod):
    section("generate-config 的逐节试必须覆盖配置表里每一节")

    schema = load_schema(yaml_mod)
    want = set(schema.get("sections") or {})
    text = io.open(os.path.join(WF_DIR, "generate-config.yml"), encoding="utf-8").read()

    # 节名从 schema 读才算合格；写死一串字面量迟早漏节
    derived = "schema.yaml" in text and "sections" in text
    literal = re.search(r"for s in ((?:[a-z_]+ )+[a-z_]+); do", text)
    if literal:
        missing = sorted(want - set(literal.group(1).split()))
        check(not missing, "逐节试的清单覆盖所有节",
              "写死的清单漏了：%s（建议改成从 schema 读）" % " ".join(missing) if missing else "")
    else:
        check(derived, "逐节试的节名从配置表读取，不会漏节")


def check_zero_config(yaml_mod):
    section("零配置：完全不配 vars 与 secrets 时默认值本身必须合法")

    schema = load_schema(yaml_mod)
    secs = " ".join(schema.get("sections") or {})
    ssecs = " ".join(schema.get("secret_sections") or {})

    tmp = tempfile.mkdtemp()
    env = {
        "GITHUB_ENV": os.path.join(tmp, "env"),
        "GITHUB_OUTPUT": os.path.join(tmp, "out"),
        "GITHUB_STEP_SUMMARY": os.path.join(tmp, "summary"),
        "CONFIG_SECTIONS": secs,
        "CONFIG_SECRET_SECTIONS": ssecs,
        "CONFIG_VARS_JSON": "{}",
        "CONFIG_INPUTS_JSON": "{}",
        "CONFIG_SECRETS_JSON": "{}",
    }
    r = run([sys.executable, RESOLVE], env=env)
    if check(r.returncode == 0, "所有节一起解析，退出码为 0", r.stdout[-3000:] + r.stderr[-3000:]):
        exported = io.open(env["GITHUB_ENV"], encoding="utf-8").read()
        check(len(exported.strip().split("\n")) > 0, "有内容写进 GITHUB_ENV")


# ==================== workflow 与配置表的一致性 ====================

def declared_env_names(text):
    """收集 workflow 里自己声明或赋值的变量名，这些不该由配置表导出"""
    names = set()

    # env: 块下的键，不限层级
    in_env = False
    env_indent = 0
    for line in text.split("\n"):
        m = re.match(r"^(\s*)env:\s*$", line)
        if m:
            in_env = True
            env_indent = len(m.group(1))
            continue
        if in_env:
            m = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*):", line)
            if m and len(m.group(1)) > env_indent:
                names.add(m.group(2))
                continue
            if line.strip() == "":
                continue
            in_env = False

    # shell 赋值：NAME=... / export NAME= / read NAME / NAME="$(...)"
    names |= set(re.findall(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=", text, re.M))
    names |= set(re.findall(r"\bread\s+(?:-r\s+)?([A-Z][A-Z0-9_]*)", text))
    names |= set(re.findall(r"\bfor\s+([A-Z][A-Z0-9_]*)\s+in\b", text))
    # python 的 os.environ["NAME"] 读的是上面某处声明的，不额外计入
    return names


def requested_sections(text):
    """该 workflow 向 resolve-config 请求的节；只认 with: 下真正的参数行"""
    plain, secret = set(), set()
    for m in re.finditer(r"^\s{10,}sections:\s*([a-z0-9 _-]+)\s*$", text, re.M):
        plain.update(m.group(1).split())
    for m in re.finditer(r"^\s{10,}secret-sections:\s*([a-z0-9 _-]+)\s*$", text, re.M):
        secret.update(m.group(1).split())
    return plain, secret


def synthetic_exports():
    """resolve-config.py 自己合成、不走配置表的导出名

    free-form 的 app 节不在 schema 里逐键声明，它的临时文件路径由脚本直接导出，
    所以这类名字要从脚本源码里取，否则 env 覆盖检查会把它当成没有出处
    [EN] The free-form app section is not declared key by key in the schema, so the path it
    exports is read out of the script itself, or the coverage check would call it unaccounted
    """
    text = io.open(RESOLVE, encoding="utf-8").read()
    return set(re.findall(r'exported\.append\(\(\s*"([A-Z][A-Z0-9_]*)"', text))


def section_envs(schema, group, sec):
    out = set()
    body = (schema.get(group) or {}).get(sec) or {}
    for spec in (body.get("keys") or {}).values():
        e = (spec or {}).get("env")
        if isinstance(e, list):
            out.update(e)
        elif e:
            out.add(e)
    return out


def check_env_coverage(yaml_mod):
    section("workflow 用到的 env 必须由它请求的节导出")

    schema = load_schema(yaml_mod)
    known_sections = set(schema.get("sections") or {})
    known_secret = set(schema.get("secret_sections") or {})

    for path in workflow_files():
        name = os.path.basename(path)
        text = io.open(path, encoding="utf-8").read()
        plain, secret = requested_sections(text)
        if not plain and not secret:
            continue

        unknown = sorted((plain - known_sections) | (secret - known_secret))
        check(not unknown, "%s 请求的节都在配置表里" % name,
              "配置表里没有这些节：%s" % " ".join(unknown) if unknown else "")

        exported = set(synthetic_exports())
        for s in plain & known_sections:
            exported |= section_envs(schema, "sections", s)
        for s in secret & known_secret:
            exported |= section_envs(schema, "secret_sections", s)

        # 先去掉注释行：正文里常用 ${{ env.XXX }} 这样的占位写法举例说明，
        # 不去掉会把 XXX 当成一个真实引用报出来
        # [EN] Comment lines are dropped first, because they use placeholder forms such as
        # env.XXX to explain a convention and would otherwise be read as real references
        scannable = "\n".join(
            l for l in text.split("\n") if not l.lstrip().startswith("#")
        )
        used = set(re.findall(r"\benv\.([A-Z][A-Z0-9_]*)", scannable))
        local = declared_env_names(text)
        missing = sorted(used - exported - local - RUNNER_PROVIDED)
        check(not missing, "%s 的 env 引用都有出处" % name,
              "用到但没有任何来源：%s" % " ".join(missing) if missing else "")


# ==================== 文档与实现的一致性 ====================

def check_docs(_yaml_mod):
    section("文档必须跟上实现")

    md_files = [os.path.join(ROOT, "README.md")]
    docs_dir = os.path.join(ROOT, "docs")
    md_files += sorted(os.path.join(docs_dir, f) for f in os.listdir(docs_dir) if f.endswith(".md"))

    # 1. 每个 workflow 都要在工作流文档里出现
    wf_doc = io.open(os.path.join(docs_dir, "02-workflows.md"), encoding="utf-8").read()
    undocumented = [
        os.path.basename(p) for p in workflow_files()
        if os.path.basename(p) not in wf_doc
    ]
    check(not undocumented, "每个 workflow 都在 02-workflows.md 里有说明",
          "没写进文档的：%s" % " ".join(undocumented) if undocumented else "")

    # 2. 中文句号一个都不能有
    with_period = []
    for p in md_files:
        n = io.open(p, encoding="utf-8").read().count("。")
        if n:
            with_period.append("%s（%d 个）" % (os.path.relpath(p, ROOT), n))
    check(not with_period, "文档里没有中文句号",
          "\n".join(with_period) if with_period else "")

    # 3. 站内链接都要能解析到真实文件
    dead = []
    for p in md_files:
        base = os.path.dirname(p)
        for m in re.finditer(r"\]\(([^)#:]+\.md)(?:#[^)]*)?\)", io.open(p, encoding="utf-8").read()):
            target = os.path.normpath(os.path.join(base, m.group(1)))
            if not os.path.exists(target):
                dead.append("%s -> %s" % (os.path.relpath(p, ROOT), m.group(1)))
    check(not dead, "文档里的站内链接都有效", "\n".join(dead) if dead else "")

    # 4. README 必须链到每一篇 docs
    readme = io.open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    unlinked = [
        os.path.basename(p) for p in md_files[1:]
        if os.path.basename(p) not in readme
    ]
    check(not unlinked, "README 链到了每一篇 docs",
          "README 没有链到：%s" % " ".join(unlinked) if unlinked else "")


# ==================== 模板 | Templates ====================

def check_templates(_yaml_mod):
    section("K8s 模板占位符必须都有对应的配置项")

    tpl_dir = os.path.join(ROOT, "templates")
    if not os.path.isdir(tpl_dir):
        check(False, "templates 目录存在", "找不到 %s" % tpl_dir)
        return

    found = []
    for dirpath, _dirs, files in os.walk(tpl_dir):
        for f in files:
            if f.endswith((".yaml", ".yml")):
                found.append(os.path.join(dirpath, f))
    check(len(found) > 0, "找到 K8s 模板文件（%d 个）" % len(found))

    # 模板里用 ${VAR} 占位，这些名字要么来自配置表，要么由 workflow 自己算出来
    schema_envs = set()
    import yaml as _y
    schema = load_schema(_y)
    for group in ("sections", "secret_sections"):
        for sec in (schema.get(group) or {}):
            schema_envs |= section_envs(schema, group, sec)

    wf_text = "\n".join(io.open(p, encoding="utf-8").read() for p in workflow_files())
    wf_local = declared_env_names(wf_text)

    orphan = {}
    for p in found:
        text = io.open(p, encoding="utf-8").read()
        for var in set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)\}", text)):
            if var not in schema_envs and var not in wf_local and var not in RUNNER_PROVIDED:
                orphan.setdefault(os.path.relpath(p, ROOT), []).append(var)
    check(not orphan, "模板占位符都有来源",
          "\n".join("%s: %s" % (k, " ".join(sorted(v))) for k, v in orphan.items()) if orphan else "")


def main():
    try:
        import yaml
    except ImportError:
        print("::error title=缺少 PyYAML | PyYAML missing::"
              "本脚本与 resolve-config.py 都需要 PyYAML，先 pip install pyyaml")
        return 2

    print("流水线自检 | Pipeline self-test")
    check_schema_round_trip(yaml)
    check_validate_loop_covers_all_sections(yaml)
    check_zero_config(yaml)
    check_env_coverage(yaml)
    check_docs(yaml)
    check_templates(yaml)

    print("\n" + "=" * 60)
    if failures:
        print("%d / %d 项不通过：" % (len(failures), checks))
        for f in failures:
            print("  - %s" % f)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with io.open(summary, "a", encoding="utf-8") as fh:
                fh.write("## ❌ 流水线自检 %d / %d 项不通过\n\n" % (len(failures), checks))
                for f in failures:
                    fh.write("- %s\n" % f)
        return 1

    print("%d 项全部通过" % checks)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with io.open(summary, "a", encoding="utf-8") as fh:
            fh.write("## ✅ 流水线自检 %d 项全部通过\n" % checks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
