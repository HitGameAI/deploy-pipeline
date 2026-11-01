# GitHub App 创建与配置

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## 🔑 GitHub App 创建与配置

`APP_ID` + `APP_PRIVATE_KEY` 这两个 Secret 不是随手填的字符串，它们来自一个你自己注册的
GitHub App
本节把「从零注册到工作流跑通」这条路写完整，官方文档见
[注册 GitHub App](https://docs.github.com/zh/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)

### 为什么用 App 而不是 PAT

| 维度 | GitHub App | 个人访问令牌（PAT） |
|------|------------|---------------------|
| 令牌有效期 | 每次运行现签，1 小时自动失效 | 手动设定，常年有效甚至永不过期 |
| 授权范围 | 按仓库逐个安装，按权限项勾选 | 跟着人走，默认能碰到这个人所有仓库 |
| 人员流动 | 与个人账号无关，离职不影响 | 发证人离职 / 改密码即全线崩 |
| 审计 | 操作记录显示为 App 身份，可追溯 | 只能看到个人账号，分不清是人还是流水线 |
| 成员席位 | 不占用组织席位 | 机器人账号要占席位（私有仓库要付费）|

工作流里用的是 [`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)：
它拿 App ID 与私钥换一个**短时效的安装令牌**，用完即弃

### 这个仓库用 App 做什么

| 用途 | 哪个工作流 | 需要的权限 |
|------|-----------|-----------|
| 拉取私有 Go 模块（`go mod download`）| `code-quality.yml`、`backend-build-binary.yml` | Contents: **Read-only** |
| 下游仓库升级 proto 引用并推回（`update-proto-mod`）| 业务仓库调用该 Action | Contents: **Read and write** |
| 机器人评论 PR（pb 同步报告、变更摘要）| `pr-opened-notify.yml` 及业务仓库的生成流程 | Pull requests: **Read and write** |
| 上游发版后通知下游（跨仓库 `repository_dispatch`）| `go-trigger-downstream.yml` | Contents: **Read and write**（dispatch 走写权限）|
| 下游升 `go.mod` 并推回 / 闸门没过时开 PR | `go-bump-upstream.yml` | Contents + Pull requests: **Read and write** |
| 给各库下发 App 凭证（写别人仓库的 Actions secret）| `golib-bootstrap.yml` | Secrets: **Read and write** |
| 推导依赖图（枚举仓库、读各库 `go.mod`）| `go-trigger-downstream.yml`、`golib-bootstrap.yml` | Metadata + Contents: **Read-only** 即可 |

只做前一件事时，权限给到 `Contents: Read-only` 就够了
把三件事放在同一个 App 上没问题 —— 权限是按安装仓库生效的，不会让只读场景拿到写权限

### 令牌是怎么换出来的

弄清这条链路，才知道为什么「App 建好了但还是 403」——问题几乎总出在第 3 步的安装关系上

```mermaid
sequenceDiagram
    autonumber
    participant WF as 业务仓库 Workflow
    participant ACT as actions/create-github-app-token
    participant GH as GitHub API
    participant REPO as 私有 proto 仓库
    participant GO as go mod download

    WF->>ACT: 传入 APP_ID + APP_PRIVATE_KEY + owner
    ACT->>GH: 用私钥签 JWT，查该 App 在 owner 下的安装记录
    GH-->>ACT: 返回 installation_id
    Note over GH,ACT: 这一步要求 App 已安装到该组织<br/>没装则查不到安装记录，直接失败
    ACT->>GH: 用 installation_id 申请安装令牌
    GH-->>ACT: 返回短时效令牌，1 小时过期
    ACT-->>WF: 输出 steps.app_token.outputs.token
    WF->>WF: git config url.insteadOf 注入令牌
    WF->>GO: go mod download
    GO->>REPO: https 拉取私有模块
    REPO-->>GO: 返回模块内容
    Note over REPO,GO: 令牌的权限是 App 在「这个仓库」上的权限<br/>App 没装到 proto 仓库时这里 404，而不是 401
```

### 注册步骤

1. **进入注册页**
   - 组织用（推荐）：`https://github.com/organizations/<你的组织>/settings/apps/new`
   - 个人用：Settings → Developer settings → GitHub Apps → New GitHub App

2. **填基本信息**

   | 字段 | 填什么 | 说明 |
   |------|--------|------|
   | GitHub App name | 如 `<org>-ops-cli` | 全局唯一，重名会被拒 |
   | Homepage URL | 组织或仓库地址即可 | 必填但不影响功能 |
   | Webhook → Active | **取消勾选** | 我们只用它换令牌，不接收事件；不取消会一直给你推送失败告警 |

3. **勾权限**（Permissions → Repository permissions）

   | 权限项 | 给什么 | 为什么 |
   |--------|--------|--------|
   | Contents | Read-only（需推送时 Read and write）| 拉私有模块 / 推送 go.mod 变更 |
   | Pull requests | Read and write（只拉模块可不给）| 机器人评论 PR |
   | Metadata | Read-only（勾了上面会自动带上）| GitHub 强制要求 |

   其余一律不动
   多给的权限不会让流程更顺，只会扩大泄露后的影响面

4. **选安装范围**：Where can this GitHub App be installed → **Only on this account**

5. **创建并记下 App ID**：创建后页面顶部的 `App ID`（纯数字）就是 `APP_ID`

6. **生成私钥**：同一页 → Private keys → Generate a private key，浏览器会下载一个
   `.pem` 文件
   **这个文件只能下载这一次**，关掉页面就拿不回来了（可以再生成新的）

7. **安装到仓库**：左侧 Install App → 选择账号/组织 → 选 `Only select repositories`，
   勾上需要被访问的仓库（比如 proto 仓库 + 所有要拉它的业务仓库）

   > 漏装是最常见的失败原因：App 创建好了但没装到目标仓库，换令牌会成功，
   > 但用它 `git clone` 私有仓库仍然 404

8. **写进 Secrets**（仓库 Settings → Secrets and variables → Actions）

   | Secret | 值 |
   |--------|----|
   | `APP_ID` | 第 5 步那串数字 |
   | `APP_PRIVATE_KEY` | `.pem` 文件的**全部内容**，含 `-----BEGIN RSA PRIVATE KEY-----` 和 `-----END` 两行 |

   粘贴私钥时保持原始换行，不要手动删空行或加缩进 —— 粘成一行会报
   `error:0909006C:PEM routines:get_name:no start line`

   组织多仓库复用时，用 **Organization secrets** 配一次，再按仓库授权，比每个仓库各存一份好维护

9. **在工作流里启用**：把认证方式切到 `app-token` 并把两个 Secret 透传给可复用工作流

   ```yaml
   jobs:
     code-quality:
       uses: kamalyes/deploy-pipeline/.github/workflows/code-quality.yml@master
       with:
         goprivate: github.com/<你的组织>/*      # 不填则不会触发私有模块认证
         go-auth-method: app-token
       secrets:
         APP_ID: ${{ secrets.APP_ID }}
         APP_PRIVATE_KEY: ${{ secrets.APP_PRIVATE_KEY }}
   ```

   也可以把 `go-auth-method` 配成仓库 Variable `GO_AUTH_METHOD=app-token`，省掉每个
   工作流都写一遍

### 常见错误对照

| 报错 / 现象 | 原因 | 处理 |
|------------|------|------|
| `Input required and not supplied: app-id` | Secret 没配，或可复用工作流没透传 `secrets:` | 在 `secrets:` 段里显式映射；不要指望 `secrets: inherit`，它[跨账号时失效](#inherit-cross-account) |
| `PEM routines:get_name:no start line` | 私钥粘成了一行 / 丢了 BEGIN-END 行 | 重新粘贴完整 `.pem` 内容 |
| 换令牌成功但 `go mod download` 仍 403/404 | App 没安装到被依赖的那个私有仓库 | Install App 里补勾该仓库 |
| `GOPRIVATE` 没生效，走了公网代理 | `goprivate` 入参为空 | 填 `github.com/<org>/*` 或配 `vars.GOPRIVATE` |
| 推送被拒 `403 Resource not accessible by integration` | Contents 只给了 Read-only | 改成 Read and write 并重新安装 |
| App 一直收到 webhook 投递失败邮件 | 注册时没取消 Webhook → Active | 回 App 设置取消勾选 |

---
