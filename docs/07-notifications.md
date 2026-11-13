# 通知与发布单

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## 📣 通知与发布单

### 发布单（Release Note）

**无需单独接入**：调用方 pipeline 开启 tag 发布模式（push `v*` / `prod-*` tag → 仅构建推送镜像、跳过 K8s 部署），构建成功后 `release-note` job 自动运行：

- 自动定位上一 tag，收集区间内**全部 PR（含标题/作者/链接）与直接提交**
- 幂等创建/更新 GitHub Release 发布单（版本、镜像、变更统计、发布人、时间）
- 同步写入 Actions Summary，无需翻日志
- **零 secrets 依赖**（仅用 `GITHUB_TOKEN`），任何仓库接入即用

**调用方需要**：

| 项 | 说明 |
|---|---|
| 触发 | `on.push.tags: ['v*', 'prod-*']`（参考 example-service 的 `pipeline.yml` 发布模式决策块） |
| 权限 | workflow 级声明 `permissions: contents: write`（创建 Release）+ `pull-requests: read`（收集 PR 明细） |
| 镜像 tag | 发布模式下镜像 tag = git tag 名（如 `v0.0.1`），且默认被镜像清理豁免 |

### PR 通知

PR 开启推送**变更文件摘要**、PR 合并推送**合并通知**，渠道由 `provider` 入参选择（默认 `feishu`）：

```yaml
# .github/workflows/pr-opened-notify.yml（PR 开启摘要）
on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  notify:
    if: github.event_name == 'pull_request'
    uses: HitGameAI/deploy-pipeline/.github/workflows/pr-opened-notify.yml@d3798f49355acd52c79c0fc85fc74b31ece20f6d
    with:
      provider: wecom   # 可选，默认 feishu；feishu | wecom | telegram | all
    secrets: inherit    # 需对应渠道的 webhook secret
```

```yaml
# .github/workflows/pr-merged-notify.yml（PR 合并通知）
on:
  pull_request:
    types: [closed]

jobs:
  notify:
    if: github.event.pull_request.merged == true
    uses: HitGameAI/deploy-pipeline/.github/workflows/pr-merged-notify.yml@d3798f49355acd52c79c0fc85fc74b31ece20f6d
    with:
      provider: wecom   # 可选，默认 feishu
    secrets: inherit
```

> 所选渠道的 webhook 未配置时静默跳过，不会把 job 判红

### 通用通知 Action

任意 workflow 中发送自定义通知（标题 + 多行正文）：

```yaml
- name: Notify
  uses: HitGameAI/deploy-pipeline/.github/actions/notify@d3798f49355acd52c79c0fc85fc74b31ece20f6d
  with:
    provider: all        # feishu | wecom | telegram | all | none（all 只发已配置的渠道）
    status: success      # success | failure | warning | info
    title: '📋 自定义标题'
    message: |
      **变更内容**
      - xxx
  env:
    FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
    WECOM_WEBHOOK_URL: ${{ secrets.WECOM_WEBHOOK_URL }}
    TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
    TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

### 企业微信群机器人

群设置 → 群机器人 → 添加，复制 Webhook 地址存为 secret `WECOM_WEBHOOK_URL`
（只想存 key 时用 `WECOM_WEBHOOK_KEY`，脚本会自行拼 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=`；两者都给时 URL 优先）

```yaml
# 部署通知
with:
  notification-provider: 'wecom'
secrets:
  WECOM_WEBHOOK_URL: ${{ secrets.WECOM_WEBHOOK_URL }}
```

消息用群机器人的 `markdown` 类型
平台侧的硬限制与本脚本的处理：

| 限制 | 说明 | 脚本处理 |
|------|------|----------|
| 正文 ≤ 4096 字节 | 超限整条被拒收 | 按 UTF-8 字节裁剪并标注"内容过长已截断"，不会截出非法字符 |
| 频率 20 条/分钟 | 超限返回 `errcode: 45009` | 每次通知 1 条消息（配了手机号 @ 时 2 条） |
| 字体色只有 3 种 | `info`(绿) / `comment`(灰) / `warning`(橙红) | 从飞书色值映射：green→info、red/yellow→warning、其他→comment |
| `markdown` 不支持 @all、手机号 @ | 仅 `text` 类型支持 | 见下 |

**@ 成员**（可选，两个变量可同时用）：

```yaml
env:
  WECOM_MENTIONED_LIST: 'zhangsan,lisi'          # userid，内联 <@userid>，不额外占配额
  WECOM_MENTIONED_MOBILE_LIST: '@all'            # 手机号或 @all，需 text 类型，额外补发 1 条
```

`WECOM_MENTIONED_MOBILE_LIST` 补发的那条 text 只含标题；它失败不影响主通知的成败判定

> 没选企业微信的 `markdown_v2`：v2 支持表格和代码块，但不支持字体色，也不支持 `<@userid>` 扩展语法，拿不到与飞书卡片一致的状态配色

### 邮件（SMTP）

飞书 / 企业微信 / Telegram 都依赖对应平台：机器人被封、webhook 被轮换、群被解散，通知就断了，而且断了往往没人发现
邮件不受这些影响，是唯一不依赖第三方平台可用性的渠道，适合作为重要环境的兜底

```yaml
# 部署通知
with:
  notification-provider: 'email'
secrets:
  PIPELINE_SECRETS: ${{ secrets.PIPELINE_SECRETS }}
```

**配置分两处**：地址、端口、收件人不是敏感信息，放明文的 `notify` 节；只有账号与密码进 secrets

```yaml
# vars.PIPELINE_CONFIG（或分节变量 NOTIFY_CONFIG）
notify:
  email_smtp_host: smtp.feishu.cn
  email_smtp_port: 465           # 465 走 SSL，587 走 STARTTLS，留空按端口推断
  email_from_name: CI 流水线      # 发件人显示名
  email_to: 'team@example.com,ops@example.com'
```

```yaml
# secrets.PIPELINE_SECRETS
notify:
  email_smtp_user: ci@example.com
  email_smtp_password: '<授权码>'
```

> **密码填「授权码」而不是登录密码**：飞书、QQ、163、Gmail 都要求为第三方客户端单独生成一串授权码，填登录密码会报认证失败
> 脚本在认证失败时会把这条提示一起打出来，不用去翻日志猜

常见邮箱的地址与端口：

| 邮箱 | SMTP 地址 | 端口 | 授权码位置 |
|------|-----------|------|------------|
| 飞书邮箱 | `smtp.feishu.cn` | 465（SSL） | 邮箱设置 → 客户端专用密码 |
| 阿里云邮件推送 | `smtpdm.aliyun.com` | 465（SSL） | 控制台 → 发信地址 → 设置 SMTP 密码 |
| 腾讯企业邮 | `smtp.exmail.qq.com` | 465（SSL） | 邮箱设置 → 客户端专用密码 |
| Gmail | `smtp.gmail.com` | 587（STARTTLS） | Google 账号 → 应用专用密码 |

**排版由模板决定**，模板在 `scripts/templates/email/`：

| 文件 | 场景 |
|------|------|
| `_base.html` | 共用外壳：状态色条、标题区、页脚、深色模式 |
| `deployment.html` | 部署 / 回滚，含环境徽章、明细表、失败原因块 |
| `custom.html` | 自定义通知，正文走 Markdown 子集（PR 通知用这个） |

明细行、失败原因块、按钮这些**要不要出现由 `send-email.py` 判断**，模板只管排版
条件逻辑不放模板里，是为了不在 HTML 里塞一套 if/each 模板语言

**环境标签与配色可覆盖**，内置 `prod` / `staging` / `test` / `uat` / `dev`，加别的环境名或改配色：

```yaml
notify:
  env_styles: 'staging=预发布|7a4bb5|f2ecfa,sea-01=新加坡|0d7d74|e4f4f2'
```

格式 `环境名=标签|主色|底色`，只写要改的、其余用内置值
色值可以不带 `#` —— 聚合配置是 YAML，不带引号的 `#` 会被当成注释起点，裸十六进制省掉这个坑
单项解析不了只告警跳过，不会因为一处手误把整封邮件拦下来

**改模板前先看效果**，不需要 SMTP：

```bash
python3 scripts/send-email.py --preview email-preview
open email-preview/index.html
```

四个场景（部署成功 / 部署失败 / 回滚成功 / PR 通知）各渲染一份 HTML 和一份纯文本回退版，汇总页一次点开看全部
走的是和真实发送同一条渲染路径，所以看到的就是收件箱里会收到的正文

`self-test.yml` 的 `email-templates` job 在每次 push 和 PR 时做同样的渲染，把结果当产物上传（保留 14 天），并对三类问题硬闸门：

| 检查 | 为什么是硬闸门 |
|------|----------------|
| 未替换的 `{{占位符}}` | 会直接出现在收件人眼前 |
| 残留的 HTML 注释 | 开发文档不该随邮件发出去，也白占体积 |
| `<!DOCTYPE` 不止一个 | 说明模板被重复注入 |

> 这三条都是实际踩过的：模板注释里把 `{{DETAIL_ROWS}}` 当文档写了两次，而渲染是全量替换，于是明细被注入三遍、正文从 7KB 涨到 38KB
> 修法是渲染前先剥掉 HTML 注释，顺带也解决了体积 —— Gmail 对超过 102KB 的正文会截断并折叠

**纯文本回退**：邮件按 `multipart/alternative` 发，同时带 HTML 和纯文本两版，纯文本不是从 HTML 剥标签而来，是用同一批数据单独生成的
另外带 `Auto-Submitted: auto-generated` 和 `X-Auto-Response-Suppress: All`，让收件方的自动回复器和规则知道别回信

### proto 模块引用升级（update-proto-mod）

proto 仓库 master 更新后，业务仓库一键升级 `go.mod` 中 proto 模块引用并自动提 PR：

```yaml
- name: Update proto mod
  uses: HitGameAI/deploy-pipeline/.github/actions/update-proto-mod@d3798f49355acd52c79c0fc85fc74b31ece20f6d
  with:
    token: ${{ secrets.GH_PAT }}
    proto-repo: 'your-org/demo-share-proto'
    proto-module: 'github.com/your-org/demo-share-proto'
```

---
