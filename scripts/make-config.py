#!/usr/bin/env python3
# ============================================================================
# 把一份 KEY=VALUE 清单转成 PIPELINE_CONFIG / PIPELINE_SECRETS | Turn a KEY=VALUE dump into
# the two aggregate documents
#
# 接一个服务时要配的东西几十项，一个个在 Settings 里点既慢又容易漏 这个脚本收一份
# 环境变量清单（就是平时 .env 的样子），按 scripts/config/schema.yaml 自动分类：
#
#   命中密钥节的 env 名   → PIPELINE_SECRETS 的对应路径（kube.kubeconfig 这类）
#   命中明文节的 env 名   → PIPELINE_CONFIG 的对应路径，并提示它不该当 secret 配
#   都没命中              → PIPELINE_SECRETS 的 app 节，即业务密钥，键名就是容器里的变量名
#
# 值不经过终端：默认写成两个 0600 的文件，标准输出只打名字与去向
# 这不是洁癖 —— 终端滚屏、CI 日志、shell 历史都会留痕，而密钥一旦出现在那里就等于泄露，
# 唯一的补救是轮换 要看内容就自己打开那两个文件
#
# 用法 | Usage
#   python3 scripts/make-config.py dump.env -o out/
#   python3 scripts/make-config.py dump.env -o out/ --map OPS_CLI_APP_ID=APP_ID
#   python3 scripts/make-config.py dump.env -o out/ --apply your-org/your-service:dev
#
# 输入格式 | Input
#   KEY=value              普通一行
#   export KEY=value       前缀 export 会被忽略，方便直接从 shell 脚本里抄
#   KEY="value"            首尾成对的引号会被去掉
#   KEY@=path/to/file      值从文件读，给 kubeconfig、PEM 私钥这种多行内容用
#   # 注释 / 空行           跳过
#
#   值里带换行的会自动写成 YAML 块标量，不用自己数缩进
#
# [EN] Onboarding a service means dozens of settings, and clicking each one into the Settings
# page is slow and easy to get wrong. This script takes one KEY=VALUE dump, the shape of an
# ordinary .env file, and classifies every entry against scripts/config/schema.yaml: names that
# match a secret section go to their path inside PIPELINE_SECRETS, names that match a plaintext
# section go to PIPELINE_CONFIG with a note that they do not belong in secrets, and anything
# unknown becomes a business secret under the free-form app section, keyed by the container's
# own variable name. Values never reach the terminal: they go to two 0600 files while stdout
# carries only names and destinations, because terminal scrollback, CI logs and shell history
# all keep a copy and a leaked secret can only be rotated, not unleaked.
# ============================================================================
import argparse
import os
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.exit("需要 PyYAML：pip install pyyaml | PyYAML required")

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "config", "schema.yaml")

# 这套生态里调用方常用的别名：仓库里的 secret 名与 schema 的 env 名不同
# 不写死在匹配逻辑里而是放这张表，是为了让 --map 能覆盖它：别的组织换了前缀照样能用
# [EN] Aliases this ecosystem uses, kept as a table that --map can extend rather than being
# hardcoded, so another organization with a different prefix can still use the script
DEFAULT_ALIASES = {
    "OPS_CLI_APP_ID": "APP_ID",
    "OPS_CLI_APP_PRIVATE_KEY": "APP_PRIVATE_KEY",
}


def load_schema():
    with open(SCHEMA, encoding="utf-8") as fh:
        schema = yaml.safe_load(fh)
    secret_by_env, plain_by_env = {}, {}
    for group, table in (("secret_sections", secret_by_env), ("sections", plain_by_env)):
        for section, body in (schema.get(group) or {}).items():
            for key, spec in (body.get("keys") or {}).items():
                env = spec.get("env")
                for name in ([env] if isinstance(env, str) else env or []):
                    table[str(name)] = (section, str(key), spec)
    free_form = {s for s, b in (schema.get("secret_sections") or {}).items() if b.get("free_form")}
    return secret_by_env, plain_by_env, free_form


def parse_dump(path):
    """读 KEY=VALUE 清单，返回 [(name, value)]，保留出现顺序便于人工核对。

    [EN] Reads the dump and keeps the original order so the summary is easy to check by eye.
    """
    out, bad = [], []
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            from_file = False
            if "@=" in line and line.index("@=") < line.index("=") + 1:
                name, _, value = line.partition("@=")
                from_file = True
            elif "=" in line:
                name, _, value = line.partition("=")
            else:
                bad.append((lineno, line))
                continue
            name = name.strip()
            if not name:
                bad.append((lineno, line))
                continue
            value = value.strip()
            # 成对的引号去掉；单边的留着，那更可能是值本身的一部分
            # [EN] Matched quotes are stripped; a lone one is more likely part of the value
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if from_file:
                # 原样读，不去掉结尾换行：PEM 私钥与 kubeconfig 的最后一行换行是格式的一部分
                # 去掉之后 YAML 会写成 |- （strip），还原出来的私钥没有结尾换行，
                # ssh 与 openssl 都会判成格式无效，而现象只是部署时「认证失败」
                # [EN] Read verbatim without stripping the trailing newline, which is part of the
                # format for a PEM key or a kubeconfig: dropping it makes YAML emit |- and the
                # restored key lacks its final newline, which ssh and openssl reject as invalid
                # while the only visible symptom is an authentication failure at deploy time
                with open(os.path.expanduser(value), encoding="utf-8") as vf:
                    value = vf.read()
            out.append((name, value))
    return out, bad


def put(tree, section, dotted, value):
    node = tree.setdefault(section, {})
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


class _Dumper(yaml.SafeDumper):
    pass


def _block_str(dumper, value):
    """带换行的值用 | 块标量写出来。

    不加这个 representer 的话 PyYAML 会选折叠的单引号形式：PEM 私钥会变成一堆
    被空行分隔的片段，语义上能往回解析，但人看不出哪儿是真换行，手工改一下就坏
    [EN] Without this representer PyYAML picks the folded single-quoted form, which turns a PEM
    key into fragments separated by blank lines: it parses back correctly but a human cannot see
    where the real newlines are, and one hand edit breaks it
    """
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _block_str)


def dump_yaml(tree):
    return yaml.dump(tree, Dumper=_Dumper, allow_unicode=True, sort_keys=True,
                     default_flow_style=False)


def write_private(path, text):
    """0600 写文件：建的时候就带权限，不是建完再 chmod。

    中间那一瞬间别人可读，而密钥文件只需要被读一次就够了。
    [EN] The mode is set at creation rather than with a later chmod, because the window in
    between is readable by others and a secret only needs to be read once.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("dump", help="KEY=VALUE 清单文件")
    ap.add_argument("-o", "--outdir", default="pipeline-config", help="输出目录")
    ap.add_argument("--map", action="append", default=[], metavar="OLD=NEW",
                    help="把输入里的名字映射成 schema 的 env 名，可多次指定")
    ap.add_argument("--apply", metavar="OWNER/REPO[:ENV]",
                    help="直接用 gh 写进仓库或环境；不给这个参数只落盘")
    args = ap.parse_args(argv)

    aliases = dict(DEFAULT_ALIASES)
    for item in args.map:
        old, _, new = item.partition("=")
        if not old or not new:
            sys.exit("--map 要写成 OLD=NEW 的形式：%r" % item)
        aliases[old.strip()] = new.strip()

    secret_by_env, plain_by_env, free_form = load_schema()
    entries, bad = parse_dump(args.dump)
    if bad:
        for lineno, line in bad:
            print("⚠️  第 %d 行不是 KEY=VALUE，已跳过：%s" % (lineno, line[:60]))
    if not entries:
        sys.exit("清单里一条都没解析出来")

    secrets, config, rows, dupes, suspicious = {}, {}, [], [], []
    seen = set()
    for name, value in entries:
        if name in seen:
            dupes.append(name)
        seen.add(name)
        lookup = aliases.get(name, name)
        if lookup in secret_by_env:
            section, key, _ = secret_by_env[lookup]
            put(secrets, section, key, value)
            rows.append((name, "PIPELINE_SECRETS", "%s.%s" % (section, key), len(value)))
        elif lookup in plain_by_env:
            section, key, _ = plain_by_env[lookup]
            put(config, section, key, value)
            rows.append((name, "PIPELINE_CONFIG", "%s.%s" % (section, key), len(value)))
        elif free_form:
            section = sorted(free_form)[0]
            put(secrets, section, name, value)
            rows.append((name, "PIPELINE_SECRETS", "%s.%s" % (section, name), len(value)))
            # 去掉常见前缀后能命中明文配置项的，极可能是旧变量名而不是业务密钥
            #
            # 这个提示有必要：业务密钥会被同步成集群 Secret，一个配置项误落进去，
            # 结果是它既没有以配置的身份生效（名字对不上 schema），又以密钥的身份
            # 进了集群 两头都不报错，排查时两头都看不出来
            #
            # [EN] A name that matches a plaintext key once a common prefix is dropped is far
            # more likely an old variable name than a business secret. The hint matters because
            # business secrets are synced into the in-cluster Secret: a setting that lands there
            # neither takes effect as configuration, since the name does not match the schema,
            # nor shows up as a problem anywhere
            for prefix in ("K8S_", "BUILD_DOCKER_", "BUILD_BINARY_", "CLEANUP_", "GOLIB_",
                           "DOCKER_", "SERVICE_", "BRANCH_OPS_", "NOTIFY_"):
                if name.startswith(prefix) and name[len(prefix):] in plain_by_env:
                    suspicious.append((name, name[len(prefix):]))
                    break
        else:
            rows.append((name, "未分类", "-", len(value)))

    os.makedirs(args.outdir, exist_ok=True)
    sec_path = os.path.join(args.outdir, "PIPELINE_SECRETS.yaml")
    cfg_path = os.path.join(args.outdir, "PIPELINE_CONFIG.yaml")
    if secrets:
        write_private(sec_path, dump_yaml(secrets))
    if config:
        write_private(cfg_path, dump_yaml(config))

    # 往返校验：写出去的 YAML 读回来，值必须和输入逐字节相同
    #
    # 密钥最怕的不是报错而是悄悄变形 —— 少一个结尾换行、块标量缩进算错一格，
    # PEM 私钥就不可用了，而现象是部署时「认证失败」，没人会想到是配置生成那一步
    #
    # [EN] A secret's worst failure is silent mangling rather than an error: one missing trailing
    # newline or one wrong block-scalar indent makes a PEM key unusable, and it surfaces as an
    # authentication failure at deploy time that nobody traces back to generation
    for tree in (secrets, config):
        if not tree:
            continue
        back = yaml.safe_load(dump_yaml(tree))
        if back != tree:
            sys.exit("内部错误：生成的 YAML 读回来和输入不一致，拒绝落盘")

    width = max(len(r[0]) for r in rows)
    print("\n%-*s  %-18s %-34s %s" % (width, "名字 | name", "去向 | goes to", "路径 | path", "长度"))
    print("-" * (width + 62))
    for name, dest, path, size in rows:
        print("%-*s  %-18s %-34s %d" % (width, name, dest, path, size))

    plain_in_dump = [r[0] for r in rows if r[1] == "PIPELINE_CONFIG"]
    if plain_in_dump:
        print("\n⚠️  这 %d 项在 schema 里是明文配置，不该当 secret 配：" % len(plain_in_dump))
        print("    %s" % " ".join(plain_in_dump))
        print("    它们已经分到 PIPELINE_CONFIG.yaml，而那个变量是公开可读的 —— 确认一下")
    if suspicious:
        print("\n⚠️  这 %d 项被当成业务密钥，但去掉前缀后命中了明文配置项 —— 多半是旧变量名：" % len(suspicious))
        for name, env in suspicious:
            section, key, _ = plain_by_env[env]
            print("    %-34s 看起来是 %s.%s，想当配置用就加 --map %s=%s" % (name, section, key, name, env))
        print("    业务密钥会被同步成集群 Secret，配置项误落进去两头都不报错")
    if dupes:
        print("\n⚠️  重复出现、后一条生效：%s" % " ".join(sorted(set(dupes))))

    print("\n产物（0600，值只在文件里，上面没有打印）")
    if secrets:
        print("  %s" % sec_path)
    if config:
        print("  %s" % cfg_path)

    # 自己先过一遍 schema 校验，别等推上去才发现键名拼错
    # [EN] Validated here rather than after it is pushed
    resolve = os.path.join(HERE, "resolve-config.py")
    for path, extra in ((sec_path, ["--secrets"]), (cfg_path, [])):
        if not os.path.exists(path):
            continue
        done = subprocess.run([sys.executable, resolve, "--check", path] + extra,
                              capture_output=True, text=True)
        print("  校验 %s：%s" % (os.path.basename(path), "通过" if done.returncode == 0 else "不通过"))
        if done.returncode != 0:
            sys.stdout.write(done.stdout[-2000:] + done.stderr[-2000:])
            return 1

    if args.apply:
        target, _, environment = args.apply.partition(":")
        for path, secret_name in ((sec_path, "PIPELINE_SECRETS"),):
            if not os.path.exists(path):
                continue
            cmd = ["gh", "secret", "set", secret_name, "--repo", target]
            if environment:
                cmd += ["--env", environment]
            with open(path, encoding="utf-8") as fh:
                done = subprocess.run(cmd, stdin=fh)
            if done.returncode != 0:
                return done.returncode
            print("  已写入 %s 的 %s%s" % (target, secret_name,
                                           "（环境 %s）" % environment if environment else ""))
        if os.path.exists(cfg_path):
            cmd = ["gh", "variable", "set", "PIPELINE_CONFIG", "--repo", target]
            if environment:
                cmd += ["--env", environment]
            with open(cfg_path, encoding="utf-8") as fh:
                done = subprocess.run(cmd, stdin=fh)
            if done.returncode != 0:
                return done.returncode
            print("  已写入 %s 的 PIPELINE_CONFIG" % target)
    else:
        print("\n下一步：确认内容后写进仓库")
        print("  gh secret set PIPELINE_SECRETS --repo <owner/repo> [--env <环境>] < %s" % sec_path)
        if config:
            print("  gh variable set PIPELINE_CONFIG --repo <owner/repo> < %s" % cfg_path)
        print("  或者加 --apply <owner/repo>[:<环境>] 让本脚本直接写")
    return 0


if __name__ == "__main__":
    sys.exit(main())
