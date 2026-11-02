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

import base64
import io
import json
import os
import re
import shutil
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

def parsed_workflow(name):
    """解析一份 workflow，失败返回 None 让调用方报红，而不是整份自检抛栈

    这个失败是本仓库最常见的一种：run: 块里内嵌的 python 只要有一行顶格，
    就会跳出 YAML 块标量、整份文件解析失败 而抛栈的后果是其余检查一条都不跑 ——
    恰恰在改那些内嵌程序时最需要这份自检说话
    [EN] Returns None so the caller reports a red check instead of the whole self-test dying with
    a traceback. This is the most common failure here: one column-zero line inside an embedded
    python program escapes the YAML block scalar and fails the file, and a traceback would skip
    every remaining check at exactly the moment the suite is needed most
    """
    import yaml as _yaml
    try:
        with io.open(os.path.join(WF_DIR, name), encoding="utf-8") as fh:
            return _yaml.safe_load(fh)
    except Exception:
        return None


def check_workflows_parse(yaml_mod):
    section("每份 workflow 都是合法 YAML")
    paths = workflow_files()
    bad = []
    for path in paths:
        try:
            with io.open(path, encoding="utf-8") as fh:
                yaml_mod.safe_load(fh)
        except Exception as exc:
            bad.append("%s: %s" % (os.path.basename(path),
                                   str(exc).replace("\n", " ")[:200]))
    check(not bad, "全部 %d 份都能解析" % len(paths), "\n".join(bad) if bad else "")


def check_no_duplicate_keys(yaml_mod):
    """配置表里不能有重复键

    重复键在 YAML 里解析得干干净净：后出现的那个静默胜出，先出现的整个消失
    这里真出过事 —— 新增配置仓库那组键时，config_path 与已有的那个撞名，
    导出 CONFIG_PATH_TEMPLATE 的那个键就此消失，service-vars.sh 找不到服务自己
    那份配置，于是走「config_path 为空，跳过 ConfigMap」分支：所有服务都会部署成
    没有 ConfigMap、启动参数也不带 -f resources/… 的样子，而每一步都是绿的

    [EN] A duplicate key parses cleanly: the later one silently wins and the earlier one
    disappears entirely. This actually happened here — adding the config-repository keys collided
    with the existing config_path, removing the key that exported CONFIG_PATH_TEMPLATE, so
    service-vars.sh could not find the service's own config file and took the "no config_path,
    skip the ConfigMap" branch: every service would deploy with no ConfigMap and no
    -f resources/… argument, with every step reporting green
    """
    section("配置表里不能有重复键")

    found = []

    class _DupLoader(yaml_mod.SafeLoader):
        pass

    def _mapping(loader, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=True)
            if key in seen:
                found.append("%s（第 %d 行）" % (key, key_node.start_mark.line + 1))
            seen.add(key)
        return yaml_mod.SafeLoader.construct_mapping(loader, node, deep=deep)

    _DupLoader.add_constructor(
        yaml_mod.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
    with io.open(SCHEMA, encoding="utf-8") as fh:
        yaml_mod.load(fh, Loader=_DupLoader)

    check(not found, "schema.yaml 里没有重复键",
          "重复的键：\n" + "\n".join(found) if found else "")


def check_expression_length(yaml_mod):
    """含 ${{ }} 的标量不能超过 GitHub 的 21000 字符上限

    这条限制只落在「含至少一个 ${{ }} 的标量」上：GitHub 会把这种标量当模板整体解析
    不含表达式的标量没有这个上限，所以同样长的 run 体，把插值改成 env: 声明就合法

    越线的表现是 Invalid workflow file / Exceeded max expression length，
    整个工作流不给跑 —— 而且它报在标量的起始行，看起来像那一行有问题
    真发生过：渲染那一步本来 138 处插值、21577 字符，往里加一段 ConfigMap 生成就越线

    [EN] The 21000-character cap applies only to a scalar holding at least one ${{ }}, which
    GitHub parses as a template; a scalar with no expression has no such limit, so the same long
    run body becomes legal once its interpolations move into env:. Exceeding it rejects the entire
    workflow with "Exceeded max expression length", reported at the scalar's first line as though
    that line were at fault. This happened: the render step at 138 interpolations across 21577
    characters went over when a ConfigMap generator was appended.
    """
    section("含表达式的标量不能超 GitHub 的长度上限")

    LIMIT = 21000
    worst, offenders = (0, "", ""), []
    for path in workflow_files():
        doc = parsed_workflow(os.path.basename(path))
        if doc is None:
            continue
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                for key, value in (step or {}).items():
                    if not isinstance(value, str) or "${{" not in value:
                        continue
                    where = "%s / %s / %s.%s" % (os.path.basename(path), job_name,
                                                 step.get("name", "?"), key)
                    if len(value) > worst[0]:
                        worst = (len(value), where, value)
                    if len(value) > LIMIT:
                        offenders.append("%s: %d 字符" % (where, len(value)))

    check(not offenders, "每个含表达式的标量都在 %d 字符以内（最长 %d，%s）"
          % (LIMIT, worst[0], worst[1]),
          "\n".join(offenders) if offenders else "")


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
    names = set(re.findall(r'exported\.append\(\(\s*"([A-Z][A-Z0-9_]*)"', text))

    # workflow 自己的步骤往 GITHUB_ENV 写的中间量也算有出处
    #
    # 从 workflow 源码里扫 `echo "X=..." >> "$GITHUB_ENV"`，而不是维护一张白名单：
    # 白名单会漏 —— 加一个中间量忘了登记，这条检查就误报，久了没人信它
    #
    # [EN] Intermediate values a workflow's own steps write into GITHUB_ENV count as accounted
    # for. They are scanned out of the workflow sources rather than kept in a hand-written
    # allowlist, which would go stale: one unregistered value makes this check cry wolf, and a
    # check that cries wolf stops being read
    for path in workflow_files():
        body = io.open(path, encoding="utf-8").read()
        names |= set(re.findall(r'echo\s+"([A-Z][A-Z0-9_]*)=[^"]*"\s*>>\s*"?\$GITHUB_ENV', body))
        # 多行值写不进 `NAME=值` 一行，只能用 GITHUB_ENV 的 NAME<<分隔符 形式，
        # 既有 echo 写的也有 python fh.write 写的 —— 只认单行形式会把这些中间量
        # 当成没有出处，而它们恰恰是 env 块这类多行量的唯一写法
        # [EN] A multi-line value cannot be written as NAME=value on one line and must use
        # GITHUB_ENV's NAME<<delimiter form, written both by echo and by python. Recognizing only
        # the single-line form would call these unaccounted, and they are the only way to pass a
        # multi-line value such as an env block
        names |= set(re.findall(r'"([A-Z][A-Z0-9_]*)<<[A-Za-z_]', body))
    return names


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

    # 4. 配置表里每一节、每一个键都要在配置文档里出现
    #
    # 实测漏过一整节：golib 有 13 个键，管着所有 Go 库的 CI，
    # 而 03-configuration.md 里一行都没有
    # 键是逐个比对 YAML 路径（如 kube.hpa.enable），不是只看节名在不在
    #
    # [EN] Every section and every key in the schema has to appear in the configuration
    # document. A whole section had been missing: golib carries thirteen keys governing CI for
    # every Go library and the document did not mention one of them. Keys are matched by their
    # YAML path rather than by section name alone
    import yaml as _yaml
    schema = load_schema(_yaml)
    cfg_doc = io.open(os.path.join(docs_dir, "03-configuration.md"), encoding="utf-8").read()

    missing_sections, missing_keys = [], []
    for group, label in (("sections", "明文"), ("secret_sections", "密钥")):
        for sec, body in (schema.get(group) or {}).items():
            keys = (body or {}).get("keys") or {}
            if not keys:
                # 自由键值表（env / app）没有固定键，只要节名在文档里就算覆盖
                if "`%s`" % sec not in cfg_doc:
                    missing_sections.append("%s/%s" % (label, sec))
                continue
            if "`%s`" % sec not in cfg_doc:
                missing_sections.append("%s/%s（%d 个键）" % (label, sec, len(keys)))
                continue
            for k in keys:
                path = "%s.%s" % (sec, k)
                if path not in cfg_doc:
                    missing_keys.append("%s/%s" % (label, path))

    check(not missing_sections, "配置表里每一节都在 03-configuration.md 里",
          "文档里没有这些节：\n" + "\n".join(missing_sections) if missing_sections else "")
    check(not missing_keys, "配置表里每个键都在 03-configuration.md 里",
          "文档里没有这 %d 个键：\n" % len(missing_keys) + "\n".join(missing_keys)
          if missing_keys else "")

    # 5. README 必须链到每一篇 docs
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


def check_config_page(yaml_mod):
    """配置页面必须由 schema 生成，而且不许有外部引用。

    页面有 116 个字段，手抄一份第一次改 schema 就漂移 —— 而漂移的现象是「页面上没有
    这个字段，所以以为不用配」，不会有任何报错
    外部引用这条更要紧：这张页面是要往里粘私钥的，引一个字体或 CDN 就多一次外部请求

    [EN] The page must be generated from the schema and must carry no external reference. With
    116 fields a hand-made copy drifts the first time the schema changes, and the symptom is a
    field missing from the page, which nobody reads as an error. The external-reference rule
    matters more: private keys get pasted into this page, and one font or CDN link is one more
    outbound request.
    """
    section("配置页面")

    gen = os.path.join(ROOT, "scripts", "make-config-page.py")
    if not check(os.path.exists(gen), "存在 make-config-page.py"):
        return
    out = os.path.join(tempfile.mkdtemp(), "index.html")
    r = run([sys.executable, gen, "-o", out])
    if not check(r.returncode == 0, "页面能生成", r.stdout + r.stderr):
        return
    html = io.open(out, encoding="utf-8").read()

    # 字段数要和 schema 对得上：页面少一个字段，就有一个配置项没人能从界面填
    # [EN] The field count must match the schema, or a setting has no way in from the page
    schema = load_schema(yaml_mod)
    want = sum(len(b.get("keys") or {})
               for g in ("sections", "secret_sections") for b in schema[g].values())
    m = re.search(r'<script id="schema" type="application/json">(.*?)</script>', html, re.S)
    if check(m is not None, "页面里嵌了 schema"):
        model = json.loads(m.group(1).replace("\\u003c", "<"))
        got = sum(len(s["keys"]) for bucket in model.values() for s in bucket)
        check(got == want, "页面字段数与配置表一致（%d）" % want,
              "页面 %d 个，配置表 %d 个" % (got, want))

    # 外部引用：只许出现在 schema 的默认值里（goproxy 那种），不许是资源引用
    # [EN] A URL may appear only inside a schema default, never as a resource reference
    banned = [p for p in ("src=", "@import", "integrity=", "fetch(", "XMLHttpRequest",
                          "sendBeacon", "<form", "<iframe", "<link ")
              if p in html]
    check(not banned, "页面没有外部引用与上报", " ".join(banned) if banned else "")

    os.unlink(out)


def configmap_generator(path):
    """把 backend-deploy-k8s 里生成 ConfigMap 的那段 python 抽出来，存成可执行的文件。

    抽出来真跑，而不是读一遍觉得没问题：它产出的是要 kubectl apply 的 YAML，而处理的
    文件内容来自配置仓库、行数与缩进不可控 —— 拼错的现象是 apply 失败或键名不合规，
    报错里看不出是哪个文件写出来的

    [EN] Extracted and actually executed rather than eyeballed: it emits YAML destined for
    kubectl apply from files whose lines and indentation come from the config repository, and a
    mistake surfaces as a failed apply or an invalid key with nothing naming the file behind it
    """
    doc = parsed_workflow("backend-deploy-k8s.yml")
    if doc is None:
        return None
    script = None
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str) and "PYEOF" in step["run"]:
                script = step["run"]
                break
    if script is None:
        return None
    lines = script.split("\n")
    start = next(i for i, l in enumerate(lines) if "<<'PYEOF'" in l)
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "PYEOF")
    body = "\n".join(lines[start + 1:end])
    # 直接编译：YAML 的块标量剥离要是没把前导缩进去干净，python 会 IndentationError
    # 不逐行查缩进 —— 代码体内的 if 与 for 本来就有缩进，那样查会把正常代码判成坏的
    # [EN] Compiled rather than inspected line by line, since the normal indentation inside if
    # and for blocks would otherwise be flagged as broken
    try:
        compile(body, "<configmap-generator>", "exec")
    except SyntaxError:
        return False
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return True


def check_service_config_repo(yaml_mod):
    """业务配置来自中心仓库：配置项、凭据、目录约定，以及生成器真跑一遍。"""
    section("业务配置仓库")

    schema = load_schema(yaml_mod)
    keys = (schema["sections"]["service"].get("keys") or {})
    missing = [k for k in ("config_repo", "config_ref", "config_repo_path") if k not in keys]
    if not check(not missing, "配置表里有 config_repo / config_ref / config_repo_path", missing):
        return
    check(keys["config_repo_path"].get("default") == "{name}/{env}",
          "目录约定的默认值是 {name}/{env}", keys["config_repo_path"].get("default"))

    with io.open(os.path.join(WF_DIR, "backend-deploy-k8s.yml"), encoding="utf-8") as fh:
        deploy = fh.read()

    # 凭据：必须是 App token，不能是 GITHUB_TOKEN
    #
    # 这一条是防回退：有人把它改回 actions/checkout 或 GITHUB_TOKEN，报错会是
    # "Repository not found"，看起来像仓库名写错，排查会绕很久
    #
    # [EN] Guards against reverting to actions/checkout or GITHUB_TOKEN, whose failure reads
    # "Repository not found" and sends the reader looking for a typo in the repository name
    check("create-github-app-token" in deploy and "CONFIG_REPO_NAME" in deploy,
          "配置仓库用 App token 换取凭据")
    check(re.search(r'secret-sections:[^\n]*\bgo\b', deploy) is not None,
          "部署这步请求了 go 密钥节（App 凭据从那里来）")
    check(re.search(r'sections:[^\n]*\bservice\b', deploy) is not None,
          "部署这步请求了 service 节（config_repo 从那里来）")
    check("Repository not found" in deploy,
          "拉取失败时的报错点出「App 没装到该仓库」这一条")
    check("--sparse" in deploy, "只 sparse 取用到的那一层目录")

    # 生成器真跑：合法 YAML、内容逐字节往返、尾换行、两道闸门
    # [EN] The generator is executed: valid YAML, byte-identical round-trip, trailing newline
    # and both guards
    work = tempfile.mkdtemp()
    gen = os.path.join(work, "gen.py")
    state = configmap_generator(gen)
    if state is False:
        check(False, "生成器那段 python 剥离后能编译")
        return
    if not check(state is True, "能从 backend-deploy-k8s 里抽出 ConfigMap 生成器"):
        return

    own = "Name: demo\nPort: 8082\nLog:\n  Level: info\n"
    own_path = os.path.join(work, "config.dev.yaml")
    with io.open(own_path, "w", encoding="utf-8") as fh:
        fh.write(own)

    # 配置仓库那一层：刻意带上会被朴素引号拼坏的内容、一份不以换行结尾的、一个子目录
    # [EN] Deliberately includes content naive quoting would break, a value without a trailing
    # newline, and a subdirectory that must be ignored
    cmdir = os.path.join(work, "cfg")
    os.makedirs(os.path.join(cmdir, "nested"))
    files = {"feature-flags.json": '{"newUI": true, "tip": "a: b #c"}',
             "extra.yaml": "list:\n  - one\n  - two\n\nafter_blank: yes\n"}
    for name, body in files.items():
        with io.open(os.path.join(cmdir, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    with io.open(os.path.join(cmdir, "nested", "ignored.yaml"), "w", encoding="utf-8") as fh:
        fh.write("should: not appear\n")

    def generate(out, config_yaml=own_path, dirpath=cmdir):
        return run([sys.executable, gen], env={
            "OUT": out, "CONFIG_YAML_PATH": config_yaml,
            "CONFIGMAP_NAME": "demo-config-dev", "CONFIGMAP_NAMESPACE": "business",
            "CONFIGMAP_DIR": dirpath,
        })

    out_path = os.path.join(work, "configmap.yaml")
    r = generate(out_path)
    if check(r.returncode == 0, "生成器跑通", r.stdout + r.stderr):
        rendered, parse_err = None, ""
        try:
            with io.open(out_path, encoding="utf-8") as fh:
                rendered = yaml_mod.safe_load(fh)
        except Exception as exc:                                  # noqa: BLE001
            parse_err = "%s: %s" % (type(exc).__name__, exc)
        if check(isinstance(rendered, dict) and "data" in rendered,
                 "生成的 ConfigMap 是合法 YAML", parse_err or str(rendered)[:400]):
            data = rendered["data"]
            check(sorted(data) == ["config.dev.yaml", "extra.yaml", "feature-flags.json"],
                  "服务自己那份与配置仓库那一层的文件都在 data 里", sorted(data))
            check("ignored.yaml" not in data, "子目录里的文件没被带进来（不递归）",
                  sorted(data))
            wrong = [n for n, want in [("config.dev.yaml", own)] + list(files.items())
                     if data.get(n) != want]
            check(not wrong, "每个文件的内容逐字节往返一致", wrong)
            check(data.get("config.dev.yaml", "").endswith("\n"),
                  "源内容的尾换行保住了（用 |）")
            check(not data.get("feature-flags.json", "x").endswith("\n"),
                  "源内容没有尾换行时不凭空加（用 |-）")

    # 闸门一：明文 ConfigMap 里不许有私钥；公钥证书不受影响
    # [EN] No private key in a plaintext ConfigMap; a public certificate is unaffected
    keydir = os.path.join(work, "withkey")
    os.makedirs(keydir)
    with io.open(os.path.join(keydir, "tls.key"), "w", encoding="utf-8") as fh:
        fh.write("-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----\n")
    r = generate(os.path.join(work, "bad1.yaml"), dirpath=keydir)
    check(r.returncode != 0 and "私钥" in (r.stdout + r.stderr),
          "配置仓库里的私钥被挡住", (r.stdout + r.stderr)[-400:])

    certdir = os.path.join(work, "withcert")
    os.makedirs(certdir)
    with io.open(os.path.join(certdir, "ca.crt"), "w", encoding="utf-8") as fh:
        fh.write("-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n")
    r = generate(os.path.join(work, "ok1.yaml"), dirpath=certdir)
    check(r.returncode == 0, "公钥证书不被误挡", (r.stdout + r.stderr)[-400:])

    # 闸门二：与服务自己那份重名要报错，不能静默顶掉
    # [EN] A name clash with the service's own file must fail rather than silently win
    dupdir = os.path.join(work, "dup")
    os.makedirs(dupdir)
    with io.open(os.path.join(dupdir, "config.dev.yaml"), "w", encoding="utf-8") as fh:
        fh.write("OVERRIDDEN: true\n")
    r = generate(os.path.join(work, "bad2.yaml"), dirpath=dupdir)
    check(r.returncode != 0 and "同名" in (r.stdout + r.stderr),
          "与服务自己那份重名时报错而不是静默覆盖", (r.stdout + r.stderr)[-400:])

    # 目录不存在要报错并说清约定，而不是静默只挂一份
    # [EN] A missing directory fails with the convention spelled out, rather than silently
    # shipping only the service's own file
    # 点文件不能进 ConfigMap：带目录结构要靠 .gitkeep 占位，而键名就是文件名
    # [EN] Dotfiles must not enter the ConfigMap: a .gitkeep carries the directory structure and
    # a ConfigMap key is the file name
    os.makedirs(os.path.join(work, "cmdir2"), exist_ok=True)
    with io.open(os.path.join(work, "cmdir2", ".gitkeep"), "w", encoding="utf-8") as fh:
        fh.write("")
    r = generate(os.path.join(work, "dotfile.yaml"), dirpath=os.path.join(work, "cmdir2"))
    doc = yaml_mod.safe_load(io.open(os.path.join(work, "dotfile.yaml"), encoding="utf-8").read()) or {}
    check(r.returncode == 0 and ".gitkeep" not in (doc.get("data") or {}),
          "点文件（.gitkeep）不进 ConfigMap", sorted((doc.get("data") or {})))

    r = generate(os.path.join(work, "bad3.yaml"), dirpath=os.path.join(work, "nope"))
    check(r.returncode != 0, "config_repo 的目录不存在时报错", (r.stdout + r.stderr)[-300:])

    shutil.rmtree(work, ignore_errors=True)


def heredoc_body(delimiter, workflow="backend-deploy-k8s.yml"):
    """抽出某个 run: 块里 <<'分隔符' 的 heredoc 正文，已剥离 YAML 块标量的缩进

    经 yaml.safe_load 读 run: 的值，缩进由解析器剥离，而不是自己按行数空格 ——
    算错缩进正是这一类 bug 的来源：顶格的 import 会跳出块标量，整份 workflow 解析失败
    [EN] The body is taken from the parsed run: value so the block scalar's indentation is
    stripped by the parser rather than counted by hand, which is where this class of bug comes
    from: a column-zero import escapes the scalar and fails the whole workflow
    """
    doc = parsed_workflow(workflow)
    if doc is None:
        return None
    needle = "<<'%s'" % delimiter
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run = step.get("run")
            if not isinstance(run, str) or needle not in run:
                continue
            lines = run.split("\n")
            start = next(i for i, l in enumerate(lines) if needle in l)
            end = next(i for i in range(start + 1, len(lines))
                       if lines[i].strip() == delimiter)
            return "\n".join(lines[start + 1:end])
    return None


def check_config_encryption(yaml_mod):
    """配置仓库的密文：解密、补扫密钥、合并 env 块、挂载解开的配置文件。

    三段内嵌 python 都抽出来真跑：它们的产物分别是注进容器的环境变量、要 apply 的
    Secret、以及渲染进 Deployment 的 env 块 —— 错了的现象是 Pod 起不来或者拿到空值，
    而不是哪一步报错
    [EN] All three embedded programs are extracted and executed: they produce the container's
    environment, a Secret to apply, and the env block rendered into the Deployment, and a mistake
    shows up as a Pod that will not start or a service holding an empty value, not as a failure
    """
    section("配置仓库的加密")

    schema = load_schema(yaml_mod)
    cfg = ((schema.get("secret_sections") or {}).get("config") or {}).get("keys") or {}
    check("age_key" in cfg and cfg.get("age_key", {}).get("type") == "secret",
          "配置表里有 config.age_key，类型是 secret", sorted(cfg))

    with io.open(os.path.join(WF_DIR, "backend-deploy-k8s.yml"), encoding="utf-8") as fh:
        deploy = fh.read()

    check("secret-sections: kube app go config" in deploy,
          "部署这步请求了 config 密钥节（解密私钥从那里来）")
    # 固定版本：解密是每次部署的硬依赖，浮动版本哪天改了行为会让所有服务一起起不来
    # [EN] Pinned, since decryption is a hard dependency of every deploy
    check(re.search(r"SOPS_VERSION:\s*v\d+\.\d+\.\d+", deploy) is not None
          and "releases/latest" not in deploy,
          "sops 固定版本，不取 latest")
    check("( umask 077; printf '%s\\n' \"${AGE_KEY}\" > \"${RUNNER_TEMP}/age.key\" )" in deploy,
          "age 私钥以 umask 077 落盘，且 umask 在子 shell 里")
    check("age_key" in deploy and "::error::配置仓库里有密文，但没配解密私钥" in deploy,
          "有密文而没配私钥时报错并点出 age_key 这一项")
    # 密文必须从配置目录删掉：留在那里会被 ConfigMap 生成器当普通文件收进去
    # [EN] The ciphertext must leave the config directory or the ConfigMap generator sweeps it in
    check(re.search(r"# \[EN\] The ciphertext is removed[\s\S]{0,200}rm -f \"\$\{f\}\"", deploy)
          is not None, "解完把密文从配置目录删掉，避免被收进 ConfigMap")

    work = tempfile.mkdtemp(prefix="pipeline-enc-")
    try:
        _run_merge_py_checks(work)
        _run_dedup_py_checks(work)
        _run_files_py_checks(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # 下游消费方必须读合并后的那一份
    #
    # 同步 Secret 那步从 env 块解析密钥名单。它要是仍读 inputs.extra-env-block，
    # 只在配置仓库里声明的密钥会被 secretKeyRef 引用、却从没写进 Secret，
    # Pod 直接卡在 CreateContainerConfigError —— 这个回归在 YAML 上看不出任何异常
    # [EN] The secret sync parses the key list out of the env block. Reading
    # inputs.extra-env-block there would leave a secret declared only in the config repository
    # referenced by a secretKeyRef and never written into the Secret, stranding the Pod in
    # CreateContainerConfigError, and the YAML would look perfectly fine
    for step in ("🔐 Sync Secrets to Cluster", "🔍 Verify Injected Secrets"):
        chunk = deploy.split("- name: %s" % step, 1)[1].split("      - name:", 1)[0]
        check("EXTRA_ENV_BLOCK: ${{ env.MERGED_ENV_BLOCK }}" in chunk
              and "inputs.extra-env-block" not in chunk,
              "%s 读的是合并后的 env 块" % step,
              "仍在读 inputs.extra-env-block" if "inputs.extra-env-block" in chunk else "")

    # 文档里的 sops 加密命令必须是真能跑的那种
    #
    # creation_rules 按「输入文件的路径」匹配，所以
    #   sops --encrypt secrets.yaml > x.enc.yaml
    # 会报 no matching creation rules found 写错这一条的代价是每个照着抄的人都卡在
    # 同一个地方，而那句报错里完全看不出问题出在文档
    # [EN] The documented sops command must actually run: creation_rules match the path of the
    # input file, so redirecting a non-.enc. input into an .enc. file fails with "no matching
    # creation rules found", and everyone copying the line hits the same wall while the error
    # says nothing about the documentation being wrong
    cfg_doc = io.open(os.path.join(ROOT, "docs", "03-configuration.md"), encoding="utf-8").read()
    bad = [l.strip() for l in cfg_doc.split("\n")
           if l.strip().startswith("sops --encrypt")
           and ">" in l
           and "--filename-override" not in l
           and "--in-place" not in l]
    check(not bad, "文档里的 sops --encrypt 命令不会撞上 creation_rules",
          "\n".join(bad) if bad else "")

    # 名字带 .enc. 却没加密：要在 AGE_KEY 检查之前挡住
    #
    # 顺序是重点 放在后面的话，没配私钥的仓库会先报「没配解密私钥」，
    # 把「这个文件根本是明文」这个真正的诊断盖住 —— 而这两件事的处置完全不同：
    # 前者补个 secret 就行，后者意味着明文已经进过 git，密钥必须换掉
    # [EN] The plaintext check must precede the AGE_KEY check. Otherwise a repository without the
    # key reports "no decryption key configured" first and masks the real diagnosis, and the two
    # have entirely different remedies: one needs a secret added, the other means plaintext
    # reached git and the secrets must be rotated
    pos_plain = deploy.find("名字带 .enc. 但内容是明文")
    pos_key = deploy.find("配置仓库里有密文，但没配解密私钥")
    check(pos_plain != -1 and pos_key != -1 and pos_plain < pos_key,
          "未加密检查排在「没配私钥」检查之前",
          "明文检查位置 %d / 私钥检查位置 %d" % (pos_plain, pos_key))
    # 判定只看形态，不需要私钥 —— 否则没配 key 的仓库根本跑不到这个诊断
    # [EN] Shape only, so a repository without the key still reaches this diagnosis
    check('grep -qE \'^(sops:|[[:space:]]*"sops":)\'' in deploy,
          "未加密判定只看 sops 元数据块，不解密")
    check("对应的密钥要换掉" in deploy,
          "报错点出「已经进过 git，密钥要换掉」，而不只是让人重新加密")
    check("$GITHUB_STEP_SUMMARY" in deploy and "配置仓库里有未加密的密钥文件" in deploy,
          "未加密时往 job summary 写一段，便于通知里看到")

    # 换不到 token 时必须有一步把原因说出来
    #
    # create-github-app-token 只抛 GET /repos/<owner>/<repo>/installation 的裸 404，
    # 而下面 checkout 那步虽然写了「App 没装」的提示，mint 先挂，那句话永远打不出来
    # 这一步要用 if: failure() 才会执行，而且要点名是哪个仓库
    # [EN] A step must state the cause when minting fails: the action throws only a bare 404 and
    # the checkout step's hint never prints because minting fails first. It needs if: failure()
    # to run at all, and must name the repository
    chunk = deploy.split("- name: 🔑 Mint Token For Config Repo", 1)[1]
    chunk = chunk.split("- name: 📥 Fetch Service Config", 1)[0]
    check("failure()" in chunk and "steps.config-token.outcome == 'failure'" in chunk,
          "换不到 token 时有一步 if: failure() 兜住", chunk[-200:] if "failure()" not in chunk else "")
    check("没装到" in chunk and "${CONFIG_REPO}" in chunk,
          "提示点名了配置仓库，并说清是 App 没装而不是仓库名错")
    check("404 而不是 403" in deploy,
          "解释了 GitHub 为什么返回 404 —— 否则这个报错读起来像仓库不存在")

    # 「装到仓库上」与「读得到文件」要分开诊断
    #
    # 实际踩到：仓库早就加进 App 的清单了，mint 也成功，但 token 只有 metadata，
    # git clone 照样报 Repository not found —— 与「App 没装」一字不差
    # 原来这里一口断定是没装，指引是「加进仓库清单」，清单本来就对的那次改不动
    # 两道防线：mint 申请 contents:read（权限不够就在 mint 挂，GitHub 回 422），
    # clone 真失败时再用同一个 token 自检，分清是不在清单里还是权限没给
    # [EN] Being installed on the repository and being able to read its files must be diagnosed
    # apart: with access but no Contents permission the mint succeeds and the token carries only
    # metadata, so git clone answers "Repository not found" word for word as it does for an App
    # that is not installed. Asserting the former sent people to the repository list, which was
    # already right. Two guards: the mint requests contents:read so GitHub answers 422 when the
    # permission is absent, and a failing clone probes the same token to tell the two apart
    mint = yaml_mod.safe_load(deploy.split("- name: 🔑 Mint Token For Config Repo", 1)[1]
                              .split("- name:", 1)[0])
    check(mint.get("with", {}).get("permission-contents") == "read",
          "mint 这一步显式申请 contents:read，权限不够时在 mint 就挂",
          repr(mint.get("with")))

    fetch = deploy.split("- name: 📥 Fetch Service Config", 1)[1]
    fetch = fetch.split("- name: 🔓 Decrypt Service Config", 1)[0]
    check("installation/repositories" in fetch and "/contents/" in fetch,
          "clone 失败时用同一个 token 自检：既列安装清单，也试读仓库内容")
    check("已经在这个 App 的仓库清单里" in fetch and "Contents 给 Read-only" in fetch,
          "在清单里却读不到时，指向 Contents 权限而不是再让人去加仓库")
    check("Install App" in fetch and "加进去" in fetch,
          "不在清单里时仍然给出 Install App 那条修法")
    check("${CONFIG_REF} 这个分支不存在" in fetch,
          "凭据都正常时点到 ref 上，而不是继续怪 App")

    _check_projected_volume(yaml_mod)


def _run_merge_py_checks(work):
    """解密出来的扁平密钥：并进业务密钥文件，并补出 secretKeyRef 条目。"""
    body = heredoc_body("MERGE_PY")
    if not check(body is not None, "能抽出解密后的密钥合并程序"):
        return
    try:
        compile(body, "<merge>", "exec")
    except SyntaxError as exc:
        check(False, "密钥合并程序是合法 python", str(exc))
        return
    check(True, "密钥合并程序是合法 python")

    def run_merge(decrypted, existing=None):
        sj = os.path.join(work, "secrets.json")
        with io.open(sj, "w", encoding="utf-8") as fh:
            json.dump(decrypted, fh)
        app = os.path.join(work, "app-secrets.json")
        if existing is None:
            if os.path.exists(app):
                os.unlink(app)
            app_arg = ""
        else:
            with io.open(app, "w", encoding="utf-8") as fh:
                json.dump(existing, fh)
            app_arg = app
        genv = os.path.join(work, "github-env")
        io.open(genv, "w", encoding="utf-8").close()
        env = dict(os.environ, SECRETS_JSON=sj, SECRET_NAME="demo-gateway-secrets",
                   APP_SECRETS_FILE=app_arg, GITHUB_ENV=genv, RUNNER_TEMP=work)
        r = subprocess.run([sys.executable, "-I", "-"], input=body, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True)
        return r, io.open(genv, encoding="utf-8").read(), app_arg or os.path.join(
            work, "pipeline-app-secrets.json")

    r, genv, app = run_merge({"DEMO_JWT_ACCESS_SECRET": "s3cret-value",
                              "DEMO_REPORT_CLICKHOUSE_PASSWORD": "another-one"})
    if not check(r.returncode == 0, "扁平密钥合并跑通", r.stdout[-400:]):
        return
    check(genv.startswith("EXTRA_SECRET_ENV_BLOCK<<"),
          "补出来的 env 块用 GITHUB_ENV 的多行写法传递",
          genv[:80])
    entries = genv.split("\n", 1)[1].rsplit("EOF_SECRET_ENV_BLOCK", 1)[0]
    # 格式必须与 service-vars.sh 渲染的那份一致，否则两份合不到一起
    # [EN] The shape must match what service-vars.sh renders or the two halves cannot merge
    check(entries.strip() == (
        "- name: DEMO_JWT_ACCESS_SECRET\n  valueFrom:\n    secretKeyRef:\n"
        "      name: demo-gateway-secrets\n      key: DEMO_JWT_ACCESS_SECRET\n      optional: true\n"
        "- name: DEMO_REPORT_CLICKHOUSE_PASSWORD\n  valueFrom:\n    secretKeyRef:\n"
        "      name: demo-gateway-secrets\n      key: DEMO_REPORT_CLICKHOUSE_PASSWORD\n"
        "      optional: true"), "补出的条目与 service-vars.sh 的格式逐字一致",
        entries.strip()[:400])
    # 值要打码：后面任何一步不小心回显就会留在对所有有读权限的人可见的日志里
    # [EN] Values must be masked or a later step echoing one leaves it in a readable, retained log
    check("::add-mask::s3cret-value" in r.stdout and "::add-mask::another-one" in r.stdout,
          "每个解开的值都发了 ::add-mask::")
    with io.open(app, encoding="utf-8") as fh:
        merged = json.load(fh)
    check(merged == {"DEMO_JWT_ACCESS_SECRET": "s3cret-value",
                     "DEMO_REPORT_CLICKHOUSE_PASSWORD": "another-one"},
          "没有 app 节时自己建一份业务密钥文件", merged)
    check(oct(os.stat(app).st_mode & 0o777) == "0o600",
          "业务密钥文件权限是 0600", oct(os.stat(app).st_mode & 0o777))

    r, _genv, app = run_merge({"NEW_ONE": "x"}, existing={"OLD_ONE": "y"})
    with io.open(app, encoding="utf-8") as fh:
        merged = json.load(fh)
    check(r.returncode == 0 and merged == {"OLD_ONE": "y", "NEW_ONE": "x"},
          "与 app 节已有的密钥并存而不是顶掉", merged)

    # 撞名不猜哪边赢：两边迟早不一致，而不一致时服务拿到哪个值取决于一个看不见的默认
    # [EN] A clash picks no winner: the two will drift and the value served would depend on an
    # invisible default
    r, _genv, _app = run_merge({"SAME": "from-config-repo"}, existing={"SAME": "from-app"})
    check(r.returncode != 0 and "SAME" in r.stdout,
          "同名键在 app 节和配置仓库里都有时报错并点出键名", r.stdout[-200:])

    r, _genv, _app = run_merge({"NESTED": {"a": 1}})
    check(r.returncode != 0 and "NESTED" in r.stdout,
          "值是嵌套结构时报错，不静默转成字符串注进容器", r.stdout[-200:])

    r, _genv, _app = run_merge({"FLAG": True, "NUM": 3, "EMPTY": None})
    check(r.returncode == 0, "bool / 数字 / 空值转成环境变量能接受的字符串", r.stdout[-200:])

    # 只有明文配置扫出来的名字（没有任何密文）：也要补出 secretKeyRef
    #
    # 这是配置搬进中心仓库后最常见的形态：配置是明文的，密钥值在 GitHub Secrets 里，
    # 配置里只写变量名 setup 侧看不到这份文件，不补的话这些变量根本不会注进容器
    # [EN] The most common shape after the migration: the config is plaintext, the values live in
    # GitHub Secrets and the config only names the variables. The setup side cannot see this file,
    # so without the rescan those variables never reach the container
    genv = os.path.join(work, "github-env-plain")
    io.open(genv, "w", encoding="utf-8").close()
    nf = os.path.join(work, "plain-names")
    with io.open(nf, "w", encoding="utf-8") as fh:
        fh.write("DEMO_SERVICE_AUTH_SECRET\nDEMO_JWT_ACCESS_SECRET\n")
    env = dict(os.environ, SECRETS_JSON="", SECRET_NAME="demo-gateway-secrets",
               APP_SECRETS_FILE="", GITHUB_ENV=genv, RUNNER_TEMP=work,
               PLAIN_ENV_NAMES_FILE=nf)
    r = subprocess.run([sys.executable, "-I", "-"], input=body, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True)
    written = io.open(genv, encoding="utf-8").read()
    check(r.returncode == 0 and "key: DEMO_SERVICE_AUTH_SECRET" in written
          and "key: DEMO_JWT_ACCESS_SECRET" in written,
          "没有密文时，明文里的 *Env: 名字也补成 secretKeyRef", (r.stdout + written)[-300:])

    # 两个来源都有：按名字取并集，不能出现两条同名
    # [EN] Both sources present: the union by name, with no duplicate entry
    sj = os.path.join(work, "both.json")
    with io.open(sj, "w", encoding="utf-8") as fh:
        json.dump({"DEMO_JWT_ACCESS_SECRET": "v", "ONLY_IN_CIPHER": "v2"}, fh)
    genv = os.path.join(work, "github-env-both")
    io.open(genv, "w", encoding="utf-8").close()
    env = dict(env, SECRETS_JSON=sj, GITHUB_ENV=genv,
               APP_SECRETS_FILE=os.path.join(work, "both-app.json"))
    r = subprocess.run([sys.executable, "-I", "-"], input=body, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True)
    written = io.open(genv, encoding="utf-8").read()
    got = [l[len("- name: "):] for l in written.split("\n") if l.startswith("- name: ")]
    check(r.returncode == 0 and sorted(got) == sorted(set(got))
          and set(got) == {"DEMO_SERVICE_AUTH_SECRET", "DEMO_JWT_ACCESS_SECRET",
                           "ONLY_IN_CIPHER"},
          "两个来源取并集，同名只出一条", got)

    # 两边都没有：什么都不写，而不是写一个空块
    # [EN] Neither source: nothing is written, rather than an empty block
    genv = os.path.join(work, "github-env-none")
    io.open(genv, "w", encoding="utf-8").close()
    r = subprocess.run([sys.executable, "-I", "-"], input=body,
                       env=dict(env, SECRETS_JSON="", PLAIN_ENV_NAMES_FILE="",
                                GITHUB_ENV=genv),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True)
    check(r.returncode == 0 and io.open(genv, encoding="utf-8").read() == "",
          "两个来源都空时不往 GITHUB_ENV 写东西",
          io.open(genv, encoding="utf-8").read()[:120])


def _run_dedup_py_checks(work):
    """两份 env 块合并：按名字去重，且结果稳定。"""
    body = heredoc_body("DEDUP_PY")
    if not check(body is not None, "能抽出 env 块去重程序"):
        return
    try:
        compile(body, "<dedup>", "exec")
    except SyntaxError as exc:
        check(False, "env 块去重程序是合法 python", str(exc))
        return
    check(True, "env 块去重程序是合法 python")

    raw = os.path.join(work, "env-block.raw")
    with io.open(raw, "w", encoding="utf-8") as fh:
        fh.write("- name: A\n  value: \"from-service-repo\"\n"
                 "- name: B\n  valueFrom:\n    secretKeyRef:\n      name: s\n      key: b\n"
                 "\n"
                 "- name: A\n  valueFrom:\n    secretKeyRef:\n      name: s\n      key: a\n"
                 "- name: C\n  value: \"x\"\n")
    r = subprocess.run([sys.executable, "-I", "-"], input=body,
                       env=dict(os.environ, RAW=raw), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True)
    if not check(r.returncode == 0, "env 块去重跑通", r.stdout[-300:]):
        return
    names = [l[len("- name: "):] for l in r.stdout.split("\n") if l.startswith("- name: ")]
    check(names == ["A", "B", "C"], "重名只留一条，顺序按先出现", names)
    check('value: "from-service-repo"' in r.stdout and "key: a" not in r.stdout,
          "撞名时服务仓库那份胜出，结果不随运行漂移")
    # 空行会把块标量在模板里断开，渲染出来的 env 列表就少了后半截
    # [EN] A blank line breaks the block scalar in the template, truncating the rendered env list
    check("\n\n" not in r.stdout, "输出里没有空行")
    # 两份都空时要输出空，而不是一行孤零零的 "- name: "
    # [EN] Two empty halves must yield nothing rather than a stray entry
    io.open(raw, "w", encoding="utf-8").write("\n\n")
    r = subprocess.run([sys.executable, "-I", "-"], input=body,
                       env=dict(os.environ, RAW=raw), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True)
    check(r.returncode == 0 and r.stdout.strip() == "", "两份都空时输出为空", r.stdout[:80])


def _run_files_py_checks(work):
    """解开的配置文件同步成 Secret：内容逐字节往返，中间产物不混进去。"""
    body = heredoc_body("FILES_PY")
    if not check(body is not None, "能抽出配置文件 Secret 生成器"):
        return
    try:
        compile(body, "<files>", "exec")
    except SyntaxError as exc:
        check(False, "配置文件 Secret 生成器是合法 python", str(exc))
        return
    check(True, "配置文件 Secret 生成器是合法 python")

    plain = os.path.join(work, "config-plain")
    os.makedirs(plain, exist_ok=True)
    payload = "Name: demo-gateway\nAuth:\n  Secret: abc\n"
    with io.open(os.path.join(plain, "config.dev.yaml"), "w", encoding="utf-8") as fh:
        fh.write(payload)
    # secrets.json 的内容已经进了业务 Secret 当环境变量，再挂成文件等于多暴露一份
    # [EN] secrets.json already went in as variables; mounting it too exposes them twice
    with io.open(os.path.join(plain, "secrets.json"), "w", encoding="utf-8") as fh:
        fh.write('{"DEMO_JWT_ACCESS_SECRET": "x"}')
    os.makedirs(os.path.join(plain, "nested"), exist_ok=True)
    with io.open(os.path.join(plain, "nested", "deep.yaml"), "w", encoding="utf-8") as fh:
        fh.write("a: 1\n")

    env = dict(os.environ, CONFIG_FILES_DIR=plain,
               CONFIG_FILES_SECRET="demo-gateway-secrets-files", NAMESPACE="demo-dev")
    r = subprocess.run([sys.executable, "-I", "-"], input=body, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True)
    if not check(r.returncode == 0, "配置文件 Secret 生成器跑通", (r.stderr or "")[-300:]):
        return
    manifest = json.loads(r.stdout)
    check(manifest["kind"] == "Secret" and manifest["metadata"]["name"] == "demo-gateway-secrets-files"
          and manifest["metadata"]["namespace"] == "demo-dev",
          "生成的是指定命名空间下的 Secret", manifest.get("metadata"))
    check(sorted(manifest["data"]) == ["config.dev.yaml"],
          "secrets.json 与子目录都不进去", sorted(manifest["data"]))
    check(base64.b64decode(manifest["data"]["config.dev.yaml"]).decode() == payload,
          "文件内容逐字节往返一致")
    # 值不能回显：解开的配置落在对所有有读权限的人可见、且会留存的日志里
    # [EN] Contents must not be echoed into logs that are retained and broadly readable
    check("abc" not in r.stderr, "日志里不回显文件内容", r.stderr[-200:])

    for name in ("config.dev.yaml",):
        os.unlink(os.path.join(plain, name))
    r = subprocess.run([sys.executable, "-I", "-"], input=body, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True)
    check(r.returncode == 3, "没有要挂的文件时退 3（由 shell 判成跳过）", r.returncode)


def _check_projected_volume(yaml_mod):
    """Deployment 的 config volume 必须两个来源都挂得上，且两种情况都是合法 YAML。"""
    path = os.path.join(ROOT, "templates", "k8s", "backend", "deployment.yaml")
    text = io.open(path, encoding="utf-8").read()
    block = ("          - secret:\n              name: demo-gateway-secrets-files\n"
             "              optional: true")
    for label, src in (("没有解密出来的配置文件", ""), ("有解密出来的配置文件", block)):
        def sub(m, src=src):
            name = m.group(1)
            if name == "CONFIG_FILES_SOURCE_BLOCK":
                return src
            return "" if name.endswith("_BLOCK") else "x"
        rendered = re.sub(r"\$\{([A-Z][A-Z0-9_]*)\}", sub, text)
        try:
            doc = yaml_mod.safe_load(rendered)
        except Exception as exc:
            check(False, "%s时 Deployment 渲染后是合法 YAML" % label, str(exc))
            continue
        check(True, "%s时 Deployment 渲染后是合法 YAML" % label)
        vol = [v for v in doc["spec"]["template"]["spec"]["volumes"]
               if v.get("name") == "config"]
        if not check(len(vol) == 1 and "projected" in vol[0],
                     "%s时 config volume 是 projected" % label, vol):
            continue
        sources = vol[0]["projected"]["sources"]
        kinds = [k for s in sources for k in s]
        expected = ["configMap", "secret"] if src else ["configMap"]
        check(kinds == expected, "%s时来源是 %s" % (label, " + ".join(expected)), kinds)
        # 两个来源都 optional：任一侧不存在时 Pod 仍能起来，由服务自己报缺哪份配置
        # [EN] Both optional so the Pod still starts and the service reports what is missing
        check(all(list(s.values())[0].get("optional") is True for s in sources),
              "%s时每个来源都是 optional" % label, sources)


def check_config_moved_to_repo(yaml_mod):
    """配置搬进中心仓库之后，启动参数、ConfigMap 名与密钥名单都还得在

    这三件事原先全绑在「服务仓库里那份配置文件存在」上，所以搬家有两个陷阱：
      - 保持 service.config_path 不变 → 文件还得留在服务仓库 → 与配置仓库那份撞名
      - 把 config_path 配成空 → ConfigMap 名没了，容器启动参数 -f resources/… 也没了
    两条路都走不通，而第二条还特别阴：部署是绿的，服务起来却读不到配置

    [EN] Those three things used to be gated on the config file existing in the service
    repository, which left migration with two traps: keeping service.config_path means the file
    must stay and then collides with the copy in the config repository, while emptying it drops
    both the ConfigMap name and the container's -f resources/… argument. The second is the nasty
    one: the deploy goes green and the service comes up unable to read its config.
    """
    section("配置搬进中心仓库之后仍然要能部署")

    sv = os.path.join(ROOT, "scripts", "service-vars.sh")
    if not check(os.path.isfile(sv), "service-vars.sh 在"):
        return

    work = tempfile.mkdtemp(prefix="pipeline-moved-")
    try:
        base = {
            "GITHUB_REPOSITORY": "example-org/demo-gateway",
            "GITHUB_REF_NAME": "dev",
            "SERVICE_ENVIRONMENT": "dev",
            "BINARY_NAME": "demo-gateway",
            "CONFIG_PATH_TEMPLATE": "config/config.{env}.yaml",
            "CMD_ARGS_TEMPLATE": "-f resources/{configfile}",
        }

        # 1. 配了 config_repo、服务仓库里没有那份文件：正常路径
        out = os.path.join(work, "moved.out")
        env = dict(base, CONFIG_REPO="example-org/service-configs", GITHUB_OUTPUT=out)
        r = run(["bash", sv], env=env)
        if not check(r.returncode == 0, "本仓库没有配置文件但配了 config_repo 时不失败",
                     (r.stdout + r.stderr)[-500:]):
            return
        got = dict(l.split("=", 1) for l in io.open(out, encoding="utf-8").read().split("\n")
                   if "=" in l)
        check(got.get("cmd-args") == "-f resources/config.dev.yaml",
              "容器启动参数仍然按文件名算出来", got.get("cmd-args"))
        check(got.get("k8s-configmap-name") == "demo-gateway-config-dev",
              "ConfigMap 名仍然算得出来", got.get("k8s-configmap-name"))
        # 这一项必须为空：非空就会与配置仓库那份同名文件撞上，而撞名是硬错误
        # [EN] Must be empty, or it collides with the same-named file from the config repository
        check(got.get("k8s-config-yaml") == "",
              "不把本仓库那个不存在的路径当成 ConfigMap 来源", got.get("k8s-config-yaml"))

        # 2. 没配 config_repo：这才是真缺文件，要失败并说清楚
        out2 = os.path.join(work, "missing.out")
        r = run(["bash", sv], env=dict(base, GITHUB_OUTPUT=out2))
        text = r.stdout + r.stderr
        check(r.returncode != 0 and "缺少配置文件" in text,
              "没配 config_repo 而文件又不在时仍然失败", text[-300:])
        check("service.config_repo" in text,
              "报错里点出 service.config_repo 这条出路", text[-300:])
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # 3. 补扫 *Env: 的口径必须与 service-vars.sh 一致
    #
    # 两边一旦写法不同，就会出现「服务仓库里认得、配置仓库里不认」这种只在搬家后才暴露的
    # 差异，而表现是某个环境变量没注进去、服务带着空值起来
    # [EN] The two must use the same rule, or a declaration recognized in the service repository
    # is silently ignored in the config repository -- visible only after the migration, as one
    # variable missing and the service running with an empty value
    sv_text = io.open(sv, encoding="utf-8").read()
    deploy = io.open(os.path.join(WF_DIR, "backend-deploy-k8s.yml"), encoding="utf-8").read()
    # 比字面子串，不比手写转义的正则：这段本身就全是方括号，转义一写错，
    # 检查就变成永远绿的
    # [EN] Compared as a literal substring rather than a hand-escaped regex: the expression is all
    # brackets, and one escaping slip turns this check permanently green
    needle = "Env:[[:space:]]*[A-Z_][A-Z0-9_]*"
    check(needle in sv_text and needle in deploy,
          "部署侧补扫 *Env: 用的正则与 service-vars.sh 逐字一致",
          "service-vars.sh %s / 部署工作流 %s" % (needle in sv_text, needle in deploy))

    # 明文与「解开之后」两轮都要扫
    #
    # 明文那轮跳过 *.enc.*，解开的文件又落在 config-plain 下，所以少了第二轮，
    # 整份加密的配置里的 *Env: 声明就没人看 —— 和搬家前漏扫是同一个后果
    # [EN] The plaintext pass skips *.enc.* and the decrypted files land under config-plain, so
    # without the second pass the *Env: declarations inside a fully encrypted config go unseen,
    # with the same consequence as missing them before the migration
    check(deploy.count(needle) >= 2 and 'find "${RUNNER_TEMP}/config-plain"' in deploy,
          "明文与解密后各扫一轮 *Env:",
          "正则出现 %d 次" % deploy.count(needle))


def check_workdir_env(yaml_mod):
    """容器里要有 APP_WORKDIR / APP_DATA_DIR

    代码里要拼绝对路径时得有唯一来源，否则各处硬编码 /usr/local/services/<repo>，
    改 kube.run_workdir 时漏一处就是一个找不到文件的运行期故障

    注意这两个**不能**给配置文件用：三个服务都是裸 conf.MustLoad、没开 UseEnv()，
    yaml 里的 ${VAR} 不会被展开 配置里指镜像内的文件要写相对路径
    [EN] Code needing an absolute path must have one source of truth, or /usr/local/services/<repo>
    gets hardcoded in several places and changing kube.run_workdir leaves a runtime
    file-not-found. These must NOT be used from config files: all three services call
    conf.MustLoad without UseEnv(), so ${VAR} in yaml is never expanded
    """
    section("容器里注入工作目录与数据目录")

    deploy = io.open(os.path.join(WF_DIR, "backend-deploy-k8s.yml"), encoding="utf-8").read()
    for name in ("APP_WORKDIR", "APP_DATA_DIR"):
        check('export %s="${{ env.DOCKER_RUN_WORKDIR }}' % name in deploy,
              "渲染步导出 %s，取自 kube.run_workdir" % name)

    path = os.path.join(ROOT, "templates", "k8s", "backend", "deployment.yaml")
    text = io.open(path, encoding="utf-8").read()
    probe = {"APP_WORKDIR": "/usr/local/services/demo", "APP_DATA_DIR": "/usr/local/services/demo/data"}

    def sub(m):
        n = m.group(1)
        if n in probe:
            return probe[n]
        return "" if n.endswith("_BLOCK") else "x"

    rendered = re.sub(r"\$\{([A-Z][A-Z0-9_]*)\}", sub, text)
    try:
        doc = yaml_mod.safe_load(rendered)
    except Exception as exc:
        check(False, "渲染后仍是合法 YAML", str(exc))
        return
    envs = {e["name"]: e.get("value")
            for e in doc["spec"]["template"]["spec"]["containers"][0].get("env") or []
            if isinstance(e, dict) and "name" in e}
    for name, want in probe.items():
        check(envs.get(name) == want,
              "渲染后容器 env 里有 %s" % name, "实际 %r" % envs.get(name))
    # 数据目录必须在工作目录下 —— 否则相对路径与它对不上，两套路径观念并存
    # [EN] The data directory must sit under the workdir, or relative paths and this variable
    # describe two different places
    check(envs.get("APP_DATA_DIR", "").startswith(envs.get("APP_WORKDIR", "\0") + "/"),
          "APP_DATA_DIR 在 APP_WORKDIR 之下",
          "%s vs %s" % (envs.get("APP_WORKDIR"), envs.get("APP_DATA_DIR")))


MERMAID_TYPES = ("sequenceDiagram", "flowchart", "graph", "classDiagram", "stateDiagram",
                 "erDiagram", "gantt", "pie", "journey", "gitGraph", "mindmap", "timeline")


def mermaid_blocks(text):
    """取出每个 ```mermaid 块：(起始行, [(行号, 正文)] 或 None 表示没闭合)"""
    out, cur = [], None
    for n, line in enumerate(text.split("\n"), 1):
        if line.strip() == "```mermaid":
            cur = [n, []]
            continue
        if cur is not None and line.strip() == "```":
            out.append((cur[0], cur[1]))
            cur = None
            continue
        if cur is not None:
            cur[1].append((n, line))
    if cur is not None:
        out.append((cur[0], None))
    return out


def check_mermaid(_yaml_mod):
    """文档里的 mermaid 图：本地渲染不了，但致命写法可以静态查出来

    这几类错的共同点是**图照样显示**，只是显示成错的东西，所以肉眼 review 容易放过：
      围栏没闭合   后面整篇文档都被吞进代码块
      图类型打错   mermaid 直接报 parse error，页面上是一块红字
      标签里有角括号  被当 HTML 标签吃掉，文字凭空消失（<br/> 例外）
      参与者没声明  mermaid 会自动新建一条泳道，图上多出一列没人认识的东西

    [EN] Diagrams cannot be rendered here, but the fatal mistakes are statically detectable. What
    they share is that the diagram still renders -- just wrong -- so review tends to miss them: an
    unclosed fence swallows the rest of the document, a misspelled type renders as a parse error,
    angle brackets are eaten as HTML and the text vanishes, and an undeclared participant silently
    gets its own lane.
    """
    section("文档里的 mermaid 图")

    docs_dir = os.path.join(ROOT, "docs")
    problems, total = [], 0
    for name in sorted(os.listdir(docs_dir)):
        if not name.endswith(".md"):
            continue
        text = io.open(os.path.join(docs_dir, name), encoding="utf-8").read()
        for start, body in mermaid_blocks(text):
            total += 1
            if body is None:
                problems.append("%s:%d 代码围栏没闭合" % (name, start))
                continue
            first = next((l.strip() for _n, l in body if l.strip()), "")
            head = first.split()[0] if first.split() else ""
            # 比第一个 token，不用 startswith —— sequenceDiagramm 这种打错的
            # 用 startswith 会被判成合法（它确实以 sequenceDiagram 开头）
            # [EN] The first token is compared rather than a startswith, which would accept a
            # misspelling such as sequenceDiagramm
            if not any(head == ty or head.startswith(ty + "-") for ty in MERMAID_TYPES):
                problems.append("%s:%d 第一行不是已知图类型：%r" % (name, start, first[:40]))
                continue
            for n, line in body:
                stripped = re.sub(r"</?br\s*/?>", "", line)
                if ("<" in stripped or ">" in stripped) and not re.search(
                        r"-->|->>|-->>|--x|-x|==>", stripped):
                    problems.append("%s:%d 标签里有角括号，会被当 HTML：%s"
                                    % (name, n, line.strip()[:50]))
            if head == "sequenceDiagram":
                declared, used = set(), set()
                for _n, line in body:
                    m = re.match(r"\s*(?:participant|actor)\s+(\S+)", line)
                    if m:
                        declared.add(m.group(1))
                for _n, line in body:
                    m = re.match(r"\s*([A-Za-z0-9_]+)\s*(?:-->>|->>|-->|->|--x|-x)[+-]?"
                                 r"\s*([A-Za-z0-9_]+)\s*:", line)
                    if m:
                        used.update(m.groups())
                    m = re.match(r"\s*Note\s+(?:over|left of|right of)\s+"
                                 r"([A-Za-z0-9_,\s]+?)\s*:", line)
                    if m:
                        used.update(x.strip() for x in m.group(1).split(",") if x.strip())
                unknown = sorted(used - declared)
                if unknown:
                    problems.append("%s:%d 用到未声明的参与者：%s"
                                    % (name, start, " ".join(unknown)))

    check(total > 0, "docs 里有 mermaid 图（%d 张）" % total)
    check(not problems, "每张图都通过静态检查",
          "\n".join(problems) if problems else "")


HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const schema = html.match(/<script id="schema" type="application\/json">([\s\S]*?)<\/script>/)[1];
const js = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/)[1];
const stub = { textContent: schema, value: '', innerHTML: '', style: {}, addEventListener(){},
               querySelector(){return null}, querySelectorAll(){return []} };
global.document = { getElementById: () => stub, addEventListener: () => {},
                    querySelectorAll: () => [], createElement: () => ({...stub}) };
global.navigator = { clipboard: { writeText: async () => {} } };
const mod = {};
new Function('module', js + '\nmodule.exports={parseDoc,nest,render,check,INDEX,classify,emitLines};')(mod);
const { parseDoc, nest, render, classify, emitLines } = mod.exports;

// 内置模板：节名行都带注释，而嵌套全靠「冒号后面是不是空的」来判断
// 模板解析错的表现不是报错，是**每个键都变成未知键** —— 页面照样显示，只是全标红
const pick = id => html.match(new RegExp('<script id="' + id + '"[^>]*>([\\s\\S]*?)<\\/script>'))[1];
const tpl = pick('tpl-plain-src').replace(/\\u003c/g, '<').replace(/^\n/, '');
const rt = parseDoc(tpl);
const tplPaths = Object.keys(rt.flat);
const tplUnknown = tplPaths.filter(p => !classify(p));
// 改一个带注释的键：只许动那一行，注释要留着
let tplEdit = {ok: false, comment: false, others: -1};
const sp = rt.spans['kube.namespace'];
if (sp){
  const L = tpl.split('\n'), B = L.slice();
  L.splice(sp.start, sp.end - sp.start + 1, ...emitLines('kube.namespace', 'business', sp));
  let d = 0;
  for (let i = 0; i < Math.max(B.length, L.length); i++) if (i !== sp.start && B[i] !== L[i]) d++;
  tplEdit = {ok: parseDoc(L.join('\n')).flat['kube.namespace'] === 'business',
             comment: L[sp.start].includes('#'), others: d};
}

const sample = fs.readFileSync(process.argv[3], 'utf8');
const r = parseDoc(sample);
const pairs = Object.keys(r.flat).map(p => [p, r.flat[p]]);
const r2 = parseDoc(render(nest(pairs)));
// 比**排序后**的键值对，不是 JSON.stringify 对象
// 序列化会把同一节的键合并到一起，所以键的先后顺序本来就会变 —— 那是对的行为
// 用顺序敏感的比较会把这当成「文档被改坏」，而真正要守的是值不丢不变
// [EN] Sorted entries are compared rather than JSON.stringify of the objects: serialization
// groups keys of one section together so insertion order legitimately changes, and an
// order-sensitive comparison would read that as corruption. What must hold is that no value is
// lost or altered.
const norm = o => Object.keys(o).sort().map(k => k + '\u0000' + o[k]).join('\u0001');
const buckets = {};
for (const p of Object.keys(r.flat)) { const i = classify(p); buckets[p] = i ? i.bucket : null; }
console.log(JSON.stringify({
  keys: Object.keys(r.flat).length,
  unknown: r.unknown.length,
  roundtrip: norm(r.flat) === norm(r2.flat),
  buckets,
  tpl: {keys: tplPaths.length, badLines: rt.unknown.length,
        unknownKeys: tplUnknown.slice(0, 5), edit: tplEdit},
}));
"""


def check_config_page_roundtrip(_yaml_mod):
    """配置页面：右边贴 YAML、左边自动摆表单，两边实时双向

    双向同步最怕的是来回一趟把文档改坏 —— 而「改坏」在页面上看不出来：
    表单照样显示，复制出去的文档却少了一项或多了一层缩进
    所以这里把页面里那几个纯函数抽出来在 node 里真跑：解析一份带多行块标量的文档，
    序列化回去再解析，比两次的键值表

    另一半同样重要:每个键属于哪份文档 节名在两份里会重复（kube / docker / notify
    都既有明文键也有密钥键），判定只能按整条路径 —— 把 kube.kubeconfig 填进
    PIPELINE_CONFIG 是真发生过的错，而流水线读不到它且不会报错

    [EN] The page's pure functions are executed in node: a document with multi-line block scalars
    is parsed, serialized back and parsed again, and the two key/value maps compared. A two-way
    editor that corrupts the document shows nothing on screen -- the form still renders while the
    copied document has lost a key or gained an indent level. The other half matters as much:
    which document each key belongs to. Section names repeat across the two (kube, docker and
    notify each hold both kinds), so only the full path decides, and putting kube.kubeconfig into
    PIPELINE_CONFIG has actually happened -- the pipeline cannot read it and nothing complains.
    """
    section("配置页面的双向同步")

    node = shutil.which("node")
    if not check(node is not None, "有 node 可以跑页面里的逻辑",
                 "装了 node 才能验这组 —— GitHub runner 自带"):
        return

    work = tempfile.mkdtemp(prefix="cfgpage-")
    try:
        page = os.path.join(work, "index.html")
        r = run([sys.executable, os.path.join(ROOT, "scripts", "make-config-page.py"),
                 "-o", page])
        if not check(r.returncode == 0 and os.path.isfile(page), "页面能生成",
                     (r.stdout + r.stderr)[-300:]):
            return

        sample = os.path.join(work, "sample.yaml")
        with io.open(sample, "w", encoding="utf-8") as fh:
            fh.write(
                "service:\n  binary_name: demo-gateway\n"
                "ports:\n  http: 8082\n"
                "kube:\n  namespace: business\n  kubeconfig: |\n"
                "    apiVersion: v1\n    clusters:\n    - name: default\n"
                "go:\n  app_private_key: |\n"
                "    -----BEGIN RSA PRIVATE KEY-----\n    AAAA\n"
                "    -----END RSA PRIVATE KEY-----\n"
                "env:\n  LOG_LEVEL: info\n"
                "app:\n  DEMO_SECRET: not-real\n"
                "service:\n  no_such_key: x\n")
        harness = os.path.join(work, "harness.js")
        with io.open(harness, "w", encoding="utf-8") as fh:
            fh.write(HARNESS)

        r = run([node, harness, page, sample])
        if not check(r.returncode == 0, "页面逻辑能在 node 里跑起来",
                     (r.stdout + r.stderr)[-400:]):
            return
        try:
            got = json.loads(r.stdout.strip().split("\n")[-1])
        except ValueError as exc:
            check(False, "harness 输出是合法 JSON", "%s / %s" % (exc, r.stdout[-200:]))
            return

        check(got["unknown"] == 0, "示例文档每一行都认得出来",
              "%d 行没认出来" % got["unknown"])
        check(got["roundtrip"] is True,
              "解析 → 序列化 → 再解析，键值完全一致（含多行块标量；键的分组顺序会变，那是对的）")
        b = got["buckets"]
        # 同一个 kube 节里，namespace 是明文、kubeconfig 是密钥 —— 这条最容易错
        # [EN] Within one kube section, namespace is plaintext while kubeconfig is a secret
        check(b.get("kube.namespace") == "plain" and b.get("kube.kubeconfig") == "secret",
              "同一节里明文键与密钥键分别判对",
              "namespace=%s kubeconfig=%s" % (b.get("kube.namespace"), b.get("kube.kubeconfig")))
        check(b.get("env.LOG_LEVEL") == "plain" and b.get("app.DEMO_SECRET") == "secret",
              "自由键值表（env / app）也按节归属判对",
              "env=%s app=%s" % (b.get("env.LOG_LEVEL"), b.get("app.DEMO_SECRET")))
        check(b.get("service.no_such_key") is None,
              "schema 里没有的键被标成未知，而不是悄悄归到某一份")
        t = got["tpl"]
        check(t["badLines"] == 0 and t["keys"] > 80,
              "内置明文模板能完整解析（%d 个键）" % t["keys"],
              "%d 行没认出来" % t["badLines"])
        # 模板解析错的表现不是报错，是每个键都成了未知键 —— 页面照样显示，只是全标红
        # 真踩过：节名行的注释剥不掉，`ports:  # 注释` 被当成有值的标量、不入栈，
        # 于是整份文档的嵌套全平，108 个路径变成 ports / http / rpc 这样的裸键
        # [EN] A misparsed template does not raise: every key simply becomes unknown, and the page
        # still renders with everything flagged red. This happened: the comment on a section line
        # could not be stripped, `ports:  # comment` was taken as a scalar and never pushed onto
        # the stack, flattening the whole document into bare keys
        check(not t["unknownKeys"],
              "模板里每个键 schema 都认得（嵌套没被解析成扁平）",
              "这些成了未知键：%s" % " ".join(t["unknownKeys"]))
        e = t["edit"]
        check(e["ok"] and e["comment"] and e["others"] == 0,
              "改模板里一个值，只动那一行且注释留着",
              "读回正确=%s 注释在=%s 其它行被动=%s" % (e["ok"], e["comment"], e["others"]))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    try:
        import yaml
    except ImportError:
        print("::error title=缺少 PyYAML | PyYAML missing::"
              "本脚本与 resolve-config.py 都需要 PyYAML，先 pip install pyyaml")
        return 2

    print("流水线自检 | Pipeline self-test")
    check_workflows_parse(yaml)
    check_expression_length(yaml)
    check_no_duplicate_keys(yaml)
    check_schema_round_trip(yaml)
    check_validate_loop_covers_all_sections(yaml)
    check_zero_config(yaml)
    check_env_coverage(yaml)
    check_config_page(yaml)
    check_config_page_roundtrip(yaml)
    check_service_config_repo(yaml)
    check_config_encryption(yaml)
    check_config_moved_to_repo(yaml)
    check_docs(yaml)
    check_mermaid(yaml)
    check_templates(yaml)
    check_workdir_env(yaml)

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
