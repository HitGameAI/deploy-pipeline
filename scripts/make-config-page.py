#!/usr/bin/env python3
# ============================================================================
# 由 schema 生成一张离线的配置页面 | Generate an offline configuration page from the schema
#
# 为什么要这张页面：这两份聚合文档里有多行值（kubeconfig、PEM 私钥），而 YAML 的块标量
# 要求正文每一行都缩进到同一层 手写时粘贴过来的原文没有缩进，只缩第一行几乎是必然，
# 而后果有两种，都难查：
#   跑出去的行不构成合法 YAML → 语法错误，报错位置在真正起因之后好几行
#   恰好构成合法 YAML（kubeconfig 就是）→ 不报语法错，那些行变成顶层节，
#     报出来的是「未知配置节 'clusters'，是否想写 docker？」，三样全错
# 缩进这种事不该靠人记，交给界面
#
# 为什么是生成的而不是手写的 HTML：字段有 116 个，手抄一份第一次改 schema 就漂移
# 这个脚本把 schema 原样嵌进页面，字段、类型、默认值、校验规则、说明全从那里来
#
# 三条硬约束 | Three hard constraints
#   单文件：CSS 与 JS 全内联，没有任何外部引用 —— 引一个字体或 CDN 就多一次外部请求，
#     而这张页面是要往里粘私钥的
#   零网络：没有 fetch、没有表单提交、没有 beacon 值只在当前标签页里，关掉就没了
#   校验与 resolve-config.py 同规则：页面说通过而 CI 说不通过是最坏的结果
#
# 用法 | Usage
#   python3 scripts/make-config-page.py -o config-page/index.html
#   然后浏览器直接打开那个文件（file:// 即可，不需要起服务）
#
# [EN] Generates a single offline page for filling in the two aggregate documents. It exists
# because those documents contain multi-line values such as a kubeconfig or a PEM key, and a
# YAML block scalar needs every body line at the same indent; pasted text carries none, so
# indenting only the first line is near-inevitable and fails in two hard-to-read ways: escaped
# lines that are invalid YAML produce a syntax error several lines after the real cause, and
# escaped lines that happen to be valid YAML -- a kubeconfig is -- produce no syntax error at
# all and are read as top-level sections. The page is generated rather than hand-written
# because there are 116 fields and a hand-made copy drifts the first time the schema changes.
# It is one file with everything inlined and no external reference, makes no network request of
# any kind, and mirrors resolve-config.py's validation so the page and CI cannot disagree.
# ============================================================================
import argparse
import json
import os
import sys

try:
    import yaml
except ImportError:
    sys.exit("需要 PyYAML：pip install pyyaml | PyYAML required")

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "config", "schema.yaml")

# 这几个键的值是多行的，页面给 textarea 加「从文件读」
# [EN] These values are multi-line, so the page offers a textarea and a local file picker
MULTILINE = {"kubeconfig", "app_private_key", "git_ssh_private_key"}


def build_model():
    with open(SCHEMA, encoding="utf-8") as fh:
        schema = yaml.safe_load(fh)
    model = {"plain": [], "secret": []}
    for group, bucket in (("sections", "plain"), ("secret_sections", "secret")):
        for section, body in (schema.get(group) or {}).items():
            node = {
                "name": section,
                "desc": (body.get("desc") or "").split(" | ")[0],
                "free_form": bool(body.get("free_form")),
                "keys": [],
            }
            for key, spec in (body.get("keys") or {}).items():
                node["keys"].append({
                    "path": str(key),
                    "type": spec.get("type", "string"),
                    "default": "" if spec.get("default") is None else str(spec.get("default")),
                    "desc": (spec.get("desc") or "").split(" | ")[0],
                    "values": [str(v) for v in (spec.get("values") or [])],
                    "min": spec.get("min"),
                    "max": spec.get("max"),
                    "multiline": str(key) in MULTILINE,
                })
            model[bucket].append(node)
    return model


# JSON 注入 <script> 时要把 < 转义：否则值里出现 </script> 就提前闭合了标签
# [EN] < is escaped when injecting JSON into a script element, or a value containing
# </script> would close the tag early
def embed(obj):
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--line:#e3e7ec;--ink:#1b2027;--dim:#5b6673;
--accent:#1f5f8b;--bad:#b3261e;--ok:#146c43;--code:#f2f4f7}
@media(prefers-color-scheme:dark){:root{--bg:#15181c;--card:#1d2127;--line:#2f353d;
--ink:#e4e8ed;--dim:#9aa5b2;--accent:#6fb3e0;--bad:#f2b8b5;--ok:#7ddba6;--code:#23282f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.65 -apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif}
header{padding:22px 28px 14px;border-bottom:1px solid var(--line);background:var(--card)}
h1{margin:0 0 6px;font-size:19px}
.sub{color:var(--dim);font-size:13px}
.warn{margin:14px 28px 0;padding:11px 14px;border-left:3px solid var(--ok);
background:var(--card);border-radius:0 6px 6px 0;font-size:13px;color:var(--dim)}
.warn b{color:var(--ink)}
.wrap{display:grid;grid-template-columns:1fr 1fr;gap:18px;padding:18px 28px 40px;align-items:start}
@media(max-width:1100px){.wrap{grid-template-columns:1fr}}
.tabs{display:flex;gap:6px;padding:14px 28px 0}
.tab{padding:7px 15px;border:1px solid var(--line);border-radius:7px;background:var(--card);
cursor:pointer;font-size:14px;color:var(--dim)}
.tab[aria-selected=true]{color:var(--ink);border-color:var(--accent);font-weight:600}
fieldset{border:1px solid var(--line);border-radius:9px;margin:0 0 14px;padding:8px 14px 14px;
background:var(--card)}
legend{padding:0 7px;font-weight:600;font-size:14px}
legend small{font-weight:400;color:var(--dim);margin-left:7px}
.row{display:grid;grid-template-columns:minmax(150px,230px) 1fr;gap:10px;align-items:start;
padding:7px 0;border-top:1px dashed var(--line)}
.row:first-of-type{border-top:0}
label{font-size:13px;word-break:break-all}
label em{display:block;color:var(--dim);font-style:normal;font-size:12px;padding-top:2px}
input,select,textarea{width:100%;padding:6px 9px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--ink);font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
textarea{min-height:88px;resize:vertical}
input:focus,select,textarea:focus{outline:2px solid var(--accent);outline-offset:-1px}
.err{color:var(--bad);font-size:12px;padding-top:3px}
.file{font-size:12px;color:var(--dim);padding-top:4px}
.kv{display:grid;grid-template-columns:1fr 1fr auto;gap:6px;padding-bottom:6px}
button{padding:6px 13px;border:1px solid var(--line);border-radius:6px;background:var(--card);
color:var(--ink);cursor:pointer;font-size:13px}
button.primary{border-color:var(--accent);color:var(--accent);font-weight:600}
.out{position:sticky;top:18px}
pre{margin:0;padding:13px;background:var(--code);border:1px solid var(--line);border-radius:9px;
overflow:auto;max-height:52vh;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
white-space:pre;tab-size:2}
.bar{display:flex;gap:8px;align-items:center;padding:0 0 9px;flex-wrap:wrap}
.bar .grow{flex:1}
.cnt{font-size:12px;color:var(--dim)}
.cnt b.bad{color:var(--bad)}
.hint{font-size:12px;color:var(--dim);padding-top:9px}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:12px}
"""

JS = r"""
const MODEL = JSON.parse(document.getElementById('schema').textContent);
const BOOL_TRUE = ['true','yes','on','1'], BOOL_FALSE = ['false','no','off','0'];
const QTY = /^\d+(\.\d+)?(m|Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E)?$/;
let TAB = 'plain';
const state = {plain:{}, secret:{}, free:{plain:{}, secret:{}}};

/* 校验规则逐条对着 resolve-config.py 的 validate() 写
   页面说通过而 CI 说不通过，比页面不校验更糟：人会信页面 */
function check(spec, raw){
  const v = (raw ?? '').trim();
  if (spec.type === 'secret' || spec.type === 'string' || spec.type === 'csv') return '';
  if (v === '' && spec.type !== 'int') return '';
  if (spec.type === 'bool')
    return [...BOOL_TRUE, ...BOOL_FALSE].includes(v.toLowerCase()) ? ''
      : '期望 true/false（也接受 yes/no/on/off/1/0）';
  if (spec.type === 'port'){
    const l = v.toLowerCase();
    if (['', '0', 'none'].includes(l)) return '';
    return /^\d+$/.test(l) && +l >= 1 && +l <= 65535 ? '' : '期望 1-65535，或留空 / 0 / none 表示不启用';
  }
  if (spec.type === 'int'){
    if (v === '') return '期望整数，实际是空值';
    if (!/^-?\d+$/.test(v)) return '期望整数';
    const n = +v;
    if (spec.min != null && n < spec.min) return `期望 ${spec.min}-${spec.max ?? '∞'} 之间`;
    if (spec.max != null && n > spec.max) return `期望 ${spec.min ?? '-∞'}-${spec.max} 之间`;
    return '';
  }
  if (spec.type === 'percent')
    return /^\d+$/.test(v) && +v >= 1 && +v <= 1000 ? '' : '期望 1-1000 的整数（不带 %）';
  if (spec.type === 'quantity')
    return QTY.test(v) ? '' : '期望 K8s 资源量，如 100m / 1 / 256Mi / 1Gi';
  if (spec.type === 'enum')
    return spec.values.includes(v) ? '' : '只能是 ' + spec.values.join(' / ') + ' 之一';
  return '';
}

/* 块标量：正文每一行都缩进到同一层 —— 这正是手写时会错的那一步 */
function yamlScalar(value, indent){
  if (value.includes('\n')){
    const pad = ' '.repeat(indent + 2);
    const body = value.replace(/\n+$/, '').split('\n').map(l => pad + l).join('\n');
    return (value.endsWith('\n') ? '|\n' : '|-\n') + body;
  }
  if (value === '') return "''";
  if (/^[-?:,[\]{}#&*!|>'"%@`]/.test(value) || /^(true|false|yes|no|on|off|null|~)$/i.test(value)
      || /^-?\d+(\.\d+)?$/.test(value) || /:\s/.test(value) || /\s$/.test(value))
    return JSON.stringify(value);
  return value;
}

function nest(flat){
  const root = {};
  for (const [path, value] of flat){
    const parts = path.split('.');
    let node = root;
    parts.slice(0, -1).forEach(p => { node = node[p] = node[p] || {}; });
    node[parts.at(-1)] = value;
  }
  return root;
}

function render(tree, indent = 0){
  let out = '';
  for (const [k, v] of Object.entries(tree)){
    const pad = ' '.repeat(indent);
    if (v && typeof v === 'object'){ out += `${pad}${k}:\n` + render(v, indent + 2); }
    else out += `${pad}${k}: ${yamlScalar(v, indent)}\n`;
  }
  return out;
}

function build(bucket){
  const flat = [], errs = [];
  for (const sec of MODEL[bucket]){
    if (sec.free_form){
      const rows = state.free[bucket][sec.name] || [];
      for (const [k, v] of rows) if (k.trim()) flat.push([`${sec.name}.${k.trim()}`, v]);
      continue;
    }
    for (const spec of sec.keys){
      const id = `${sec.name}.${spec.path}`;
      const raw = state[bucket][id];
      if (raw == null || raw === '') continue;        /* 没填的不写进文档，保持最小 */
      const e = check(spec, raw);
      if (e) errs.push(`${id}: ${e}`);
      flat.push([id, raw]);
    }
  }
  return {text: render(nest(flat)), errs, count: flat.length};
}

function refresh(){
  for (const bucket of ['plain','secret']){
    const {text, errs, count} = build(bucket);
    const name = bucket === 'plain' ? 'PIPELINE_CONFIG' : 'PIPELINE_SECRETS';
    document.getElementById('out-' + bucket).textContent = text || '# 还没填任何项\n';
    const c = document.getElementById('cnt-' + bucket);
    c.innerHTML = `${count} 项` + (errs.length ? ` · <b class="bad">${errs.length} 处不合法</b>` : ' · 校验通过');
    document.getElementById('errs-' + bucket).textContent = errs.join('\n');
    document.getElementById('cmd-' + bucket).textContent = bucket === 'plain'
      ? `gh variable set ${name} --repo <owner/repo> < config.yaml`
      : `gh secret set ${name} --repo <owner/repo> --env <环境> < secrets.yaml`;
  }
}

function field(bucket, sec, spec){
  const id = `${sec.name}.${spec.path}`;
  const row = document.createElement('div'); row.className = 'row';
  const lab = document.createElement('label'); lab.setAttribute('for', id);
  lab.innerHTML = `<code>${spec.path}</code>` +
    (spec.desc ? `<em>${spec.desc}</em>` : '') +
    `<em>${spec.type}${spec.default ? ' · 默认 ' + spec.default : ''}</em>`;
  const box = document.createElement('div');
  let input;
  if (spec.type === 'enum' || spec.type === 'bool'){
    input = document.createElement('select');
    const opts = spec.type === 'bool' ? ['', 'true', 'false'] : ['', ...spec.values];
    for (const o of opts){
      const el = document.createElement('option');
      el.value = o; el.textContent = o === '' ? '（不设置，用默认值）' : o;
      input.appendChild(el);
    }
  } else if (spec.multiline){
    input = document.createElement('textarea');
    input.placeholder = '整段粘贴进来即可，缩进由页面处理';
  } else {
    input = document.createElement('input');
    input.type = 'text';
    input.placeholder = spec.default ? `默认 ${spec.default}` : '';
  }
  input.id = id;
  input.addEventListener('input', () => {
    state[bucket][id] = input.value;
    err.textContent = check(spec, input.value);
    refresh();
  });
  box.appendChild(input);
  if (spec.multiline){
    const pick = document.createElement('input');
    pick.type = 'file'; pick.className = 'file';
    /* 本地读取，不上传：FileReader 全程在页面里 */
    pick.addEventListener('change', () => {
      const f = pick.files[0]; if (!f) return;
      const r = new FileReader();
      r.onload = () => { input.value = r.result; state[bucket][id] = r.result; refresh(); };
      r.readAsText(f);
    });
    box.appendChild(pick);
  }
  const err = document.createElement('div'); err.className = 'err';
  box.appendChild(err);
  row.append(lab, box);
  return row;
}

function freeRows(bucket, sec){
  const box = document.createElement('div');
  state.free[bucket][sec.name] = state.free[bucket][sec.name] || [];
  const rows = state.free[bucket][sec.name];
  const list = document.createElement('div');
  const draw = () => {
    list.innerHTML = '';
    rows.forEach((pair, i) => {
      const line = document.createElement('div'); line.className = 'kv';
      const k = document.createElement('input'); k.placeholder = '变量名（大写）'; k.value = pair[0];
      const v = document.createElement('input'); v.placeholder = '值'; v.value = pair[1];
      k.addEventListener('input', () => { pair[0] = k.value; refresh(); });
      v.addEventListener('input', () => { pair[1] = v.value; refresh(); });
      const del = document.createElement('button'); del.textContent = '删';
      del.addEventListener('click', () => { rows.splice(i, 1); draw(); refresh(); });
      line.append(k, v, del);
      list.appendChild(line);
    });
  };
  const add = document.createElement('button');
  add.textContent = '+ 加一项'; add.className = 'primary';
  add.addEventListener('click', () => { rows.push(['', '']); draw(); refresh(); });
  draw();
  box.append(list, add);
  return box;
}

function mount(){
  for (const bucket of ['plain','secret']){
    const host = document.getElementById('form-' + bucket);
    for (const sec of MODEL[bucket]){
      const fs = document.createElement('fieldset');
      const lg = document.createElement('legend');
      lg.innerHTML = `${sec.name}<small>${sec.desc || ''}</small>`;
      fs.appendChild(lg);
      if (sec.free_form){
        const note = document.createElement('div'); note.className = 'hint';
        note.textContent = '自由键值表：键名就是容器里的环境变量名';
        fs.append(note, freeRows(bucket, sec));
      } else {
        for (const spec of sec.keys) fs.appendChild(field(bucket, sec, spec));
      }
      host.appendChild(fs);
    }
  }
  document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
    TAB = t.dataset.tab;
    document.querySelectorAll('.tab').forEach(x => x.setAttribute('aria-selected', x === t));
    document.querySelectorAll('[data-pane]').forEach(p =>
      p.hidden = p.dataset.pane !== TAB);
  }));
  document.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', async () => {
    const text = document.getElementById('out-' + b.dataset.copy).textContent;
    try { await navigator.clipboard.writeText(text); b.textContent = '已复制'; }
    catch { b.textContent = '复制失败，手动选'; }
    setTimeout(() => b.textContent = '复制', 1500);
  }));
  document.querySelectorAll('[data-save]').forEach(b => b.addEventListener('click', () => {
    const bucket = b.dataset.save;
    const text = document.getElementById('out-' + bucket).textContent;
    /* Blob + a[download]：本地另存，不经网络 */
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], {type:'text/yaml'}));
    a.download = bucket === 'plain' ? 'PIPELINE_CONFIG.yaml' : 'PIPELINE_SECRETS.yaml';
    a.click(); URL.revokeObjectURL(a.href);
  }));
  refresh();
}
mount();
"""

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>流水线配置生成器</title>
<style>%(css)s</style>
</head>
<body>
<header>
  <h1>流水线配置生成器</h1>
  <div class="sub">填完复制走 —— 明文配置进 <code>vars.PIPELINE_CONFIG</code>，密钥进
    <code>secrets.PIPELINE_SECRETS</code>　字段、类型、默认值与校验规则都来自
    <code>scripts/config/schema.yaml</code>，共 %(nplain)d 个明文键、%(nsecret)d 个密钥键</div>
</header>
<div class="warn">
  <b>这张页面不联网</b>　没有任何外部引用，也没有 fetch、表单提交或上报：你填进去的值只在
  当前标签页里，关掉就没了　选文件走的是浏览器本地读取，文件内容同样不出页面
  <br><b>生成的密钥文档不要提交进仓库</b>　它只该贴进 GitHub 的 Secrets 页面，或者用
  <code>gh secret set</code> 从本地文件喂进去
</div>
<div class="tabs" role="tablist">
  <button class="tab" data-tab="plain" role="tab" aria-selected="true">明文配置 PIPELINE_CONFIG</button>
  <button class="tab" data-tab="secret" role="tab" aria-selected="false">密钥 PIPELINE_SECRETS</button>
</div>
%(panes)s
<script id="schema" type="application/json">%(schema)s</script>
<script>%(js)s</script>
</body>
</html>
"""

PANE = """
<div class="wrap" data-pane="%(bucket)s"%(hidden)s>
  <div><div id="form-%(bucket)s"></div></div>
  <div class="out">
    <div class="bar">
      <strong>%(title)s</strong>
      <span class="grow"></span>
      <span class="cnt" id="cnt-%(bucket)s"></span>
      <button data-copy="%(bucket)s">复制</button>
      <button data-save="%(bucket)s">另存</button>
    </div>
    <pre id="out-%(bucket)s"></pre>
    <div class="err" id="errs-%(bucket)s"></div>
    <div class="hint">写进仓库：<code id="cmd-%(bucket)s"></code></div>
    <div class="hint">%(note)s</div>
  </div>
</div>
"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="config-page/index.html")
    args = ap.parse_args(argv)

    model = build_model()
    nplain = sum(len(s["keys"]) for s in model["plain"])
    nsecret = sum(len(s["keys"]) for s in model["secret"])

    panes = PANE % {
        "bucket": "plain", "hidden": "", "title": "vars.PIPELINE_CONFIG",
        "note": "只写填了的键，没填的走 schema 默认值 —— 文档越小越好查",
    } + PANE % {
        "bucket": "secret", "hidden": " hidden", "title": "secrets.PIPELINE_SECRETS",
        "note": "要配在 Environment 上，而且每个环境那份要写全："
                "同名的 Environment secret 整体取代仓库级那一份，不做逐键合并",
    }
    html = HTML % {"css": CSS, "js": JS, "schema": embed(model),
                   "panes": panes, "nplain": nplain, "nsecret": nsecret}

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("✅ %s" % out)
    print("   %d 个明文键 / %d 个密钥键，%.1f KB，单文件无外部引用"
          % (nplain, nsecret, os.path.getsize(out) / 1024))
    print("   浏览器直接打开即可，不需要起服务")
    return 0


if __name__ == "__main__":
    sys.exit(main())
