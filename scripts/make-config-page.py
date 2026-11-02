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
--accent:#1f5f8b;--bad:#b3261e;--ok:#146c43;--code:#f2f4f7;--warnbg:#fff8e6;--warnline:#c98a00}
@media(prefers-color-scheme:dark){:root{--bg:#15181c;--card:#1d2127;--line:#2f353d;
--ink:#e4e8ed;--dim:#9aa5b2;--accent:#6fb3e0;--bad:#f2b8b5;--ok:#7ddba6;--code:#23282f;
--warnbg:#2a2418;--warnline:#d9a21b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.65 -apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif}
header{padding:18px 24px 12px;border-bottom:1px solid var(--line);background:var(--card)}
h1{margin:0 0 5px;font-size:18px}
.sub{color:var(--dim);font-size:13px}
.warn{margin:12px 24px 0;padding:10px 13px;border-left:3px solid var(--ok);
background:var(--card);border-radius:0 6px 6px 0;font-size:13px;color:var(--dim)}
.warn b{color:var(--ink)}
.wrap{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px 24px 40px;align-items:start}
@media(max-width:1100px){.wrap{grid-template-columns:1fr}}
.col{min-width:0}
.bar{display:flex;align-items:center;gap:9px;padding:7px 12px;border:1px solid var(--line);
border-bottom:0;border-radius:9px 9px 0 0;background:var(--card);font-size:13px}
.bar .grow{flex:1}
.bar strong{font-size:13px}
.panel{border:1px solid var(--line);border-radius:0 0 9px 9px;background:var(--card);
padding:12px;margin-bottom:14px}
button{padding:5px 11px;border:1px solid var(--line);border-radius:6px;background:var(--bg);
color:var(--ink);cursor:pointer;font-size:12px}
button:hover:not(:disabled){border-color:var(--accent)}
button:disabled{opacity:.45;cursor:default}
#src{width:100%;min-height:340px;resize:vertical;border:1px solid var(--line);border-radius:0 0 9px 9px;
background:var(--card);color:var(--ink);padding:11px 13px;
font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}
#src:focus{outline:2px solid var(--accent);outline-offset:-2px}
pre{margin:0;padding:11px 13px;background:var(--code);border-radius:7px;overflow:auto;
max-height:260px;font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;
word-break:break-all}
section{border:1px solid var(--line);border-radius:9px;margin:0 0 12px;padding:9px 13px 13px;
background:var(--card)}
section h3{margin:0 0 4px;font-size:14px}
section h3 small{font-weight:400;color:var(--dim);margin-left:7px;font-size:12px}
.row{padding:8px 0;border-top:1px dashed var(--line)}
.row:first-of-type{border-top:0}
.row.bad{background:color-mix(in srgb,var(--bad) 7%,transparent);border-radius:6px;
padding-left:8px;padding-right:8px}
.lab{display:flex;align-items:center;gap:7px;margin-bottom:5px;flex-wrap:wrap}
.lab code{font-size:13px}
.lab b{font-weight:600;font-size:11px;padding:1px 6px;border-radius:10px;letter-spacing:.3px}
.b-plain{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent)}
.b-secret{background:color-mix(in srgb,var(--bad) 14%,transparent);color:var(--bad)}
.b-free{background:var(--code);color:var(--dim)}
.b-unknown{background:var(--warnbg);color:var(--warnline)}
.del{margin-left:auto;padding:1px 7px;line-height:1.5;color:var(--dim)}
input,select,textarea{width:100%;padding:6px 9px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--ink);font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
textarea{resize:vertical}
.sw{display:inline-flex;align-items:center;gap:8px;font:13px/1.5 ui-monospace,Menlo,monospace}
.sw input{width:auto}
.hint{color:var(--dim);font-size:12px;padding-top:3px}
.err{color:var(--bad);font-size:12px;padding-top:3px}
.bad{color:var(--bad)}
.empty{color:var(--dim);font-size:13px;padding:26px 14px;border:1px dashed var(--line);
border-radius:9px;background:var(--card);line-height:1.9}
#addable{margin-top:4px}
#addable summary{cursor:pointer;font-size:13px;color:var(--dim);padding:7px 0}
.adds{display:flex;flex-wrap:wrap;gap:6px;padding:6px 0 2px}
.add{display:inline-flex;align-items:center;gap:6px;font:12px/1.5 ui-monospace,Menlo,monospace}
.add span{font-size:10px;padding:1px 5px;border-radius:9px}
#unknown{margin-top:10px;font-size:13px;color:var(--dim)}
#unknown pre{margin-top:6px;max-height:140px}
code{background:var(--code);padding:1px 5px;border-radius:4px;
font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
"""

JS = r"""
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

function parseDoc(text){
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const flat = {}, unknown = [];
  const stack = [];                      /* [{indent, key}] */
  let i = 0;
  while (i < lines.length){
    const line = lines[i];
    if (!line.trim() || line.trim().startsWith('#')){ i++; continue; }
    const indent = line.length - line.trimStart().length;
    const m = line.match(/^\s*([\w.\-]+):\s*(.*)$/);
    if (!m){ unknown.push(`第 ${i+1} 行：${line.trim().slice(0,60)}`); i++; continue; }
    const [, key, rawRest] = m;
    const rest = rawRest.replace(/\s+#.*$/, '').trim();
    while (stack.length && stack.at(-1).indent >= indent) stack.pop();
    const path = [...stack.map(x => x.key), key].join('.');
    if (/^[|>][-+]?$/.test(rest)){
      /* 块标量：收集所有缩进更深的行，按第一行的缩进统一剥掉 */
      const body = [];
      let j = i + 1, base = null;
      while (j < lines.length){
        const nxt = lines[j];
        if (!nxt.trim()){ body.push(''); j++; continue; }
        const ind = nxt.length - nxt.trimStart().length;
        if (ind <= indent) break;
        if (base === null) base = ind;
        body.push(nxt.slice(base));
        j++;
      }
      while (body.length && body.at(-1) === '') body.pop();
      flat[path] = body.join('\n') + (rest.includes('-') ? '' : '\n');
      i = j; continue;
    }
    if (rest === ''){ stack.push({indent, key}); i++; continue; }
    let v = rest;
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'")))
      v = v.slice(1, -1);
    flat[path] = v;
    i++;
  }
  return {flat, unknown};
}const MODEL = JSON.parse(document.getElementById('schema').textContent);
const BOOL_TRUE = ['true','yes','on','1'], BOOL_FALSE = ['false','no','off','0'];
const QTY = /^\d+(\.\d+)?(m|Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E)?$/;

/* 一次把 schema 摊平成查表：路径 → 它属于哪份文档、它的类型与校验规则
   节名在两份文档里会重复（kube / docker / notify 都既有明文键也有密钥键），
   所以能判定归属的只有**整条路径**，不是节名 —— 这正是人最容易填错的地方：
   把 kube.kubeconfig 写进 PIPELINE_CONFIG，流水线读不到，而且不会报错
   [EN] The schema is flattened once into a lookup. Section names repeat across the two documents
   (kube, docker and notify each hold both plaintext and secret keys), so only the full path
   decides which document a key belongs to -- which is exactly what people get wrong, putting
   kube.kubeconfig into PIPELINE_CONFIG where the pipeline cannot see it and nothing complains. */
const INDEX = {}, FREE = {}, SECTION = {};
for (const bucket of ['plain','secret']){
  for (const sec of MODEL[bucket]){
    SECTION[bucket + ':' + sec.name] = sec;
    if (sec.free_form){ FREE[sec.name] = {bucket, sec}; continue; }
    for (const spec of sec.keys) INDEX[sec.name + '.' + spec.path] = {bucket, sec, spec};
  }
}

let DOC = {};          /* 路径 → 值，顺序就是粘贴进来的顺序 */
let UNKNOWN = [];
let WRITING = false;   /* 回写 textarea 时不要再去解析它，否则光标会被打断 */

function classify(path){
  if (INDEX[path]) return INDEX[path];
  const sec = path.split('.')[0];
  if (FREE[sec]) return {bucket: FREE[sec].bucket, sec: FREE[sec].sec, spec: null, free: true};
  return null;
}

function esc(s){ return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

/* ============ 右边：YAML 是唯一的事实来源 ============ */

function readSource(){
  const r = parseDoc(document.getElementById('src').value);
  DOC = r.flat;
  UNKNOWN = r.unknown;
  renderForm();
  renderOutputs();
}

function writeSource(){
  const pairs = Object.keys(DOC).map(p => [p, DOC[p]]);
  WRITING = true;
  document.getElementById('src').value = pairs.length ? render(nest(pairs)) : '';
  WRITING = false;
  renderOutputs();
}

/* ============ 左边：按 DOC 里实际有什么来摆 ============ */

function control(path, info){
  const v = DOC[path];
  const spec = info && info.spec;
  const multi = String(v).includes('\n') || (spec && spec.multiline);
  if (multi){
    return `<textarea data-path="${esc(path)}" rows="${Math.min(14, String(v).split('\n').length + 1)}">${esc(v)}</textarea>`;
  }
  if (spec && spec.type === 'bool'){
    const on = BOOL_TRUE.includes(String(v).toLowerCase());
    return `<label class="sw"><input type="checkbox" data-path="${esc(path)}"${on ? ' checked' : ''}><span>${on ? 'true' : 'false'}</span></label>`;
  }
  if (spec && spec.values && spec.values.length){
    const opts = spec.values.map(o => `<option${String(v) === o ? ' selected' : ''}>${esc(o)}</option>`).join('');
    return `<select data-path="${esc(path)}">${opts}</select>`;
  }
  return `<input type="text" data-path="${esc(path)}" value="${esc(v)}">`;
}

function renderForm(){
  const host = document.getElementById('form');
  const paths = Object.keys(DOC);
  if (!paths.length){
    host.innerHTML = `<div class="empty">右边贴一份 <code>PIPELINE_CONFIG</code> 或
      <code>PIPELINE_SECRETS</code> 的 YAML，这里就按它的内容自动摆出表单<br>
      两份混在一起贴也行 —— 每一项属于哪份文档由 schema 判定，下面会逐项标出来</div>`;
    return;
  }
  /* 按节分组，但节的先后顺序跟着粘贴顺序走，不按 schema 的顺序重排 ——
     人对着自己贴进来的那份找东西，重排反而让人找不到
     [EN] Grouped by section in paste order rather than schema order: people look for a key where
     they typed it, and reordering makes it harder to find */
  const groups = [], seen = {};
  for (const p of paths){
    const sec = p.split('.')[0];
    if (!seen[sec]){ seen[sec] = []; groups.push([sec, seen[sec]]); }
    seen[sec].push(p);
  }
  let html = '', nerr = 0;
  for (const [sec, items] of groups){
    const any = classify(items[0]);
    const desc = any && any.sec ? any.sec.desc : '';
    html += `<section><h3>${esc(sec)}${desc ? `<small>${esc(desc)}</small>` : ''}</h3>`;
    for (const p of items){
      const info = classify(p);
      const key = p.slice(sec.length + 1);
      let badge, err = '';
      if (!info) badge = '<b class="b-unknown">未知键</b>';
      else if (info.free) badge = `<b class="b-${info.bucket}">${info.bucket === 'plain' ? '明文' : '密钥'}</b><b class="b-free">自由键</b>`;
      else {
        badge = `<b class="b-${info.bucket}">${info.bucket === 'plain' ? '明文' : '密钥'}</b>`;
        const e = check(info.spec, DOC[p]);
        if (e){ err = `<div class="err">${esc(e)}</div>`; nerr++; }
      }
      const spec = info && info.spec;
      html += `<div class="row${err ? ' bad' : ''}">
        <div class="lab"><code>${esc(key)}</code>${badge}
          <button class="del" data-del="${esc(p)}" title="从文档里删掉">✕</button></div>
        ${control(p, info)}
        ${spec && spec.desc ? `<div class="hint">${esc(spec.desc)}</div>` : ''}
        ${spec && spec.default ? `<div class="hint">默认 <code>${esc(spec.default)}</code></div>` : ''}
        ${!info ? '<div class="hint">schema 里没有这个键 —— 要么写错了，要么 schema 该加上它</div>' : ''}
        ${err}
      </div>`;
    }
    html += '</section>';
  }
  host.innerHTML = html;
  document.getElementById('nerr').innerHTML = nerr
    ? `<b class="bad">${nerr} 处不合法</b>` : (paths.length ? '校验通过' : '');
  renderAddable();
}

/* schema 里有、这份文档里没有的 —— 折叠放在底下，点一下按默认值加进去
   不默认展开：这页面的用处是改手上那份，不是把 116 个键全填一遍
   [EN] Keys the schema defines but this document lacks, collapsed and added with their default on
   click. Not expanded by default: the page exists to edit the document in hand, not to fill in all
   116 keys */
function renderAddable(){
  const miss = [];
  for (const path in INDEX) if (!(path in DOC)) miss.push(path);
  const host = document.getElementById('addable');
  if (!miss.length){ host.innerHTML = ''; return; }
  const items = miss.map(p => {
    const i = INDEX[p];
    return `<button data-add="${esc(p)}" class="add"><span class="b-${i.bucket}">${i.bucket === 'plain' ? '明文' : '密钥'}</span>${esc(p)}</button>`;
  }).join('');
  host.innerHTML = `<details><summary>还没填的 ${miss.length} 项（点一下按默认值加进来）</summary>
    <div class="adds">${items}</div></details>`;
}

/* ============ 两份输出：按每个键的归属拆开 ============ */

function renderOutputs(){
  for (const bucket of ['plain','secret']){
    const pairs = [];
    for (const p in DOC){
      const info = classify(p);
      if (info && info.bucket === bucket) pairs.push([p, DOC[p]]);
    }
    const text = pairs.length ? render(nest(pairs)) : '';
    document.getElementById('out-' + bucket).textContent = text || '# 这份文档里没有属于它的项\n';
    document.getElementById('cnt-' + bucket).textContent = pairs.length + ' 项';
    document.getElementById('copy-' + bucket).disabled = !pairs.length;
  }
  const u = document.getElementById('unknown');
  u.innerHTML = UNKNOWN.length
    ? `<b class="bad">${UNKNOWN.length} 行没认出来</b>（原样列出，自己看）<pre>${esc(UNKNOWN.join('\n'))}</pre>`
    : '';
}

/* ============ 事件 ============ */

function mount(){
  const src = document.getElementById('src');
  src.addEventListener('input', () => { if (!WRITING) readSource(); });

  document.getElementById('form').addEventListener('input', ev => {
    const el = ev.target.closest('[data-path]');
    if (!el) return;
    const path = el.getAttribute('data-path');
    DOC[path] = el.type === 'checkbox' ? String(el.checked) : el.value;
    if (el.type === 'checkbox') el.parentNode.querySelector('span').textContent = String(el.checked);
    /* 只回写右边并重算校验，不重建左边的 DOM —— 否则正在打字的输入框会被换掉
       [EN] Only the right pane is rewritten; the left DOM stays so the field being typed into is
       not replaced under the cursor */
    writeSource();
    const info = classify(path);
    const row = el.closest('.row');
    if (info && info.spec && row){
      const e = check(info.spec, DOC[path]);
      row.classList.toggle('bad', !!e);
      let node = row.querySelector('.err');
      if (e && !node){ node = document.createElement('div'); node.className = 'err'; row.appendChild(node); }
      if (node) node.textContent = e || '';
      if (!e && node) node.remove();
    }
    const nerr = document.querySelectorAll('#form .row.bad').length;
    document.getElementById('nerr').innerHTML = nerr ? `<b class="bad">${nerr} 处不合法</b>` : '校验通过';
  });

  document.getElementById('form').addEventListener('click', ev => {
    const d = ev.target.closest('[data-del]');
    if (!d) return;
    delete DOC[d.getAttribute('data-del')];
    writeSource();
    renderForm();
  });

  document.getElementById('addable').addEventListener('click', ev => {
    const a = ev.target.closest('[data-add]');
    if (!a) return;
    const p = a.getAttribute('data-add');
    DOC[p] = INDEX[p].spec.default || '';
    writeSource();
    renderForm();
  });

  for (const bucket of ['plain','secret']){
    document.getElementById('copy-' + bucket).addEventListener('click', async ev => {
      const text = document.getElementById('out-' + bucket).textContent;
      try { await navigator.clipboard.writeText(text); ev.target.textContent = '已复制'; }
      catch (e) { ev.target.textContent = '复制失败，手动选'; }
      setTimeout(() => { ev.target.textContent = '复制'; }, 1500);
    });
  }
  readSource();
}

document.addEventListener('DOMContentLoaded', mount);
"""

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>流水线配置编辑器</title>
<style>%(css)s</style>
</head>
<body>
<header>
  <h1>流水线配置编辑器</h1>
  <div class="sub">右边贴 YAML，左边按它的内容自动摆出表单；两边实时双向同步　每一项属于
    <code>vars.PIPELINE_CONFIG</code> 还是 <code>secrets.PIPELINE_SECRETS</code> 由
    <code>scripts/config/schema.yaml</code> 判定，逐项标出来 —— 共 %(nplain)d 个明文键、%(nsecret)d 个密钥键</div>
</header>
<div class="warn">
  <b>这张页面不联网</b>　没有任何外部引用，也没有 fetch、表单提交或上报：贴进去的值只在当前标签页里，关掉就没了
  <br><b>密钥文档不要提交进仓库</b>　它只该贴进 GitHub 的 Secrets 页面，或者用
  <code>gh secret set</code> 从本地文件喂进去
</div>
<div class="wrap">
  <div class="col">
    <div class="bar"><strong>配置项</strong><span class="grow"></span><span id="nerr"></span></div>
    <div class="panel"><div id="form"></div><div id="addable"></div></div>
  </div>
  <div class="col">
    <div class="bar"><strong>YAML</strong><span class="grow"></span>
      <span class="sub" style="font-size:12px">贴进来即可，改哪边另一边跟着动</span></div>
    <textarea id="src" spellcheck="false" placeholder="把手上那份 PIPELINE_CONFIG 或 PIPELINE_SECRETS 贴在这里
两份混在一起也行，下面会按归属拆开

service:
  binary_name: my-service
kube:
  namespace: business
  kubeconfig: |
    apiVersion: v1
    ..."></textarea>
    <div id="unknown"></div>

    <div class="bar" style="margin-top:14px"><strong>vars.PIPELINE_CONFIG</strong>
      <span class="grow"></span><span id="cnt-plain"></span>
      <button id="copy-plain">复制</button></div>
    <div class="panel"><pre id="out-plain"></pre></div>

    <div class="bar"><strong>secrets.PIPELINE_SECRETS</strong>
      <span class="grow"></span><span id="cnt-secret"></span>
      <button id="copy-secret">复制</button></div>
    <div class="panel"><pre id="out-secret"></pre></div>
  </div>
</div>
<script id="schema" type="application/json">%(schema)s</script>
<script>%(js)s</script>
</body>
</html>
"""



def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="config-page/index.html")
    args = ap.parse_args(argv)

    model = build_model()
    nplain = sum(len(s["keys"]) for s in model["plain"])
    nsecret = sum(len(s["keys"]) for s in model["secret"])

    html = HTML % {"css": CSS, "js": JS, "schema": embed(model),
                   "nplain": nplain, "nsecret": nsecret}

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
