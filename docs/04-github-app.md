# GitHub App 创建与配置

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## 🔑 GitHub App 创建与配置

流水线环境变量 `APP_ID` + `APP_PRIVATE_KEY` 来自一个你自己注册的 GitHub App
新接入将它们保存在调用方 `secrets.PIPELINE_SECRETS` 的 `go.app_id` / `go.app_private_key`，
解析器会导出对应环境变量，不必再创建两份独立 Secret
本仓库仍兼容独立 APP_ID / APP_PRIVATE_KEY，聚合 YAML 优先；下面的新接入步骤统一使用聚合 Secret
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
| 拉取集中配置仓库 | `backend-deploy-k8s.yml` | Contents: **Read-only** |
| 拉取私有 Go 模块（`go mod download`）| `code-quality.yml`、`backend-build-binary.yml` | Contents: **Read-only** |
| 下游仓库升级 proto 引用并推回（`update-proto-mod`）| 业务仓库调用该 Action | Contents: **Read and write** |
| 机器人评论 PR（pb 同步报告、变更摘要）| `pr-opened-notify.yml` 及业务仓库的生成流程 | Pull requests: **Read and write** |
| 上游发版后通知下游（跨仓库 `repository_dispatch`）| `go-trigger-downstream.yml` | Contents: **Read and write**（dispatch 走写权限）|
| 下游升 `go.mod` 并推回 / 闸门没过时开 PR | `go-bump-upstream.yml` | Contents + Pull requests: **Read and write** |
| 给各库下发 App 凭证（写别人仓库的 Actions secret）| `golib-bootstrap.yml` | Secrets: **Read and write** |
| 推导依赖图（枚举仓库、读各库 `go.mod`）| `go-trigger-downstream.yml`、`golib-bootstrap.yml` | Metadata + Contents: **Read-only** 即可 |

只拉私有模块或配置仓库时，Contents Read-only 就够了，Metadata 是自动授予的基础只读权限
推代码、发跨仓库 repository_dispatch 或开 PR 才需要表中对应写权限
同一 App 可以承担多项功能，但生成 token 默认可能继承安装的全部权限；
只读用途应显式申请 `permission-contents: read`，例如配置仓库的 token 步骤

### 仓库已经勾选，为什么仍然拉不到

安装页的两块信息要分别看：

| 安装页显示 | 说明 | 能否代替 Contents |
|---|---|---|
| Repository access 中有 service-configs | 这个 App 安装已覆盖配置仓库，不用再添加 | 不能 |
| Read access to metadata | 能读取仓库基本信息 | 不能 |
| Read and write access to actions, pull requests, and workflows | 有 Actions、PR 与 workflow 文件相关权限 | 不能 |
| Read access to contents 或权限列表中包含 contents | 有读仓库文件所需的权限 | 是 |

**如果仓库已经在清单里，但 Permissions 没有 contents，下一步改权限，不要反复修改清单**

1. 打开该 App 的注册设置页：组织 Settings → Developer settings → GitHub Apps → 对应 App → Permissions & events
2. Repository permissions → **Contents → Read-only**，保存
3. 回到组织 Settings → GitHub Apps / Installed GitHub Apps，找到同一个 App，批准待处理的权限变更
4. 核对安装页的 Permissions 已包含 contents，再重新触发部署

配置仓库里无需再存 App ID / 私钥，也无需开启 Actions Access 来解决 git clone 的认证
需要检查的是调用方 PIPELINE_SECRETS.go 指向的 App 与上述安装是否为同一个
GitHub 对 HTTP Git 访问要求 Contents，见[官方权限说明](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app#choosing-permissions-for-git-access)
新增权限要由已有安装批准才会生效，见[官方变更规则](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration#changing-the-permissions-of-a-github-app)

### 哪些权限默认不用开

只拉配置仓库或私有模块时，除了 Metadata Read-only 和 Contents Read-only，其余保持 No access
不需要 Actions、Pull requests、Workflows、Secrets、Administration 或 Organization permissions
具体用途的追加权限见上表；自动下发工作流文件还需要 Workflows 写权限
这些是 App 后台权限，不同于业务 workflow 中控制 GITHUB_TOKEN 的 permissions 字段

### 令牌是怎么换出来的

弄清这条链路后，按失败步骤区分安装范围、文件读取权限和实际凭据，避免只凭 403 / 404 猜原因

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

   > 漏装目标仓库会导致安装查询或后续访问失败；是否能换到令牌取决于申请的仓库范围
   > 当前配置仓库步骤明确指定目标仓库，未安装通常会先在换令牌步骤失败

   > 这一步与上面第 3 步是两件事，缺哪一件报错都一样：
   > 这里决定 App 看得见哪些仓库，第 3 步的 Contents 决定看得见的能不能读
   > 只装仓库不给 Contents 时，换令牌同样成功，发出来的令牌只有 Metadata，
   > `git clone` 还是 404 —— 与漏装一字不差，所以先按仓库清单查过一遍、
   > 确认仓库在清单里之后，下一个要看的就是 Contents

8. **写进业务仓库的聚合 Secret**（Settings → Secrets and variables → Actions → Secrets → `PIPELINE_SECRETS`）

   | YAML 路径 | 值 |
   |--------|----|
   | `go.app_id` | 第 5 步那串数字，作为字符串保存 |
   | `go.app_private_key` | `.pem` 文件的**全部内容**，含 BEGIN / END 行，用 YAML 的 `|` 保留换行 |

   ```yaml
   go:
     app_id: '123456'
     app_private_key: |
       -----BEGIN RSA PRIVATE KEY-----
       <完整 PEM 私钥内容>
       -----END RSA PRIVATE KEY-----
   ```

   粘贴私钥时保持原始换行，不要手动删空行或加缩进 —— 粘成一行会报
   `error:0909006C:PEM routines:get_name:no start line`

   组织内使用相同完整密钥配置时，可用 Organization secret `PIPELINE_SECRETS`，授权给调用方仓库
   仓库或 Environment 的同名聚合 Secret 会整体覆盖它，不会按 YAML 路径合并

9. **在工作流里启用**：在 `PIPELINE_CONFIG.builder` 中设 `go_auth_method: app-token` 和私有模块前缀 `goprivate`，并显式传递聚合 Secret

   ```yaml
   jobs:
     code-quality:
       uses: HitGameAI/deploy-pipeline/.github/workflows/code-quality.yml@0c70ed9ef8fd8b81bad3423612fdb99be418ef2e
       secrets:
         PIPELINE_SECRETS: ${{ secrets.PIPELINE_SECRETS }}
   ```

   `builder.go_auth_method` 与 `builder.goprivate` 都写在同一份 `PIPELINE_CONFIG`，不再增加独立 Variable

### 常见错误对照

| 报错 / 现象 | 原因 | 处理 |
|------------|------|------|
| `Input required and not supplied: app-id` | 聚合 Secret 缺少 go.app_id，或未传给复用工作流 | 检查 PIPELINE_SECRETS.go，并显式传递 PIPELINE_SECRETS，见[配置说明](03-configuration.md#inherit-cross-account) |
| `PEM routines:get_name:no start line` | 私钥粘成了一行 / 丢了 BEGIN-END 行 | 重新粘贴完整 `.pem` 内容 |
| 换令牌成功但 `go mod download` 仍 403/404 | App 没安装到被依赖的那个私有仓库 | Install App 里补勾该仓库 |
| 换令牌成功但 `git clone` 报 `Repository not found`，而仓库确实在 Install App 的清单里 | 仓库访问给了、`Contents` 权限没给，令牌只有 Metadata | App 设置 → Permissions → Repository permissions → `Contents` = Read-only，保存后到组织里批准这次权限变更 |
| 改了 App 权限但行为没变 | 权限变更对已有安装要批准才生效 | 组织 Settings → GitHub Apps → 该 App → 批准待处理的权限请求 |
| `GOPRIVATE` 没生效，走了公网代理 | `goprivate` 入参为空 | 填 `github.com/<org>/*` 或配 `vars.GOPRIVATE` |
| 推送被拒 `403 Resource not accessible by integration` | Contents 只给了 Read-only | 改成 Read and write 并重新安装 |
| App 一直收到 webhook 投递失败邮件 | 注册时没取消 Webhook → Active | 回 App 设置取消勾选 |

---
