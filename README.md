# Deploy Pipeline

可复用的 GitHub Actions 工作流集合，支持 **Go 后端服务** 和 **前端应用** 的完整 CI/CD 流程及 Kubernetes 部署

> **同一份内容，两个组织** | One content, two organizations
>
> 同一套工作流在两个组织下各留一份：私有仓库默认不对其它仓库开放 `uses:` 引用
> （放开办法是 Settings → Actions → General → 底部 Access → "Accessible from repositories
> in the organization"，没放开时调用方会收到 `workflow was not found`），
> 所以调用方指向能访问的那一份
>
> 两份内容**完全相同**，唯一的差别是自引用里的组织前缀 ——
> `uses:` 的路径、`repository:` 的值，都指向各自所在的组织
> 文档里的示例也只带这个前缀，不带任何项目内部信息：内容本身就是通用的，
> 所以同步只是「换前缀」，**哪一份当源头都行**，不存在一份要脱敏、一份不用的问题
>
> 同步用 [scripts/sync-fork.sh](scripts/sync-fork.sh)，两边组织名都从各自的 git remote
> 推出来，脚本里不写死任何组织名，因此两份副本里是同一个脚本，反向同步只是把参数
> 换成对面的路径：
>
> ```bash
> # 第一次：把对面那份 clone 到本仓旁边
> git clone <对面仓库的 git 地址> ../deploy-pipeline-fork
>
> bash scripts/sync-fork.sh ../deploy-pipeline-fork --check    # 先看会改哪些文件
> bash scripts/sync-fork.sh ../deploy-pipeline-fork --force    # 整份覆盖，对面多出的文件一并删掉
> bash scripts/sync-fork.sh ../deploy-pipeline-fork            # 只覆盖与新增，不删
> ```
>
> 脚本只改工作区文件，不碰 git 历史：落盘后在对面仓库自己看 diff 再提交
> 换完前缀会校验对面不再残留来源组织名，残留就非零退出
>
> [EN] The same workflows live under two organizations, because a private repository is not
> referenceable by other repositories by default, so callers point at whichever copy they can
> reach. The two copies are identical except for the organization prefix in their own
> self-references, and the documentation carries no project-internal identifiers either, so
> syncing is a prefix swap and either side can be the source. scripts/sync-fork.sh derives both
> organization names from the respective git remotes, hardcodes none, and is therefore the same
> script in both copies; it only touches working-tree files and never git history.

---

## 📑 文档索引

正文按主题拆到 `docs/`，本页只做汇总
每篇都能单独读

| # | 文档 | 看这里解决什么问题 |
|---|---|---|
| 01 | [项目架构与脚本引用机制](docs/01-architecture.md) | 仓库里有什么、各目录职责、工作流怎么找到 `scripts/` |
| 02 | [工作流列表与详细说明](docs/02-workflows.md) | 18 个可复用工作流分别干什么、完整入参与 Secrets |
| 03 | [配置：一份聚合 YAML](docs/03-configuration.md) | 一个变量配完所有配置项，怎么写、怎么校验、有哪些键 |
| 04 | [GitHub App 创建与配置](docs/04-github-app.md) | `APP_ID` 从哪来、要勾哪些权限、常见 403/404 对照 |
| 05 | [Go 库流水线](docs/05-go-library-pipeline.md) | 三十来个 Go 库共用一条 CI 与上下游联动，要配什么、不用配什么 |
| 06 | [快速接入与多架构构建](docs/06-quickstart.md) | 复制一份能跑的 `pipeline.yml`、怎么出 arm64 镜像 |
| 07 | [通知与发布单](docs/07-notifications.md) | 飞书 / 企业微信 / Telegram 通知与 Release Note |
| 08 | [K8s 部署架构与模板](docs/08-kubernetes.md) | 部署出来的资源长什么样、12 个模板何时渲染、占位符从哪来 |
| 09 | [Composite Actions](docs/09-composite-actions.md) | 7 个复合 Action 的入参与出参 |
| 10 | [公共脚本](docs/10-scripts.md) | `scripts/` 下每个脚本的用法 |
| 11 | [自测清单](docs/11-self-check.md) | 改完之后怎么验 |
| 12 | [依赖层级](docs/12-dependency-graph.md) | 65 个仓库的真实依赖图、影响面与发版顺序 |

---

## 🧭 一分钟全貌

**这个仓库提供什么**：18 个可复用工作流 + 7 个 composite action + 一套 `scripts/`，
被三个账号下的二三十个仓库通过 `uses:` 引用
改这里一处，所有调用方同时生效

**两条主线**

| 主线 | 面向 | 核心工作流 |
|---|---|---|
| 服务交付 | 后端 / 前端业务仓库 | `backend-build-binary` → `backend-build-docker` → `backend-deploy-k8s`（前端同构） |
| Go 库维护 | 三十来个 `go-*` 公共库 | `go-library-test`（测试闸门）、`go-trigger-downstream`（发版通知下游）、`go-bump-upstream`（下游升依赖） |

**配置只有两个入口**

- 明文走 `vars`：一份 `PIPELINE_CONFIG` 总表，可按节用 `<SECTION>_CONFIG` 覆盖
- 密钥走 `secrets`：一份 `PIPELINE_SECRETS`，或逐个单独的 secret

两者都由 `resolve-config` 这一个 action 解析、校验、合并、打码，再经 `GITHUB_ENV` 交给后续步骤
详见 [配置](docs/03-configuration.md)

**最容易踩的三个坑**

| 坑 | 症状 | 出处 |
|---|---|---|
| `secrets: inherit` 跨账号传不过去 | 没有任何报错，只有依赖该 secret 的那一步静默跳过 | [配置](docs/03-configuration.md#inherit-cross-account) |
| App 没装到目标仓库 | 换令牌成功，但对该仓库的操作 404 而不是 401 | [GitHub App](docs/04-github-app.md) |
| 私有仓库下 checkout 本仓库 | 报 `Repository not found`，看起来像仓库不存在，实际是没权限 | [Composite Actions](docs/09-composite-actions.md) |

---

## 📜 许可证

MIT
