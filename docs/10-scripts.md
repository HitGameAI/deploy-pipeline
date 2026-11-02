# 公共脚本

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## 📜 公共脚本

### resolve-config.py

聚合配置解析器，[resolve-config action](#resolve-config) 调的就是它
入参走环境变量，
所以在 CI 里不用传参；本地则有三个子命令可以直接用：

```bash
# 校验一份总表配置（键名、类型、密钥误放明文都会报）
python3 scripts/resolve-config.py --check my-config.yaml

# 校验一份分节配置：内容不带节名，要指明是哪一节
python3 scripts/resolve-config.py --check kube.yaml --section kube

# 校验聚合密钥（按 secrets 侧的 schema 校验，值只打印长度与指纹）
python3 scripts/resolve-config.py --check my-secrets.yaml --secrets

# 打印带注释的完整示例，改完直接贴进 GitHub 变量
python3 scripts/resolve-config.py --print-example [节名...]
```

配置项的唯一出处是 `scripts/config/schema.yaml`：加一个配置项只改那张表，
解析、校验、默认值、README 里的清单都跟着走，不用再去各个 workflow 的 `env:` 块里加行
依赖 PyYAML（GitHub 托管的 ubuntu runner 自带；自建 runner 由 action 自动装）

### service-vars.sh

[resolve-service-vars](#resolve-service-vars) 调的就是它，入参全走环境变量
（大部分由 resolve-config 从聚合配置解析好写进 `GITHUB_ENV`），结果写 `$GITHUB_OUTPUT`
算四件事：

1. **产物与镜像**：名字、版本（tag 推送取 tag 名，否则 `<分支>-<短 commit>`）、镜像名与基址
2. **部署决策**：push / workflow_dispatch / tag 三种触发各有规则；环境不在
   `service.allowed_envs` 白名单里就只跑检查，推 tag 则只构建不部署
3. **配置与启动**：ConfigMap 名、配置文件路径（不存在直接失败，不静默跳过）、写进镜像 CMD 的启动参数
4. **容器 env 列表**：明文变量来自 `env` 配置节走 `value:`；密钥名从 go-zero 的
   `,env=XXX` struct tag 与服务配置里 `*Env:` 结尾的键自动抽出来，走
   `valueFrom.secretKeyRef`，值只存在集群的 Secret 里
   两边撞名时以密钥为准

密钥清单是**自动抽的而不是手维护的**：加了新的密钥配置，下次部署自动带上，不会漏

### sync-fork.sh

把本仓库整份同步到同一项目在另一个组织下的那一份，只换自引用的组织前缀
两边组织名都从各自的 `git remote get-url origin` 推出来，脚本里不写死组织名，
所以两份副本里是同一个脚本，反向同步只是把参数换成对面的路径

```bash
bash scripts/sync-fork.sh <对面仓库路径>            # 覆盖与新增
bash scripts/sync-fork.sh <对面仓库路径> --check     # 只报告会改哪些文件
bash scripts/sync-fork.sh <对面仓库路径> --force     # 整份覆盖，对面多出来的文件一并删掉
```

只改工作区文件、不碰 git 历史；跑完校验对面不再残留来源组织名，残留就非零退出
（二进制文件和没列进扩展名白名单的文件不换前缀，报出来的就是这类）
背景与完整说明见[顶部「同一份内容，两个组织」](#deploy-pipeline)

### branch-ops.sh

批量切分支与 dev 回收的实现，`branch-cut.yml` / `branch-recycle.yml` 共用
全部通过 `gh api` 操作远端，不需要本地 git 工作区，所以 workflow 里只取脚本不 checkout 代码
参数与安全设计见[分支管理工作流](#分支管理工作流)

### backend/build-linux.sh

Go 二进制构建脚本

```bash
bash scripts/backend/build-linux.sh \
  --version "v1.0.0" \
  --build-time "2026-01-01_00:00:00" \
  --git-commit "abc12356" \
  --binary-name "your-service" \
  --output-dir "deployments" \
  --os linux --arch amd64 \
  --upx-compress false
```

### frontend/build-frontend.sh

前端构建脚本，支持多种包管理器

```bash
bash scripts/frontend/build-frontend.sh \
  --version "v1.0.0" \
  --build-time "2026-01-01_00:00:00" \
  --git-commit "abc12356" \
  --node-version "20" \
  --package-manager "pnpm" \
  --package-registry "https://registry.npmmirror.com" \
  --output-dir "dist"
```

### common.sh

被其他脚本 `source` 的公共函数库，自己不单独执行
所有输出都 `tee` 进 `${LOG_FILE}`，
所以构建日志既能在 Actions 里实时看到，也会落到文件里随 artifact 一起上传

```bash
source "$(dirname "$0")/../common.sh"     # 相对路径按调用方位置调整
```

| 函数 | 用法 | 说明 |
|------|------|------|
| `log_info` | `log_info "正在构建"` | ℹ️ 普通信息 |
| `log_success` | `log_success "构建完成"` | ✅ 成功 |
| `log_warn` | `log_warn "跳过 UPX 压缩"` | ⚠️ 警告，不中断 |
| `log_error` | `log_error "找不到 go"` | ❌ 错误，需调用方自己决定是否 exit |
| `log_section` | `log_section "环境准备"` | 带分隔线的大标题，用于划分阶段 |
| `log_step` | `log_step "下载依赖"` | ▶️ 阶段内的小步骤 |
| `run_command` | `run_command "go build ./..." "编译"` | 执行命令并打印描述与结果，失败返回非零 |
| `check_env` | `check_env VERSION GIT_COMMIT` | 批量校验环境变量非空，缺哪个报哪个 |
| `ensure_directory` | `ensure_directory deployments` | 目录不存在则创建 |
| `show_directory_tree` | `show_directory_tree deployments 2` | 打印目录结构，第二个参数是深度（默认 2）|

写新脚本时优先用这些函数而不是裸 `echo`：日志格式统一，出问题时在几百行 Actions
输出里能一眼找到阶段边界

### update-proto-mod.sh

下游仓库升级 proto 模块引用的核心脚本，由
[`update-proto-mod`](#proto-模块引用升级update-proto-mod) Action 调用，也可本地手动跑

```bash
bash scripts/update-proto-mod.sh <目标分支> <模块名> <模块版本>
# 例：bash scripts/update-proto-mod.sh master demo-share-proto master
```

| 参数 | 必填 | 说明 |
|------|------|------|
| 1. 目标分支 | ✅ | 升级后推回哪个分支 |
| 2. 模块名 | ✅ | proto 仓库名，组织前缀由 `GITHUB_REPOSITORY` 推导 |
| 3. 模块版本 | 否（默认 `master`）| 可填分支、tag 或 commit hash |

| 环境变量 | 说明 |
|---------|------|
| `PROTO_READ_TOKEN` | 能读 proto 仓库的令牌（GitHub App token）|
| `PUSH_TOKEN` | 能推当前仓库的令牌（一般用 `GITHUB_TOKEN`）|
| `GITHUB_REPOSITORY` | `owner/repo`，Actions 自动注入；本地跑要手动导出 |

执行流程：配置 `url.insteadOf` 让 go 带令牌拉私有库 → `go get <module>@<ref>` →
`go mod tidy` → 有 diff 才提交推送（无变化直接退出，不会产生空提交）

> 本地手动跑之前先确认 `GOPRIVATE` 覆盖了该组织，否则 go 会走公共代理拿不到私有模块

### notify.sh

通用通知脚本，支持飞书 / 企业微信 / Telegram（单通道失败不影响其他通道）

```bash
export NOTIFICATION_PROVIDER=feishu  # feishu | wecom | telegram | all | none
export NOTIFICATION_TYPE=deployment  # deployment | rollback | custom
export STATUS=success                # success | failure | warning | info
export ENVIRONMENT=dev
export BINARY_NAME=your-service
export WORKFLOW_URL=https://github.com/...
export FEISHU_WEBHOOK_URL=https://open.feishu.cn/...
bash scripts/notify.sh

# 企业微信
export NOTIFICATION_PROVIDER=wecom
export WECOM_WEBHOOK_URL='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'
# export WECOM_WEBHOOK_KEY=xxx                   # 与上面二选一
# export WECOM_MENTIONED_LIST=zhangsan,lisi      # 可选，内联 <@userid>
# export WECOM_MENTIONED_MOBILE_LIST=@all        # 可选，额外补发 1 条 text
bash scripts/notify.sh

# custom 类型：自定义标题与正文（飞书 lark_md / 企业微信 markdown / Telegram Markdown，支持多行）
export NOTIFICATION_TYPE=custom
export CUSTOM_TITLE="📋 自定义标题"
export CUSTOM_MESSAGE="**变更内容**
- PR #12 xxx
- PR #13 yyy"
bash scripts/notify.sh
```

---

### send-email.py

邮件通知的渲染与投递，由 notify.sh 在 `NOTIFICATION_PROVIDER` 命中 email 时调用，
平时不用直接跑 模板在 `scripts/templates/email/`，`_base.html` 是外壳，
`deployment.html` / `custom.html` 是两种正文

值全部从环境变量读，与 notify.sh 同一套名字 配置分两处:地址、端口、收件人在
`vars.PIPELINE_CONFIG` 的 notify 节，账号与密码在 `secrets.PIPELINE_SECRETS`

```bash
# 看模板效果不需要 SMTP：各场景渲染成本地 HTML，浏览器直接打开
python3 scripts/send-email.py --preview email-preview
open email-preview/index.html
```

`--preview` 走的是和真实发送完全相同的渲染路径,只是不碰 SMTP,所以它能看出来的问题
真实发送也会有 退出码:0 成功、2 配置不全、其它为发送失败

渲染产物默认落在仓库根的 `email-preview/`,已被 `.gitignore` 排除;`build` 分支上那份
提交进来的产物在 `docs/email-preview/` —— `.gitignore` 里写的是带前导斜杠的
`/email-preview/`,只挡仓库根那一个,就是为了让 docs 下这份能提交

### make-config.py

接一个服务要配几十项，一个个在 Settings 里点既慢又容易漏 这个脚本收一份
`KEY=VALUE` 清单(就是平时 `.env` 的样子)，按 `scripts/config/schema.yaml` 自动分类并
生成那两份聚合文档

```bash
python3 scripts/make-config.py dump.env -o out/
python3 scripts/make-config.py dump.env -o out/ --map K8S_NAMESPACE=NAMESPACE
python3 scripts/make-config.py dump.env -o out/ --apply your-org/your-service:dev
```

分类规则

| 输入的名字 | 去向 |
|---|---|
| 命中密钥节的 env 名(`KUBECONFIG`、`APP_PRIVATE_KEY` …) | `PIPELINE_SECRETS` 的对应路径 |
| 命中明文节的 env 名(`NAMESPACE`、`DOCKER_REGISTRY` …) | `PIPELINE_CONFIG` 的对应路径,并提示它不该当 secret 配 |
| 都没命中 | `PIPELINE_SECRETS` 的 `app` 节,即业务密钥,键名就是容器里的变量名 |

输入格式:`KEY=value`、`export KEY=value`、`KEY="value"`(成对引号会去掉)、
`KEY@=路径`(值从文件读,给 kubeconfig 与 PEM 私钥用)、`#` 注释与空行跳过

几处刻意的设计

**值不经过终端** 默认写成两个 0600 的文件,标准输出只打名字、去向与长度
终端滚屏、CI 日志、shell 历史都会留痕,而密钥一旦出现在那里只能轮换、不能撤回

**多行值强制 `|` 块标量,且保留结尾换行** PyYAML 默认会给多行值选折叠的单引号形式,
PEM 私钥会变成一堆被空行分隔的片段 —— 能解析回去,但人看不出哪儿是真换行,手工改一下就坏
结尾换行也是格式的一部分:去掉之后 ssh 与 openssl 都判无效,而现象只是部署时「认证失败」

**落盘前做往返校验** 生成的 YAML 读回来必须和输入逐字节相同,不一致就拒绝落盘
密钥最怕的不是报错而是悄悄变形

**认得出被误放的配置项** 名字去掉 `K8S_` / `CLEANUP_` 这类常见前缀后能命中明文配置项的,
会单独提示并给出 `--map` 的写法 业务密钥会被同步成集群 Secret,一个配置项误落进去,
它既不以配置的身份生效(名字对不上 schema),又以密钥的身份进了集群,两头都不报错

注意这个脚本依赖 PyYAML,所以**不能用 `python3 -I` 跑** —— `-I` 会屏蔽 user site-packages
