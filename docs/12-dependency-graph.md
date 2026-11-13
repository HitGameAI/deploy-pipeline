# 依赖层级

> 本文是 [deploy-pipeline](../README.md) 文档的一部分

## 🧬 依赖层级

本文由 [`scripts/dep-graph.py`](../scripts/dep-graph.py) 从各仓库真实的 `go.mod` 生成，不是手写的
手写的表一定会脱节，而这张表恰好是**发 tag 顺序**的依据 —— 顺序错了就会出现下游拿着旧上游去编译

重新生成：

```bash
python3 scripts/dep-graph.py --workspace ~/Workspaces/GoProjects \
    --extra-mods /tmp/gomods -o docs/12-dependency-graph.md
```

### 判定规则

与 [Go Trigger Downstream](02-workflows.md) 的自动推导**完全一致**，故意保持一致，
这样「文档里看到的下游」和「发版时真正被通知的下游」不会是两回事：

| 算 | 不算 |
|---|---|
| `go.mod` 里直接 `require` 的 kamalyes 模块 | 带 `// indirect` 的传递依赖 |
| | 被 `//` 注释掉的行 |
| | `replace` 指令指向的目标 |
| | 模块路径不在 `github.com/kamalyes/` 下的（fork 来的仓库） |

带 `// indirect` 的不算，是因为那些依赖由它们**自己的直接上游**在各自那一跳通知到，
重复通知只会让同一个仓库被反复 bump

### 规模

| 项 | 值 |
|---|---|
| 纳入的仓库 | 65 个（本地 46 个，仅远端 19 个） |
| 直接依赖边 | 153 条 |
| 层数 | 7 层 |

只在远端的那些仓库也算进来了：不算的话下游数会偏小，
而发版联动是按远端真实情况跑的，本地有没有 clone 并不影响它被通知

---

## 📚 分层

第 0 层不依赖本系任何仓库，是**最底层，改动与发版从这里开始**
往上每一层都只依赖更低的层，所以按层序发 tag 就不会出现「下游先发、上游后发」

### 第 0 层（18 个）

| 仓库 | 直接依赖 | 一级下游 | 传递影响 | 本地 |
|---|---|---|---|---|
| `go-argus` | — | 11 | 46 | ✅ |
| `go-assert` | — | 0 | 0 | 仅远端 |
| `go-dbsync-gorm-adapter` | — | 0 | 0 | 仅远端 |
| `go-dbsync-redis-adapter` | — | 0 | 0 | 仅远端 |
| `go-desensitize` | — | 0 | 0 | 仅远端 |
| `go-i18n` | — | 7 | 13 | ✅ |
| `go-idempotent` | — | 0 | 0 | 仅远端 |
| `go-jsonpath` | — | 0 | 0 | 仅远端 |
| `go-memo` | — | 0 | 0 | 仅远端 |
| `go-replay` | — | 0 | 0 | ✅ |
| `protoc-go-inject-tag` | — | 0 | 0 | ✅ |
| _内部服务 ×7_ | — | — | — | — |

### 第 1 层（3 个）

| 仓库 | 直接依赖 | 一级下游 | 传递影响 | 本地 |
|---|---|---|---|---|
| `go-argus-benchmark` | `go-argus` | 0 | 0 | ✅ |
| `go-bizerror` | `go-i18n` | 3 | 3 | ✅ |
| `go-toolbox` | `go-argus` | 35 | 44 | ✅ |

### 第 2 层（2 个）

| 仓库 | 直接依赖 | 一级下游 | 传递影响 | 本地 |
|---|---|---|---|---|
| `go-logger` | `go-toolbox` | 34 | 42 | ✅ |
| `go-pbmo` | `go-toolbox` | 5 | 14 | ✅ |

### 第 3 层（17 个）

| 仓库 | 直接依赖 | 一级下游 | 传递影响 | 本地 |
|---|---|---|---|---|
| `go-bot` | `go-logger` `go-toolbox` | 2 | 2 | ✅ |
| `go-cachex` | `go-argus` `go-logger` `go-toolbox` | 7 | 16 | ✅ |
| `go-casbin` | `go-logger` `go-toolbox` | 5 | 5 | ✅ |
| `go-config` | `go-argus` `go-logger` `go-toolbox` | 16 | 16 | ✅ |
| `go-distsaga` | `go-logger` `go-toolbox` | 2 | 2 | 仅远端 |
| `go-llmx` | `go-logger` | 0 | 0 | 仅远端 |
| `go-logger-adapter` | `go-logger` `go-toolbox` | 0 | 0 | 仅远端 |
| `go-logger-benchmark` | `go-logger` | 0 | 0 | ✅ |
| `go-namesilo` | `go-logger` `go-toolbox` | 0 | 0 | ✅ |
| `go-natsx` | `go-logger` `go-toolbox` | 1 | 12 | ✅ |
| `go-ossx` | `go-logger` `go-toolbox` | 0 | 0 | ✅ |
| `go-phantom` | `go-logger` `go-toolbox` | 0 | 0 | 仅远端 |
| `go-sqlbuilder` | `go-argus` `go-logger` `go-toolbox` | 8 | 16 | ✅ |
| `go-stress` | `go-logger` `go-toolbox` | 0 | 0 | 仅远端 |
| `grpc-runtime` | `go-logger` `go-toolbox` | 0 | 0 | 仅远端 |
| `kopy` | `go-logger` `go-toolbox` | 0 | 0 | ✅ |
| `kronos-cluster` | `go-logger` `go-toolbox` | 0 | 0 | 仅远端 |

### 第 4 层（13 个）

| 仓库 | 直接依赖 | 一级下游 | 传递影响 | 本地 |
|---|---|---|---|---|
| `go-bot-queue-nats` | `go-bot` `go-logger` `go-toolbox` | 0 | 0 | ✅ |
| `go-bot-queue-redis` | `go-bot` `go-logger` `go-toolbox` | 0 | 0 | 仅远端 |
| `go-casbin-gorm-adapter` | `go-casbin` `go-logger` `go-sqlbuilder` `go-toolbox` | 1 | 1 | ✅ |
| `go-casbin-kafka-adapter` | `go-casbin` `go-logger` `go-toolbox` | 0 | 0 | ✅ |
| `go-casbin-nats-adapter` | `go-casbin` `go-logger` `go-toolbox` | 1 | 1 | ✅ |
| `go-casbin-redis-adapter` | `go-cachex` `go-casbin` `go-logger` `go-toolbox` | 0 | 0 | ✅ |
| `go-core` | `go-cachex` `go-config` `go-logger` `go-toolbox` | 0 | 0 | 仅远端 |
| `go-distsaga-gorm-adapter` | `go-distsaga` `go-logger` | 0 | 0 | 仅远端 |
| `go-distsaga-redis-adapter` | `go-distsaga` `go-logger` | 0 | 0 | 仅远端 |
| `go-pbmo-benchmark` | `go-argus` `go-pbmo` `go-sqlbuilder` | 0 | 0 | ✅ |
| `go-swagger` | `go-config` `go-logger` `go-toolbox` | 2 | 12 | ✅ |
| `go-wsc` | `go-argus` `go-cachex` `go-config` `go-logger` `go-pbmo` `go-sqlbuilder` `go-toolbox` | 2 | 12 | ✅ |
| `kronos-scheduler` | `go-cachex` `go-config` `go-logger` `go-sqlbuilder` `go-toolbox` | 0 | 0 | ✅ |

### 第 5 层（1 个）

| 仓库 | 直接依赖 | 一级下游 | 传递影响 | 本地 |
|---|---|---|---|---|
| `go-rpc-gateway` | `go-argus` `go-cachex` `go-config` `go-i18n` `go-logger` `go-natsx` `go-sqlbuilder` `go-swagger` `go-toolbox` `go-wsc` | 11 | 11 | ✅ |

### 第 6 层（11 个）

| 仓库 | 直接依赖 | 一级下游 | 传递影响 | 本地 |
|---|---|---|---|---|
| `kronos-access-control-template` | `go-bizerror` `go-config` `go-logger` `go-rpc-gateway` | 0 | 0 | ✅ |
| `kronos-gateway-template` | `go-config` `go-i18n` `go-logger` `go-rpc-gateway` `go-toolbox` | 0 | 0 | ✅ |
| `kronos-key-service-template` | `go-bizerror` `go-cachex` `go-config` `go-logger` `go-rpc-gateway` `go-toolbox` | 0 | 0 | ✅ |
| `kronos-service-template` | `go-bizerror` `go-config` `go-i18n` `go-logger` `go-pbmo` `go-rpc-gateway` `go-sqlbuilder` `go-toolbox` | 0 | 0 | ✅ |
| `kronos-service-template-proto` | `go-argus` `go-config` `go-rpc-gateway` | 0 | 0 | 仅远端 |
| `kronos-share-proto-template` | `go-argus` `go-config` `go-rpc-gateway` | 0 | 0 | ✅ |
| _内部服务 ×5_ | — | — | — | — |

---

## 💥 影响面

「一级下游」是发 tag 后**直接**收到 `upstream-updated` 的仓库数
「传递影响」是沿依赖边一路传下去最终会被波及的总数 —— 这个数字才是改动风险的真实量级

| 仓库 | 一级下游 | 传递影响 | 一级下游清单 |
|---|---|---|---|
| `go-argus` | **11** | **46** | `go-argus-benchmark` `go-cachex` `go-config` `go-pbmo-benchmark` `go-rpc-gateway` `go-sqlbuilder` `go-toolbox` `go-wsc` `kronos-service-template-proto` `kronos-share-proto-template` _内部服务 ×1_ |
| `go-toolbox` | **35** | **44** | `go-bot` `go-bot-queue-nats` `go-bot-queue-redis` `go-cachex` `go-casbin` `go-casbin-gorm-adapter` `go-casbin-kafka-adapter` `go-casbin-nats-adapter` `go-casbin-redis-adapter` `go-config` `go-core` `go-distsaga` `go-logger` `go-logger-adapter` `go-namesilo` `go-natsx` `go-ossx` `go-pbmo` `go-phantom` `go-rpc-gateway` `go-sqlbuilder` `go-stress` `go-swagger` `go-wsc` `grpc-runtime` `kopy` `kronos-cluster` `kronos-gateway-template` `kronos-key-service-template` `kronos-scheduler` `kronos-service-template` _内部服务 ×4_ |
| `go-logger` | **34** | **42** | `go-bot` `go-bot-queue-nats` `go-bot-queue-redis` `go-cachex` `go-casbin` `go-casbin-gorm-adapter` `go-casbin-kafka-adapter` `go-casbin-nats-adapter` `go-casbin-redis-adapter` `go-config` `go-core` `go-distsaga` `go-distsaga-gorm-adapter` `go-distsaga-redis-adapter` `go-llmx` `go-logger-adapter` `go-logger-benchmark` `go-namesilo` `go-natsx` `go-ossx` `go-phantom` `go-rpc-gateway` `go-sqlbuilder` `go-stress` `go-swagger` `go-wsc` `grpc-runtime` `kopy` `kronos-access-control-template` `kronos-cluster` `kronos-gateway-template` `kronos-key-service-template` `kronos-scheduler` `kronos-service-template` |
| `go-config` | **16** | **16** | `go-core` `go-rpc-gateway` `go-swagger` `go-wsc` `kronos-access-control-template` `kronos-gateway-template` `kronos-key-service-template` `kronos-scheduler` `kronos-service-template` `kronos-service-template-proto` `kronos-share-proto-template` _内部服务 ×5_ |
| `go-sqlbuilder` | **8** | **16** | `go-casbin-gorm-adapter` `go-pbmo-benchmark` `go-rpc-gateway` `go-wsc` `kronos-scheduler` `kronos-service-template` _内部服务 ×2_ |
| `go-cachex` | **7** | **16** | `go-casbin-redis-adapter` `go-core` `go-rpc-gateway` `go-wsc` `kronos-key-service-template` `kronos-scheduler` _内部服务 ×1_ |
| `go-pbmo` | **5** | **14** | `go-pbmo-benchmark` `go-wsc` `kronos-service-template` _内部服务 ×2_ |
| `go-i18n` | **7** | **13** | `go-bizerror` `go-rpc-gateway` `kronos-gateway-template` `kronos-service-template` _内部服务 ×3_ |
| `go-swagger` | **2** | **12** | `go-rpc-gateway` _内部服务 ×1_ |
| `go-wsc` | **2** | **12** | `go-rpc-gateway` _内部服务 ×1_ |
| `go-natsx` | **1** | **12** | `go-rpc-gateway` |
| `go-rpc-gateway` | **11** | **11** | `kronos-access-control-template` `kronos-gateway-template` `kronos-key-service-template` `kronos-service-template` `kronos-service-template-proto` `kronos-share-proto-template` _内部服务 ×5_ |
| `go-casbin` | **5** | **5** | `go-casbin-gorm-adapter` `go-casbin-kafka-adapter` `go-casbin-nats-adapter` `go-casbin-redis-adapter` _内部服务 ×1_ |
| `go-bizerror` | **3** | **3** | `kronos-access-control-template` `kronos-key-service-template` `kronos-service-template` |
| `go-bot` | **2** | **2** | `go-bot-queue-nats` `go-bot-queue-redis` |
| `go-distsaga` | **2** | **2** | `go-distsaga-gorm-adapter` `go-distsaga-redis-adapter` |
| `go-casbin-gorm-adapter` | **1** | **1** | _内部服务 ×1_ |
| `go-casbin-nats-adapter` | **1** | **1** | _内部服务 ×1_ |

没有任何下游的 35 个（改它们不会联动到别人）：

`go-argus-benchmark` `go-assert` `go-bot-queue-nats` `go-bot-queue-redis` `go-casbin-kafka-adapter` `go-casbin-redis-adapter` `go-core` `go-dbsync-gorm-adapter` `go-dbsync-redis-adapter` `go-desensitize` `go-distsaga-gorm-adapter` `go-distsaga-redis-adapter` `go-idempotent` `go-jsonpath` `go-llmx` `go-logger-adapter` `go-logger-benchmark` `go-memo` `go-namesilo` `go-ossx` `go-pbmo-benchmark` `go-phantom` `go-replay` `go-stress` `grpc-runtime` `kopy` `kronos-access-control-template` `kronos-cluster` `kronos-gateway-template` `kronos-key-service-template` `kronos-scheduler` `kronos-service-template` `kronos-service-template-proto` `kronos-share-proto-template` `protoc-go-inject-tag`

---

## 🚦 发版顺序

按层序从第 0 层往上，每层内部互不依赖、可以并行
每发一层先等下游的 bump 流水线跑完再发下一层 —— `go-bump-upstream` 的三道闸门
（build / vet / test）会在这里拦住不兼容的升级，没过就开 PR 不碰主干

1. **第 0 层**：`go-argus` `go-i18n`
2. **第 1 层**：`go-bizerror` `go-toolbox`
3. **第 2 层**：`go-logger` `go-pbmo`
4. **第 3 层**：`go-bot` `go-cachex` `go-casbin` `go-config` `go-distsaga` `go-natsx` `go-sqlbuilder`
5. **第 4 层**：`go-casbin-gorm-adapter` `go-casbin-nats-adapter` `go-swagger` `go-wsc`
6. **第 5 层**：`go-rpc-gateway`

只在这一层出现、且没有下游的仓库不在上面的序列里，它们什么时候发都不影响别人

---

## ⚠️ 图里缺的边

这些仓库 require 了 kamalyes 名下的模块，但那个模块对应的仓库没被纳入本图
（既不在本地、也没通过 `--extra-mods` 提供），所以这几条依赖边在上面的分层里是缺失的：

| 仓库 | 没解析到的模块 |
|---|---|
| `go-dbsync-gorm-adapter` | `github.com/kamalyes/go-dbsync` |
| `go-dbsync-redis-adapter` | `github.com/kamalyes/go-dbsync` |

