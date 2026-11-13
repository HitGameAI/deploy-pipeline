#!/usr/bin/env python3
# 邮件通知发送器 | Email notification sender
#
# 为什么是 Python 而不是 curl：curl 能发 SMTP，但认证、STARTTLS 和
# multipart/alternative 拼起来既脆又难读，而 smtplib + email.message 是标准库，
# GitHub runner 自带，仓库里 resolve-config.py 等脚本也已经走 python3
# [EN] curl can speak SMTP, but auth, STARTTLS and multipart/alternative are fragile and
# unreadable that way, while smtplib and email.message ship with the Python the runners
# already have and that this repository's other scripts already use
#
# 模板只管排版，条件判断全在这里：哪些明细行有值、要不要错误块、要不要按钮，
# 都由调用方给了什么决定，模板里不塞 if/each 那套
# [EN] Templates carry layout only; every conditional lives here, because which detail rows,
# error block and button appear depends on what the caller supplied
#
# 退出码 | Exit codes
#   0  发送成功
#   2  配置不全（缺 SMTP 地址 / 收件人 / 凭据），调用方据此判断「未配置」而非失败
#   1  其它错误（认证失败、连接失败、模板缺失）

import html
import os
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "email"

# 状态 → 配色，与 notify.sh 的 get_status_info 一一对应
# [EN] Status to palette, matching notify.sh's get_status_info
STATUS_PALETTE = {
    "success": ("#2f6b4f", "✅"),
    "failure": ("#b3261e", "❌"),
    "warning": ("#8a5a1b", "⚠️"),
    "info": ("#1f5f8b", "ℹ️"),
}

# 环境 → 标签与配色的内置值，语义与 notify.sh 的 get_env_label 一致
# 这里只是默认值，NOTIFY_ENV_STYLES 可以逐项覆盖，也可以加内置表里没有的环境名
# [EN] Built-in environment labels and palettes, matching notify.sh's get_env_label. These are
# defaults only: NOTIFY_ENV_STYLES overrides any entry and can add environments not listed here
ENV_STYLE_DEFAULT = {
    "prod": ("生产环境", "#b3261e", "#fdeceb"),
    "staging": ("预发环境", "#7a4bb5", "#f2ecfa"),
    "test": ("测试环境", "#8a5a1b", "#fbf1e2"),
    "uat": ("UAT 环境", "#1f5f8b", "#e8f0f6"),
    "dev": ("开发环境", "#2f6b4f", "#e9f4ee"),
}
ENV_STYLE_FALLBACK = ("#6b7684", "#eef1f4")


def parse_env_styles(raw):
    """解析 NOTIFY_ENV_STYLES，覆盖到内置表上

    格式：name=标签|主色|底色，多项用逗号分隔，例如
        staging=预发|#7a4bb5|#f2ecfa,sea-01=新加坡|#0d7d74|#e4f4f2

    色值可以带或不带 #：聚合配置是 YAML，不带引号的 # 会被当成注释起点，
    所以允许写裸十六进制，省掉一处踩坑
    [EN] Hex values may omit the leading #: the aggregate config is YAML, where an unquoted #
    starts a comment, so bare hex removes one way to get this wrong

    标签之后的色值可省略，省略就用兜底灰；整条解析不了就跳过并提示，不让一条
    手误配置把整封邮件拦下来
    [EN] The colours are optional and fall back to grey. An unparsable entry is skipped with a
    warning rather than failing the whole message
    """
    styles = dict(ENV_STYLE_DEFAULT)
    if not raw:
        return styles

    def norm(color, default):
        color = (color or "").strip()
        if not color:
            return default
        if not color.startswith("#"):
            color = "#" + color
        return color if re.fullmatch(r"#[0-9a-fA-F]{3,8}", color) else default

    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            print(
                "::warning::NOTIFY_ENV_STYLES 忽略无法解析的项（缺 =）：{}".format(item),
                file=sys.stderr,
            )
            continue
        name, _, spec = item.partition("=")
        name = name.strip()
        parts = [p.strip() for p in spec.split("|")]
        label = parts[0] or name
        fg = norm(parts[1] if len(parts) > 1 else "", ENV_STYLE_FALLBACK[0])
        bg = norm(parts[2] if len(parts) > 2 else "", ENV_STYLE_FALLBACK[1])
        if not name:
            print(
                "::warning::NOTIFY_ENV_STYLES 忽略无法解析的项（缺环境名）：{}".format(item),
                file=sys.stderr,
            )
            continue
        styles[name] = (label, fg, bg)
    return styles


# 取值来源做成可替换的：预览模式下换成场景字典，就不必为了看模板去造一遍真环境
# [EN] The lookup source is swappable so preview mode can substitute a scenario dict instead of
# requiring a real environment just to look at a template
_ENV = os.environ


def env(name, default=""):
    return (_ENV.get(name) or default).strip()


def md_subset_to_html(text):
    """把飞书 / Telegram 那边已在用的 Markdown 子集转成邮件 HTML

    只支持 **粗体**、`代码`、[文字](链接)、- 列表 和换行，刻意不引入完整
    Markdown 解析：调用方的正文本来就是按这几样写的，支持更多只会让三个渠道
    渲染结果不一致
    [EN] Converts the Markdown subset the Lark and Telegram paths already accept. Supporting
    more would make the same body render differently per channel
    """
    out, bullets = [], []

    def flush_bullets():
        if not bullets:
            return
        items = "".join(
            '<li style="margin:0 0 4px;">{}</li>'.format(b) for b in bullets
        )
        out.append(
            '<ul style="margin:6px 0 10px;padding-left:20px;">{}</ul>'.format(items)
        )
        bullets.clear()

    def inline(raw):
        # 先转义，再放行我们自己生成的标签，顺序反了会把用户正文里的 < 当标签
        # [EN] Escape first, then emit our own tags: the reverse order would treat a < in the
        # caller's body as markup
        s = html.escape(raw, quote=False)
        s = re.sub(
            r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
            lambda m: '<a href="{}" style="color:#1f5f8b;text-decoration:underline;">{}</a>'.format(
                m.group(2), m.group(1)
            ),
            s,
        )
        s = re.sub(r"\*\*([^*]+)\*\*", r'<strong style="font-weight:600;">\1</strong>', s)
        s = re.sub(
            r"`([^`]+)`",
            r'<code class="code" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
            r'font-size:13px;background:#f1f4f7;border:1px solid #e3e8ed;border-radius:4px;'
            r'padding:1px 5px;">\1</code>',
            s,
        )
        # 裸 URL 也转成链接，但跳过已经在 href="..." 里的
        s = re.sub(
            r'(?<!")(?<!>)(https?://[^\s<]+)',
            r'<a href="\1" style="color:#1f5f8b;text-decoration:underline;">\1</a>',
            s,
        )
        return s

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            bullets.append(inline(stripped[2:]))
            continue
        flush_bullets()
        if not stripped:
            continue
        out.append('<div style="margin:0 0 6px;">{}</div>'.format(inline(stripped)))
    flush_bullets()
    return "".join(out) or '<div style="color:#8a95a3;">（无正文）</div>'


def kv_rows(pairs, last_border=False):
    """明细表的行；pairs 里值为空的条目直接不渲染"""
    rows, visible = [], [(k, v) for k, v in pairs if v]
    for i, (key, val) in enumerate(visible):
        border = (
            ""
            if (i == len(visible) - 1 and not last_border)
            else "border-bottom:1px solid #e9edf1;"
        )
        rows.append(
            '<tr>'
            '<td class="kv-key" style="width:34%;padding:11px 8px 11px 20px;font-size:13px;'
            'color:#6b7684;vertical-align:top;{border}">{key}</td>'
            '<td class="kv-val ink" style="padding:11px 20px 11px 8px;font-size:13px;'
            'color:#2b323b;vertical-align:top;word-break:break-all;{border}">{val}</td>'
            "</tr>".format(border=border, key=html.escape(key), val=val)
        )
    return "".join(rows)


def mono(value):
    return (
        '<span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
        'font-size:12.5px;">{}</span>'.format(html.escape(value))
    )


def button(url, label):
    if not url:
        return ""
    # 邮件按钮用表格包一层：Outlook 不认 <a> 上的 padding
    # [EN] Wrapped in a table because Outlook ignores padding on an <a>
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:22px 0 4px;"><tr><td align="center" '
        'style="background:#1b2027;border-radius:8px;">'
        '<a href="{url}" style="display:inline-block;padding:11px 26px;font-size:14px;'
        'font-weight:500;color:#ffffff;text-decoration:none;">{label}</a>'
        "</td></tr></table>".format(url=html.escape(url, quote=True), label=html.escape(label))
    )


def error_block(message):
    if not message:
        return ""
    body = html.escape(message)
    if len(body) > 1500:
        body = body[:1500] + "\n…（已截断，完整日志见下方链接）"
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin-bottom:18px;"><tr>'
        '<td style="padding:14px 18px;background:#fdeceb;border-left:3px solid #b3261e;'
        'border-radius:0 8px 8px 0;">'
        '<div style="font-size:12px;font-weight:600;color:#b3261e;padding-bottom:6px;">失败原因</div>'
        '<pre class="code" style="margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
        'font-size:12px;line-height:1.6;color:#4a2420;white-space:pre-wrap;word-break:break-all;">'
        "{}</pre></td></tr></table>".format(body)
    )


def render(name, mapping):
    path = TEMPLATE_DIR / name
    if not path.is_file():
        print("::error::缺少邮件模板 {}".format(path), file=sys.stderr)
        sys.exit(1)
    text = path.read_text(encoding="utf-8")

    # 替换前先剥掉 HTML 注释，两个原因：
    #   1. 模板注释里会把 {{占位符}} 当文档写，不剥的话那里也会被替换 ——
    #      明细行曾因此被注入三次（注释两处 + 正文一处），实测踩过
    #   2. 开发文档没必要随邮件发出去；Gmail 对超过 102KB 的正文会截断并折叠，
    #      注释是纯浪费的体积
    # 保留 Outlook 条件注释（<!--[if mso]>…<![endif]-->），那是渲染指令不是文档
    # [EN] Comments are stripped before substitution for two reasons. First, template comments
    # document the {{placeholders}} by name, and without stripping those mentions are substituted
    # too, which injected the detail rows three times. Second, developer documentation has no
    # business in the delivered mail, and Gmail clips a body over 102KB. Outlook conditional
    # comments are kept, since those are rendering instructions rather than documentation
    text = re.sub(r"<!--(?!\[if\b)(?:(?!-->).)*?-->", "", text, flags=re.S)

    for key, val in mapping.items():
        text = text.replace("{{%s}}" % key, val)
    # 未被替换的占位符留在正文里会很难看，清掉并记一条
    leftover = set(re.findall(r"\{\{([A-Z_]+)\}\}", text))
    if leftover:
        print(
            "::warning::模板 {} 有未填充的占位符：{}".format(name, " ".join(sorted(leftover))),
            file=sys.stderr,
        )
        text = re.sub(r"\{\{[A-Z_]+\}\}", "", text)
    return text


# ==================== 预览 ====================
# 看模板效果不该需要一台 SMTP：--preview 把各场景渲染成本地 HTML，浏览器直接打开
# 走的是 build_message() 同一条路径，所以看到的就是实际会发出去的正文
# [EN] Looking at a template should not require an SMTP server. --preview renders each scenario
# to a local HTML file through the same build_message() path, so what you see is what gets sent
PREVIEW_SCENARIOS = {
    "deploy-success": {
        "NOTIFICATION_TYPE": "deployment", "STATUS": "success", "ENVIRONMENT": "staging",
        "BINARY_NAME": "demo-gateway", "VERSION": "ghcr.io/your-org/demo-gateway:dev-c36b7d0-20261030",
        "GIT_COMMIT": "c36b7d0a1b2c3d4e5f6a7b8c", "OPERATOR": "kamalyes",
        "GITHUB_REPOSITORY": "your-org/demo-gateway", "NAMESPACE": "business",
        "DEPLOYMENT_REPLICAS": "2", "HTTP_PORT": "8082", "RPC_PORT": "9082",
        "CPU_REQUEST": "100m", "CPU_LIMIT": "300m",
        "MEMORY_REQUEST": "128Mi", "MEMORY_LIMIT": "256Mi",
        "CONFIG_YAML": "config/config.dev.yaml",
        "WORKFLOW_URL": "https://github.com/your-org/demo-gateway/actions/runs/41017643925",
    },
    "deploy-failure": {
        "NOTIFICATION_TYPE": "deployment", "STATUS": "failure", "ENVIRONMENT": "prod",
        "BINARY_NAME": "demo-api", "VERSION": "ghcr.io/your-org/demo-api:v1.4.2",
        "GIT_COMMIT": "9d8680bf1a2b3c", "OPERATOR": "kamalyes",
        "GITHUB_REPOSITORY": "your-org/demo-api", "NAMESPACE": "business",
        "ERROR_MESSAGE": (
            "Error: ImagePullBackOff\n"
            'failed to pull image "ghcr.io/your-org/demo-api:v1.4.2": '
            "rpc error: code = Unknown desc = failed to resolve reference: unauthorized"
        ),
        "WORKFLOW_URL": "https://github.com/your-org/demo-api/actions/runs/41017700001",
    },
    "rollback-success": {
        "NOTIFICATION_TYPE": "rollback", "STATUS": "success", "ENVIRONMENT": "prod",
        "BINARY_NAME": "demo-service", "VERSION": "ghcr.io/your-org/demo-service:v1.3.9",
        "GIT_COMMIT": "f6448490a1b2c3", "OPERATOR": "kamalyes",
        "GITHUB_REPOSITORY": "your-org/demo-service", "NAMESPACE": "business",
        "DEPLOYMENT_REPLICAS": "3",
        "WORKFLOW_URL": "https://github.com/your-org/demo-service/actions/runs/41017700002",
    },
    "custom-pr": {
        "NOTIFICATION_TYPE": "custom", "STATUS": "info", "ENVIRONMENT": "dev",
        "BINARY_NAME": "demo-gateway", "OPERATOR": "kamalyes",
        "GITHUB_REPOSITORY": "your-org/demo-gateway",
        "CUSTOM_TITLE": "📣 新 PR · demo-gateway · ci: 通知渠道增加邮件",
        "CUSTOM_MESSAGE": (
            "📦 **9 个文件**：➕ 4 新增 / 📝 5 修改 / ➖ 0 删除（+612/-18 行）\n"
            "- 新增 `scripts/send-email.py` 与四套邮件模板\n"
            "- 环境标签与配色改为可配置，补上了漏掉的 `staging`\n"
            "- 修复模板注释里的占位符被一并替换导致明细重复注入\n"
            "详见 [运行记录](https://github.com/your-org/demo-gateway/actions/runs/41017700003)"
        ),
        "WORKFLOW_URL": "https://github.com/your-org/demo-gateway/actions/runs/41017700003",
    },
}


def preview(outdir):
    global _ENV
    target = Path(outdir)
    target.mkdir(parents=True, exist_ok=True)
    saved, index = _ENV, []
    try:
        for name, scenario in PREVIEW_SCENARIOS.items():
            _ENV = scenario
            subject, body_html, plain_text = build_message()
            html_path = target / "{}.html".format(name)
            html_path.write_text(body_html, encoding="utf-8")
            (target / "{}.txt".format(name)).write_text(
                "Subject: {}\n\n{}\n".format(subject, plain_text), encoding="utf-8"
            )
            index.append((name, subject, html_path, len(body_html.encode())))
    finally:
        _ENV = saved

    # 汇总页：一次点开看全部，省得逐个找文件
    # [EN] An index page so every scenario is one click away
    cards = "".join(
        '<li><a href="{f}">{n}</a><div class="s">{s}</div>'
        '<div class="m">{k:.1f} KB · <a href="{t}">纯文本版</a></div></li>'.format(
            f=path.name, n=html.escape(name), s=html.escape(subject),
            k=size / 1024, t=path.with_suffix(".txt").name,
        )
        for name, subject, path, size in index
    )
    # 这段 CSS 里有大括号，所以用拼接而不是 .format：format 会把 {font:...} 当字段名
    # [EN] Concatenated rather than formatted: .format would read the CSS {font:...} as a field
    index_css = (
        "body{font:15px/1.7 -apple-system,'PingFang SC',sans-serif;max-width:720px;"
        "margin:40px auto;padding:0 20px;color:#1b2027}"
        "h1{font-size:22px}ul{list-style:none;padding:0;display:grid;gap:14px}"
        "li{border:1px solid #e2e6ea;border-radius:10px;padding:14px 18px}"
        "a{color:#1f5f8b}.s{font-size:13px;color:#555f6d;padding-top:3px}"
        ".m{font-size:12px;color:#8a95a3;padding-top:4px}"
        "@media(prefers-color-scheme:dark){body{background:#15181c;color:#e4e8ed}"
        "li{border-color:#2f353d}.s{color:#9aa5b2}}"
    )
    (target / "index.html").write_text(
        "<!DOCTYPE html><html lang=zh-CN><head><meta charset=utf-8>"
        "<title>邮件模板预览</title><style>" + index_css + "</style></head>"
        "<body><h1>邮件模板预览</h1>"
        "<p>渲染路径与真实发送完全一致，这里看到的就是收件箱里会收到的正文</p>"
        # 这句写在生成器里而不是事后往产物里补：产物每次重跑都整份覆盖，
        # 手工加的说明会被静默冲掉，而被冲掉的恰好是那句「不要手工编辑」
        # [EN] This line lives in the generator rather than being added to the output
        # afterwards: the output is overwritten wholesale on every run, so a hand-added note
        # is silently lost, and the note that gets lost is the one saying not to hand-edit
        "<p style='font-size:13px;color:#8a95a3'>本目录是 "
        "<code>python3 scripts/send-email.py --preview docs/email-preview</code> "
        "的生成物，只在 build 分支提交，改模板后重跑覆盖即可，不要手工编辑</p>"
        "<ul>" + cards + "</ul></body></html>",
        encoding="utf-8",
    )

    for name, subject, path, size in index:
        print("  {:<17} {:>6.1f} KB  {}".format(name, size / 1024, subject))
    print("\n汇总页：{}".format(target / "index.html"))


def main():
    smtp_host = env("EMAIL_SMTP_HOST")
    recipients = [a.strip() for a in env("EMAIL_TO").replace(";", ",").split(",") if a.strip()]
    smtp_user = env("EMAIL_SMTP_USER")
    smtp_pass = env("EMAIL_SMTP_PASSWORD")

    # 配置不全用退出码 2：调用方据此当「未配置、跳过」，而不是发送失败
    # [EN] Exit 2 for incomplete configuration so the caller treats it as not configured
    missing = [
        n
        for n, v in (
            ("EMAIL_SMTP_HOST", smtp_host),
            ("EMAIL_TO", ",".join(recipients)),
            ("EMAIL_SMTP_USER", smtp_user),
            ("EMAIL_SMTP_PASSWORD", smtp_pass),
        )
        if not v
    ]
    if missing:
        print("邮件未配置，缺少：{}".format(" ".join(missing)), file=sys.stderr)
        sys.exit(2)

    subject, body_html, plain_text = build_message()
    send(subject, body_html, plain_text, smtp_host, recipients, smtp_user, smtp_pass)


def build_message():
    """按场景渲染出 (主题, HTML 正文, 纯文本正文)，不碰 SMTP

    拆出来是为了预览模式能复用同一条渲染路径 —— 预览要是走另一套代码，
    看到的就不是实际会发出去的东西
    [EN] Split out so preview mode reuses the exact rendering path: a preview built by separate
    code would not be showing what actually gets sent
    """
    notify_type = env("NOTIFICATION_TYPE", "deployment")
    status = env("STATUS", "success")
    accent, icon = STATUS_PALETTE.get(status, STATUS_PALETTE["info"])

    service = env("BINARY_NAME") or env("GITHUB_REPOSITORY").split("/")[-1] or "service"
    environment = env("ENVIRONMENT", "dev")
    env_styles = parse_env_styles(env("NOTIFY_ENV_STYLES"))
    env_label, env_color, env_bg = env_styles.get(
        environment,
        (environment or "未知环境", ENV_STYLE_FALLBACK[0], ENV_STYLE_FALLBACK[1]),
    )
    workflow_url = env("WORKFLOW_URL")
    operator = env("OPERATOR") or env("GITHUB_ACTOR") or "unknown"
    repo = env("GITHUB_REPOSITORY")
    commit = env("GIT_COMMIT")

    if notify_type == "custom":
        title = env("CUSTOM_TITLE") or "通知"
        subtitle_bits = [b for b in (repo, operator and "by @" + operator) if b]
        extra = kv_rows(
            [
                ("环境", html.escape(env_label) if env("ENVIRONMENT") else ""),
                ("操作人", html.escape(operator) if operator != "unknown" else ""),
            ]
        )
        content = render(
            "custom.html",
            {
                "BODY_HTML": md_subset_to_html(env("CUSTOM_MESSAGE")),
                "DETAIL_ROWS_WRAPPED": (
                    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                    'border="0" class="rule" style="border:1px solid #e9edf1;border-radius:10px;'
                    'margin-top:14px;">{}</table>'.format(extra)
                    if extra
                    else ""
                ),
                "BUTTON": button(workflow_url, "查看运行详情"),
            },
        )
        plain_lines = [title, "", env("CUSTOM_MESSAGE")]
    else:
        action = "部署" if notify_type == "deployment" else "回滚"
        verb = {"success": "成功", "failure": "失败"}.get(status, status)
        title = "{}{}".format(action, verb)
        subtitle_bits = [b for b in (repo, operator and "by @" + operator) if b]
        ports = " / ".join(
            p for p in ("HTTP " + env("HTTP_PORT") if env("HTTP_PORT") else "",
                        "RPC " + env("RPC_PORT") if env("RPC_PORT") else "") if p
        )
        resources = ""
        if env("CPU_REQUEST") or env("MEMORY_REQUEST"):
            resources = "{} / {} → {} / {}".format(
                env("CPU_REQUEST") or "-", env("MEMORY_REQUEST") or "-",
                env("CPU_LIMIT") or "-", env("MEMORY_LIMIT") or "-",
            )
        content = render(
            "deployment.html",
            {
                "SERVICE": html.escape(service),
                "ENV_LABEL": html.escape(env_label),
                "ENV_COLOR": env_color,
                "ENV_BG": env_bg,
                "ERROR_BLOCK": error_block(env("ERROR_MESSAGE")) if status != "success" else "",
                "DETAIL_ROWS": kv_rows(
                    [
                        ("版本", mono(env("VERSION")) if env("VERSION") else ""),
                        ("提交", mono(commit[:12]) if commit else ""),
                        ("操作人", html.escape(operator) if operator != "unknown" else ""),
                        ("部署目标", html.escape(env("DEPLOYMENT_TARGET"))),
                        ("命名空间", mono(env("NAMESPACE")) if env("NAMESPACE") else ""),
                        ("副本数", html.escape(env("DEPLOYMENT_REPLICAS"))),
                        ("端口", mono(ports) if ports else ""),
                        ("资源 请求 → 上限", mono(resources) if resources else ""),
                        ("配置", mono(env("CONFIG_YAML")) if env("CONFIG_YAML") else ""),
                    ]
                ),
                "BUTTON": button(workflow_url, "查看运行详情"),
            },
        )
        plain_lines = [
            "{} {}".format(icon, title),
            "",
            "服务: {}".format(service),
            "环境: {}".format(env_label),
        ]
        for k, v in (
            ("版本", env("VERSION")), ("提交", commit[:12] if commit else ""),
            ("操作人", operator), ("命名空间", env("NAMESPACE")),
            ("副本数", env("DEPLOYMENT_REPLICAS")), ("端口", ports),
        ):
            if v:
                plain_lines.append("{}: {}".format(k, v))
        if status != "success" and env("ERROR_MESSAGE"):
            plain_lines += ["", "失败原因:", env("ERROR_MESSAGE")]

    if workflow_url:
        plain_lines += ["", "运行详情: {}".format(workflow_url)]

    # 主题分段拼接，但 custom 的标题通常已经带了服务名（PR 通知就是
    # 「📣 新 PR · demo-gateway · 标题」这种），再拼一次会在收件箱里看到两遍
    # [EN] Subject segments are joined, but a custom title usually already carries the service
    # name, as the PR notifications do, so appending it again shows it twice in the inbox
    subject_parts = ["{} {}".format(icon, title)]
    if service and service not in title:
        subject_parts.append(service)
    if env("ENVIRONMENT"):
        subject_parts.append(env_label)
    subject = " · ".join(subject_parts)

    body_html = render(
        "_base.html",
        {
            "TITLE": html.escape(title),
            "SUBTITLE": html.escape(" · ".join(subtitle_bits)),
            "PREHEADER": html.escape(
                "{} {} · {}".format(icon, title, service)
            ),
            "ACCENT": accent,
            "ICON": icon,
            "CONTENT": content,
            "FOOTER_TEXT": html.escape(
                " · ".join(b for b in (repo, commit[:12] if commit else "", operator) if b)
            ),
        },
    )

    return subject, body_html, "\n".join(plain_lines)


def send(subject, body_html, plain_text, smtp_host, recipients, smtp_user, smtp_pass):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((env("EMAIL_FROM_NAME", "CI 流水线"), env("EMAIL_FROM") or smtp_user))
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(env("EMAIL_FROM") or smtp_user).split("@")[-1])
    # 自动邮件声明：让收件方的规则和自动回复器知道别回信，也降低被判垃圾的概率
    # [EN] Marks the message as automatic so filters and auto-responders leave it alone
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(plain_text)
    msg.add_alternative(body_html, subtype="html")

    port = int(env("EMAIL_SMTP_PORT", "465"))
    mode = env("EMAIL_SMTP_TLS").lower() or ("ssl" if port == 465 else "starttls")
    context = ssl.create_default_context()

    try:
        if mode == "ssl":
            with smtplib.SMTP_SSL(smtp_host, port, timeout=30, context=context) as s:
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, port, timeout=30) as s:
                s.ehlo()
                if mode == "starttls":
                    s.starttls(context=context)
                    s.ehlo()
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        print(
            "::error::SMTP 认证失败（{}）；多数邮箱要用「授权码」而不是登录密码".format(exc.smtp_code),
            file=sys.stderr,
        )
        sys.exit(1)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        print(
            "::error::邮件发送失败：{}（host={} port={} mode={}）".format(
                exc, smtp_host, port, mode
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    print("邮件已发送至 {} 个收件人：{}".format(len(recipients), ", ".join(recipients)))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--preview", "-p"):
        preview(sys.argv[2] if len(sys.argv) > 2 else "email-preview")
    else:
        main()
