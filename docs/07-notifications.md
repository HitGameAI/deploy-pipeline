# 通知与发布单

> 本文是 [deploy-pipeline](../README.md) 文档的一部分。

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
    uses: kamalyes/deploy-pipeline/.github/workflows/pr-opened-notify.yml@master
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
    uses: kamalyes/deploy-pipeline/.github/workflows/pr-merged-notify.yml@master
    with:
      provider: wecom   # 可选，默认 feishu
    secrets: inherit
```

> 所选渠道的 webhook 未配置时静默跳过，不会把 job 判红。

### 通用通知 Action

任意 workflow 中发送自定义通知（标题 + 多行正文）：

```yaml
- name: Notify
  uses: kamalyes/deploy-pipeline/.github/actions/notify@master
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
（只想存 key 时用 `WECOM_WEBHOOK_KEY`，脚本会自行拼 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=`；两者都给时 URL 优先）。

```yaml
# 部署通知
with:
  notification-provider: 'wecom'
secrets:
  WECOM_WEBHOOK_URL: ${{ secrets.WECOM_WEBHOOK_URL }}
```

消息用群机器人的 `markdown` 类型。平台侧的硬限制与本脚本的处理：

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

`WECOM_MENTIONED_MOBILE_LIST` 补发的那条 text 只含标题；它失败不影响主通知的成败判定。

> 没选企业微信的 `markdown_v2`：v2 支持表格和代码块，但不支持字体色，也不支持 `<@userid>` 扩展语法，拿不到与飞书卡片一致的状态配色。

### proto 模块引用升级（update-proto-mod）

proto 仓库 master 更新后，业务仓库一键升级 `go.mod` 中 proto 模块引用并自动提 PR：

```yaml
- name: Update proto mod
  uses: kamalyes/deploy-pipeline/.github/actions/update-proto-mod@master
  with:
    token: ${{ secrets.GH_PAT }}
    proto-repo: 'your-org/demo-share-proto'
    proto-module: 'github.com/your-org/demo-share-proto'
```

---
