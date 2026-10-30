#!/usr/bin/env python3
# ============================================================================
# 聚合配置解析器 | Aggregate pipeline configuration resolver
#
# 做三件事
#   1. 校验：把 vars.PIPELINE_CONFIG / vars.<SECTION>_CONFIG / secrets.PIPELINE_SECRETS
#      当 YAML 解析，逐项对照 scripts/config/schema.yaml 校验键名与取值，
#      错了就带路径、实际值、期望形式一次性全报出来，不是报一个停一个
#   2. 合并：分节 YAML > 总表 YAML > workflow input > 旧式单个 vars > schema 默认值
#   3. 导出：写进 GITHUB_ENV 供后续 step 用 ${{ env.XXX }} 取；
#      密钥逐个 ::add-mask:: 打码，业务密钥落到 RUNNER_TEMP 下 0600 的文件
#
# 本地用法 | Local usage
#   python3 scripts/resolve-config.py --check my-config.yaml           # 校验总表
#   python3 scripts/resolve-config.py --check kube.yaml --section kube # 校验单节
#   python3 scripts/resolve-config.py --print-example                  # 打印带注释的完整示例
#   python3 scripts/resolve-config.py --print-example kube docker      # 只打印这几节
#
# [EN] Validates the aggregate YAML against scripts/config/schema.yaml, merges it over the
# workflow inputs, the legacy per-key variables and the schema defaults, then exports the
# result through GITHUB_ENV. Every error is reported in one pass with its path, the actual
# value and the expected shape. Secret leaves are masked individually.
# ============================================================================

import difflib
import hashlib
import json
import os
import re
import sys
import uuid

try:
    import yaml
except ImportError:  # pragma: no cover - 由 action 负责装好 PyYAML | installed by the action
    print("::error::缺少 PyYAML，无法解析 YAML 配置 | PyYAML is required", file=sys.stderr)
    sys.exit(1)


# ==================== 输出辅助 | Output helpers ====================

IS_ACTIONS = bool(os.environ.get("GITHUB_ACTIONS"))


def log(msg=""):
    print(msg, file=sys.stderr)


def fail(lines):
    """把收集到的错误一次性报完再退出 | Report every collected error at once, then exit."""
    head = "配置校验失败，共 %d 处 | %d configuration problem(s)" % (len(lines), len(lines))
    if IS_ACTIONS:
        # ::error:: 只认一行，所以多行用 %0A 编码，GitHub 的 annotation 里会还原成换行
        # [EN] ::error:: is single-line; %0A encodes the newlines back in the annotation
        log("::error::" + head + "%0A" + "%0A".join(lines))
    log("")
    log("❌ " + head)
    for line in lines:
        log("   " + line)
    log("")
    sys.exit(1)


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()[:8]


# ==================== YAML 解析 | YAML parsing ====================

def parse_yaml(text, origin, errors):
    """解析一段 YAML；语法错误带行列号报出来，像 k3s 校验配置文件那样。

    [EN] Parse one YAML document, reporting syntax errors with line and column.
    """
    if text is None or not text.strip():
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        where = ""
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            where = " 第 %d 行第 %d 列" % (mark.line + 1, mark.column + 1)
            raw = text.split("\n")
            if 0 <= mark.line < len(raw):
                where += "：%s" % raw[mark.line].rstrip()
        problem = getattr(exc, "problem", None) or str(exc).split("\n")[0]
        errors.append("%s: YAML 语法错误%s（%s）| invalid YAML" % (origin, where, problem))
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        errors.append("%s: 顶层必须是键值表，实际是 %s | top level must be a mapping"
                      % (origin, type(data).__name__))
        return {}
    return data


def leaves(node, prefix=""):
    """把嵌套 YAML 压平成「点号路径 → 值」，便于逐项对照 schema。

    [EN] Flatten nested YAML into dotted paths so each leaf can be checked against the schema.
    """
    out = {}
    for key, value in node.items():
        key = str(key)
        path = "%s.%s" % (prefix, key) if prefix else key
        if isinstance(value, dict) and value:
            out.update(leaves(value, path))
        else:
            out[path] = value
    return out


def deep_merge(base, overlay):
    """overlay 覆盖 base：子对象逐层合并，标量直接替换。

    [EN] Overlay wins: mappings merge level by level, scalars replace.
    """
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# ==================== 取值校验 | Value validation ====================

QUANTITY_RE = re.compile(r"^\d+(\.\d+)?(m|Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E)?$")
BOOL_TRUE = {"true", "yes", "on", "1"}
BOOL_FALSE = {"false", "no", "off", "0"}
PORT_DISABLED = {"", "0", "none"}


def scalar(value):
    """YAML 的标量统一转成字符串：env 只能传字符串，顺手把 True → true 归一掉。

    [EN] Every scalar becomes a string because GITHUB_ENV only carries strings; this also
    normalizes YAML's True/False into the lowercase form the shell steps compare against.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def validate(spec, raw, where, errors):
    """按 type 校验并归一化一个值；不合法时追加错误并返回原值。

    [EN] Validate and normalize one value by its declared type.
    """
    kind = spec.get("type", "string")
    value = scalar(raw)

    if isinstance(raw, (dict, list)):
        allowed = "子对象" if isinstance(raw, dict) else "列表"
        errors.append("%s: 这里要的是单个值，给的是%s | expected a scalar" % (where, allowed))
        return value

    if kind == "secret":
        return value

    if kind == "bool":
        low = value.strip().lower()
        if low in BOOL_TRUE:
            return "true"
        if low in BOOL_FALSE:
            return "false"
        errors.append("%s: 期望 true/false（也接受 yes/no/on/off/1/0），实际 %r" % (where, value))
        return value

    if kind == "port":
        low = value.strip().lower()
        if low in PORT_DISABLED:
            return low if low != "none" else "none"
        if low.isdigit() and 1 <= int(low) <= 65535:
            return low
        errors.append("%s: 期望 1-65535 的端口，或留空 / 0 / none 表示不启用，实际 %r" % (where, value))
        return value

    if kind == "int":
        low = value.strip()
        if not low:
            errors.append("%s: 期望整数，实际是空值" % where)
            return value
        if not low.lstrip("-").isdigit():
            errors.append("%s: 期望整数，实际 %r" % (where, value))
            return value
        num = int(low)
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and num < lo or hi is not None and num > hi:
            errors.append("%s: 期望 %s-%s 之间的整数，实际 %s" % (where, lo, hi, num))
        return str(num)

    if kind == "percent":
        low = value.strip()
        if not low:
            return ""  # 百分比允许留空表示「不启用」| empty means disabled
        if not low.isdigit() or not 1 <= int(low) <= 1000:
            errors.append("%s: 期望 1-1000 的百分比整数（不带 %%），实际 %r" % (where, value))
            return value
        return str(int(low))

    if kind == "quantity":
        low = value.strip()
        if not QUANTITY_RE.match(low):
            errors.append("%s: 期望 K8s 资源量，如 100m / 1 / 256Mi / 1Gi，实际 %r" % (where, value))
        return low

    if kind == "enum":
        values = [str(v) for v in spec.get("values", [])]
        low = value.strip()
        if low not in values:
            hint = difflib.get_close_matches(low, values, n=1, cutoff=0.5)
            tail = "，是否想写 %s？" % hint[0] if hint else ""
            errors.append("%s: 只能是 %s 之一，实际 %r%s" % (where, " / ".join(values), value, tail))
        return low

    if kind == "csv":
        items = [part.strip() for part in value.split(",") if part.strip()]
        return ",".join(items)

    return value


# ==================== schema ====================

def load_schema(path, errors):
    with open(path, "r", encoding="utf-8") as handle:
        schema = parse_yaml(handle.read(), "schema.yaml", errors)
    if errors:
        fail(errors)
    return schema


def listify(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def check_tree(tree, schema_sections, origin, errors, single_section=None,
               secret_sections=None):
    """逐项对照 schema 校验键名：未知键报错并给「是否想写」提示。

    single_section 不为空时，tree 本身就是那一节的内容（分节变量的情形）。
    [EN] Check every leaf path against the schema and suggest the closest valid key for an
    unknown one. With single_section set, tree is the body of that one section.
    """
    if single_section:
        groups = [(single_section, tree)]
    else:
        groups = []
        for name, body in tree.items():
            name = str(name)
            if name not in schema_sections:
                hint = difflib.get_close_matches(name, list(schema_sections), n=1, cutoff=0.4)
                tail = "，是否想写 %s？" % hint[0] if hint else "，可用的节：%s" % " ".join(schema_sections)
                errors.append("%s: 未知配置节 %r%s | unknown section" % (origin, name, tail))
                continue
            # 一节写了但下面是空的（键全注释掉了，或只写了 env: {}）：当空表，不报错
            # [EN] A section present but empty, because every key under it is commented out or
            # it was written as env: {}, counts as an empty mapping rather than an error
            if body is None:
                body = {}
            if not isinstance(body, dict):
                errors.append("%s: %s 下面要写子对象，实际是 %s | section must be a mapping"
                              % (origin, name, type(body).__name__))
                continue
            groups.append((name, body))

    resolved = {}
    for name, body in groups:
        section = schema_sections[name]
        if section.get("free_form"):
            for key, value in leaves(body).items():
                if not re.match(r"^[A-Z][A-Z0-9_]*$", key):
                    errors.append("%s: %s.%s 的键名必须是大写环境变量名（A-Z0-9_）| invalid key"
                                  % (origin, name, key))
                else:
                    resolved[(name, key)] = scalar(value)
            continue

        keys = section.get("keys") or {}
        for path, value in leaves(body).items():
            where = "%s 的 %s.%s" % (origin, name, path)
            if path in keys:
                resolved[(name, path)] = validate(keys[path], value, where, errors)
                continue
            # 密钥写进了明文 vars：当场挡住，而不是让它被当成未知键含糊过去
            # [EN] A credential written into a plaintext variable is rejected outright
            if secret_sections:
                secret_keys = (secret_sections.get(name) or {}).get("keys") or {}
                if path in secret_keys or (secret_sections.get(name) or {}).get("free_form"):
                    errors.append("%s: %s.%s 是密钥，必须配在 secrets.PIPELINE_SECRETS 里，"
                                  "不能放进明文 vars | this key belongs in secrets"
                                  % (origin, name, path))
                    continue
            children = [k for k in keys if k.startswith(path + ".")]
            if children:
                errors.append("%s: %s.%s 下面还有子项，直接给值无效；可写的是 %s | needs sub-keys"
                              % (origin, name, path, " ".join(sorted(children)[:4])))
                continue
            hint = difflib.get_close_matches(path, list(keys), n=1, cutoff=0.4)
            tail = "，是否想写 %s？" % hint[0] if hint else ""
            errors.append("%s: %s 节里没有 %r 这一项%s | unknown key" % (origin, name, path, tail))
    return resolved


# ==================== 解析优先级 | Precedence ====================

def pick(section, path, spec, sources, repo_name):
    """按优先级取值：分节 YAML > 总表 YAML > input > 旧式 vars > 默认值。

    [EN] Highest to lowest: section YAML, aggregate YAML, workflow input, legacy variable,
    schema default.
    """
    key = (section, path)
    for label, table in sources["yaml"]:
        if key in table:
            return table[key], label

    for name in listify(spec.get("input")):
        if name in sources["inputs"]:
            value = sources["inputs"][name]
            # 沿用原先 ${{ inputs.x || vars.X || '默认' }} 的语义：空串与 false 都算「没传」
            # [EN] Mirrors the original expression chain, where an empty string and false both
            # mean "not provided"
            if value not in (None, "", False):
                return scalar(value), "input:%s" % name

    for name in listify(spec.get("var")):
        value = sources["vars"].get(name)
        if value not in (None, "", False):
            return scalar(value), "vars.%s" % name

    for name in [path] + listify(spec.get("env")):
        override = sources["defaults"].get("%s.%s" % (section, name) if name == path else name)
        if override is not None:
            return override.replace("{repo}", repo_name), "workflow 默认值"

    return scalar(spec.get("default", "")).replace("{repo}", repo_name), "schema 默认值"


# ==================== GITHUB_ENV / 打码 | Export and masking ====================

def write_env(pairs):
    target = os.environ.get("GITHUB_ENV")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for name, value in pairs:
            if "\n" in value:
                # 多行值必须走 heredoc，分隔符带随机串，避免正好撞上值里的某一行
                # [EN] A multiline value needs the heredoc form; the random delimiter cannot
                # collide with a line of the value itself
                delim = "PIPELINE_EOF_%s" % uuid.uuid4().hex
                handle.write("%s<<%s\n%s\n%s\n" % (name, delim, value, delim))
            else:
                handle.write("%s=%s\n" % (name, value))


def mask(value):
    """逐行打码：GitHub 按字符串匹配，多行值不拆开的话只有整体会被遮住。
    太短的值不打码，否则日志里到处都是 ***，反而看不清。

    [EN] Mask line by line, because the runner matches whole strings and would otherwise only
    hide the multiline value as a single blob. Very short values are left alone: masking them
    turns ordinary log lines into noise.
    """
    if not IS_ACTIONS:
        return
    for line in value.split("\n"):
        line = line.strip()
        if len(line) >= 4:
            print("::add-mask::%s" % line, flush=True)


# ==================== 主流程 | Main ====================

def env_text(name):
    return os.environ.get(name, "")


def load_json(name, errors):
    text = env_text(name).strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append("%s: 不是合法 JSON（%s）| invalid JSON" % (name, exc))
        return {}
    return data if isinstance(data, dict) else {}


def parse_defaults(text):
    """workflow 传进来的 per-workflow 默认值覆盖：每行一个 NAME=value。

    [EN] Per-workflow default overrides, one NAME=value per line.
    """
    out = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        out[name.strip()] = value.strip()
    return out


def yaml_scalar(value):
    """把一个值写成示例里那一格的字面量：能原样读回同一个值就不加引号，否则交给 PyYAML 引。

    以 { 开头的模板（{name}-config-{env}）不加引号会被解析成流式映射，整份示例就废了；
    而端口、true 这些加了引号反而难读，所以用「读回来是不是同一个值」来判断。
    [EN] Emit the literal for one example value: leave it bare when parsing it back yields the
    same value, and let PyYAML quote it otherwise. A template starting with { would be read as a
    flow mapping and break the whole document, while quoting ports and booleans only makes the
    example harder to read, hence the round-trip test.
    """
    if value == "":
        return "''"
    try:
        back = yaml.safe_load(value)
        if scalar(back) == value and not isinstance(back, (dict, list)):
            return value
    except yaml.YAMLError:
        pass
    dumped = yaml.safe_dump({"v": value}, default_flow_style=False,
                            allow_unicode=True, width=10 ** 6)
    return dumped.split("v:", 1)[1].strip()


def print_example(schema, wanted):
    """打印带注释的示例 YAML，直接贴进 GitHub 变量就能用。

    [EN] Print an annotated example that can be pasted into a GitHub variable as is.
    """
    sections = schema.get("sections") or {}
    names = wanted or list(sections)
    print("# vars.PIPELINE_CONFIG —— 一个变量管住所有明文配置")
    print("# [EN] One variable for every plaintext setting")

    def nest(keys):
        """点号路径还原成嵌套结构，好把 service: / resources: 这些父键也打出来。

        [EN] Rebuild the nesting from the dotted paths so parent keys are printed too.
        """
        tree = {}
        for path, spec in keys.items():
            node = tree
            parts = path.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            # 叶子裹一层元组：配置里本来就有名叫 type 的键（service.type），
            # 不裹的话会和 schema 自己的 type 字段撞名，被当成叶子打出来
            # [EN] Leaves are wrapped because the configuration itself has a key named type
            # (service.type), which would otherwise collide with the schema's own type field
            node[parts[-1]] = ("leaf", spec)
        return tree

    def emit(node, depth):
        pad = "  " * depth
        for key, value in node.items():
            if isinstance(value, dict):
                print("%s%s:" % (pad, key))
                emit(value, depth + 1)
                continue
            value = value[1]
            shown = yaml_scalar(scalar(value.get("default", "")))
            note = value.get("desc", "")
            if value.get("type") == "enum":
                note = (note + "；可选 " + " / ".join(str(v) for v in value["values"])).lstrip("；")
            print("%s%s: %s%s" % (pad, key, shown, "  # " + note if note else ""))

    for name in names:
        section = sections.get(name)
        if not section:
            continue
        # 自由键值节的键名由使用者定，schema 里列不出来：给个空表加示范注释，
        # 直接贴进变量也不会凭空多出环境变量
        # [EN] A free-form section's keys are the user's own and cannot be enumerated here, so it
        # gets an empty mapping plus a commented sample: pasting it verbatim adds nothing
        if section.get("free_form"):
            print("\n%s: {}  # %s" % (name, section.get("desc", "")))
            print("  # 键名就是变量名，按需要往下加，例如：| keys are the names, for example:")
            print("  #   LOG_LEVEL: info")
            print("  #   GOMAXPROCS: \"4\"")
            continue
        print("\n%s:  # %s" % (name, section.get("desc", "")))
        emit(nest(section.get("keys") or {}), 1)
    return 0


def print_secrets_example(schema, wanted):
    """打印 secrets.PIPELINE_SECRETS 的骨架：占位值，照着替换就能用。

    [EN] Print a skeleton for secrets.PIPELINE_SECRETS with placeholder values to replace.
    """
    sections = schema.get("secret_sections") or {}
    names = wanted or list(sections)
    print("# secrets.PIPELINE_SECRETS —— 一个 secret 装下所有敏感配置")
    print("# 占位值替换成真的；多行值（kubeconfig、私钥）用 | 块标量")
    print("# [EN] One secret for every sensitive setting. Replace the placeholders; multi-line")
    print("#      values such as a kubeconfig or a private key use the | block scalar form.")
    for name in names:
        section = sections.get(name)
        if not section:
            continue
        print("\n%s:  # %s" % (name, section.get("desc", "")))
        if section.get("free_form"):
            print("  # 键名就是容器里的环境变量名，部署时同步成集群里的 K8s Secret")
            print("  # [EN] Keys are the container's environment variable names")
            print("  MY_SERVICE_SECRET: <替换成真实值 | replace me>")
            continue
        for path, spec in (section.get("keys") or {}).items():
            parts = path.split(".")
            pad = "  " * len(parts)
            note = spec.get("desc", "")
            # 多行的那几项给块标量示例，省得使用者自己猜缩进
            # [EN] The multi-line ones get a block-scalar example so nobody has to guess
            if "多行" in note or "全文" in note:
                print("%s%s: |%s" % (pad, parts[-1], "  # " + note if note else ""))
                print("%s  <把整段内容贴在这里，保持这一层缩进 | paste the whole content here>" % pad)
            else:
                print("%s%s: <替换 | replace>%s" % (pad, parts[-1], "  # " + note if note else ""))
    return 0


def main(argv):
    errors = []
    here = os.path.dirname(os.path.abspath(__file__))
    schema_path = env_text("CONFIG_SCHEMA") or os.path.join(here, "config", "schema.yaml")
    schema = load_schema(schema_path, errors)
    sections = schema.get("sections") or {}
    secret_sections = schema.get("secret_sections") or {}

    if "--print-example" in argv:
        rest = argv[argv.index("--print-example") + 1:]
        wanted = [a for a in rest if not a.startswith("-")]
        if "--secrets" in argv:
            return print_secrets_example(schema, wanted)
        return print_example(schema, wanted)

    # ---------- 本地校验模式 | Local check mode ----------
    if "--check" in argv:
        idx = argv.index("--check")
        path = argv[idx + 1] if len(argv) > idx + 1 else "-"
        single = None
        if "--section" in argv:
            single = argv[argv.index("--section") + 1]
            if single not in sections:
                fail(["--section %s 不是已知配置节，可用：%s" % (single, " ".join(sections))])
        text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
        origin = "secrets.PIPELINE_SECRETS" if "--secrets" in argv else os.path.basename(path)
        tree = parse_yaml(text, origin, errors)
        if errors:
            fail(errors)
        table = schema_sections_for(argv, sections, secret_sections)
        found = check_tree(tree, table, origin, errors, single,
                           None if "--secrets" in argv else secret_sections)
        if errors:
            fail(errors)
        log("✅ %s 校验通过，共 %d 项 | valid, %d setting(s)" % (origin, len(found), len(found)))
        for (section, key), value in sorted(found.items()):
            shown = "<已设置 len=%d sha256=%s>" % (len(value), fingerprint(value)) \
                if "--secrets" in argv else value
            log("   %-52s %s" % ("%s.%s" % (section, key), shown))
        return 0

    # ---------- Actions 解析模式 | Resolve mode ----------
    repo_name = env_text("GITHUB_REPOSITORY").split("/")[-1] or "app"
    wanted = env_text("CONFIG_SECTIONS").replace(",", " ").split()
    wanted_secrets = env_text("CONFIG_SECRET_SECTIONS").replace(",", " ").split()
    for name in wanted:
        if name not in sections:
            errors.append("sections 入参里的 %r 不是已知配置节，可用：%s" % (name, " ".join(sections)))
    for name in wanted_secrets:
        if name not in secret_sections:
            errors.append("secret-sections 入参里的 %r 不是已知密钥节，可用：%s"
                          % (name, " ".join(secret_sections)))
    if errors:
        fail(errors)

    # vars / secrets / inputs 三份上下文整体传进来，聚合配置就在 vars 里按约定命名取，
    # 不用给每个节各开一个 action 入参
    # [EN] The three contexts arrive whole and the aggregate configuration is looked up inside
    # vars by convention, so the action needs no input per section
    github_vars = load_json("CONFIG_VARS_JSON", errors)
    github_secrets = load_json("CONFIG_SECRETS_JSON", errors)
    if errors:
        fail(errors)

    # 明文 YAML 的分层，高优先级在前 | Plaintext YAML layers, highest first
    #   1. action 的 config 入参（调用方直接贴 YAML，或从别处读出来再传）
    #   2. vars.<SECTION>_CONFIG 分节变量（环境级覆盖仓库级的常用姿势）
    #   3. vars.PIPELINE_CONFIG 总表
    #   4. config-file 指向的仓库内文件（签入版本库的基线）
    yaml_layers = []

    inline = env_text("CONFIG_INLINE")
    if inline.strip():
        tree = parse_yaml(inline, "config 入参", errors)
        yaml_layers.append(("config 入参", check_tree(tree, sections, "config 入参", errors,
                                                      None, secret_sections)))

    for name in sections:
        raw = github_vars.get("%s_CONFIG" % name.upper())
        if not isinstance(raw, str) or not raw.strip():
            continue
        origin = "vars.%s_CONFIG" % name.upper()
        tree = parse_yaml(raw, origin, errors)
        yaml_layers.append((origin, check_tree(tree, sections, origin, errors, name,
                                               secret_sections)))

    aggregate = github_vars.get("PIPELINE_CONFIG")
    if isinstance(aggregate, str) and aggregate.strip():
        tree = parse_yaml(aggregate, "vars.PIPELINE_CONFIG", errors)
        yaml_layers.append(("vars.PIPELINE_CONFIG",
                            check_tree(tree, sections, "vars.PIPELINE_CONFIG", errors,
                                       None, secret_sections)))

    config_file = env_text("CONFIG_FILE").strip()
    if config_file:
        if os.path.isfile(config_file):
            with open(config_file, encoding="utf-8") as handle:
                tree = parse_yaml(handle.read(), config_file, errors)
            yaml_layers.append((config_file, check_tree(tree, sections, config_file, errors,
                                                        None, secret_sections)))
        else:
            log("::warning::config-file 指向的 %s 不存在，跳过 | config-file not found" % config_file)

    # 密钥：secrets.PIPELINE_SECRETS 走 secrets 侧的 schema
    # [EN] Secrets come from secrets.PIPELINE_SECRETS, validated against the secrets schema
    secrets_blob = github_secrets.get("PIPELINE_SECRETS")
    secrets_tree = parse_yaml(secrets_blob if isinstance(secrets_blob, str) else "",
                              "secrets.PIPELINE_SECRETS", errors)
    from_secrets = check_tree(secrets_tree, secret_sections, "secrets.PIPELINE_SECRETS", errors)
    if errors:
        fail(errors)

    sources = {
        "yaml": yaml_layers,
        "inputs": load_json("CONFIG_INPUTS_JSON", errors),
        "vars": github_vars,
        "defaults": parse_defaults(env_text("CONFIG_DEFAULTS")),
    }
    if errors:
        fail(errors)

    exported = []       # [(env_name, value)]
    rows = []           # [(label, shown_value, source)]
    seen = {}           # env_name -> (value, label)

    def export(env_names, value, label, source, secret=False):
        for name in env_names:
            if name in seen and seen[name][0] != value:
                log("::warning::%s 被 %s 和 %s 解析出不同的值，采用先出现的 %s"
                    % (name, seen[name][1], label, seen[name][1]))
                continue
            if name in seen:
                continue
            seen[name] = (value, label)
            exported.append((name, value))
        if secret:
            mask(value)
            shown = "<已设置 len=%d sha256=%s>" % (len(value), fingerprint(value)) if value else "<未设置>"
        else:
            shown = value if value != "" else "''"
        rows.append((label, shown, source))

    for name in wanted:
        # 自由键值节（如 env）：键名由使用者定，schema 里列不出来，所以整节按层合并后
        # 导出成一个 JSON，交给下游脚本渲染，而不是逐项写成环境变量
        # [EN] A free-form section such as env has user-defined keys that the schema cannot
        # enumerate, so the whole section is merged layer by layer and exported as one JSON blob
        # for a downstream script to render, rather than as one variable per key
        if sections[name].get("free_form"):
            merged = {}
            for label, table in reversed(sources["yaml"]):
                for (sec, key), value in table.items():
                    if sec == name:
                        merged[key] = value
            if merged:
                export(["PIPELINE_%s_JSON" % name.upper()],
                       json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
                       "%s.* (%d 项)" % (name, len(merged)), "聚合 YAML")
            continue

        for path, spec in (sections[name].get("keys") or {}).items():
            value, source = pick(name, path, spec, sources, repo_name)
            # 旧式 vars 和 input 的值也要过一遍校验：错配置不该等到 kubectl 才炸
            # [EN] Values coming from inputs and legacy variables are validated too, so a bad
            # setting fails here rather than inside kubectl
            value = validate(spec, value, "%s.%s（来自 %s）" % (name, path, source), errors)
            export(listify(spec.get("env")), value, "%s.%s" % (name, path), source)

    for name in wanted_secrets:
        section = secret_sections[name]
        if section.get("free_form"):
            continue
        for path, spec in (section.get("keys") or {}).items():
            key = (name, path)
            if key in from_secrets:
                value, source = from_secrets[key], "secrets.PIPELINE_SECRETS"
            else:
                value, source = "", "未配置"
                for sec_name in listify(spec.get("secret")):
                    fallback = github_secrets.get(sec_name)
                    if isinstance(fallback, str) and fallback:
                        value, source = fallback, "secrets.%s" % sec_name
                        break
                if not value:
                    override = sources["defaults"].get(listify(spec.get("env"))[0])
                    if override:
                        value, source = override, "workflow 默认值"
            export(listify(spec.get("env")), value, "%s.%s" % (name, path), source, secret=True)

    if errors:
        fail(errors)

    # 业务密钥不进 GITHUB_ENV：写成 0600 的文件，由部署那步读去同步成集群 Secret
    # [EN] Service secrets never enter GITHUB_ENV; they go to a 0600 file that the deploy step
    # reads when syncing the in-cluster Secret
    app_secrets = {key: value for (section, key), value in from_secrets.items()
                   if section == "app" and "app" in wanted_secrets}
    if app_secrets:
        temp = os.environ.get("RUNNER_TEMP") or "/tmp"
        path = os.path.join(temp, "pipeline-app-secrets.json")
        old = os.umask(0o077)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(app_secrets, handle)
        finally:
            os.umask(old)
        os.chmod(path, 0o600)
        for value in app_secrets.values():
            mask(value)
        exported.append(("PIPELINE_APP_SECRETS_FILE", path))
        rows.append(("app.* (%d 个业务密钥)" % len(app_secrets), "→ %s" % path,
                     "secrets.PIPELINE_SECRETS"))

    write_env(exported)

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        public = {name: value for name, value in exported
                  if name not in {n for s in wanted_secrets
                                  for k in ((secret_sections[s].get("keys") or {}).values())
                                  for n in listify(k.get("env"))}
                  and name != "PIPELINE_APP_SECRETS_FILE"}
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("json=%s\n" % json.dumps(public, separators=(",", ":")))

    # 解析结果表：每一项的最终值和它是从哪一层来的，排查「为什么是这个值」只看这一张表
    # [EN] One table showing every final value and the layer it came from, which is all that is
    # needed to answer "why is it this value"
    title = "⚙️ 配置解析结果 | Resolved configuration (%d 项 | settings)" % len(rows)
    log("::group::%s" % title if IS_ACTIONS else title)
    log("%-52s %-34s %s" % ("配置项 | key", "值 | value", "来源 | source"))
    log("%s" % ("-" * 116))
    for label, shown, source in rows:
        log("%-52s %-34s %s" % (label, shown[:34], source))
    if IS_ACTIONS:
        log("::endgroup::")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and env_text("CONFIG_SUMMARY").lower() == "true":
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n### %s\n\n" % title)
            handle.write("| 配置项 | 值 | 来源 |\n|---|---|---|\n")
            for label, shown, source in rows:
                handle.write("| `%s` | `%s` | %s |\n" % (label, shown, source))
    return 0


def schema_sections_for(argv, sections, secret_sections):
    return secret_sections if "--secrets" in argv else sections


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
