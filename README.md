# infra-artifact-ledger

为数字成果保存稳定身份、不可变版本、准确内容和来源关系，支持按指定版本取回、校验与快照恢复。

这里的“制品”是需要保留精确内容和版本的数字成果，例如报告、数据集、工作流定义或模型输出。个人工具、团队应用、知识库和 Agent 都可以通过 Python 库或 JSON 命令行接入，各自管理自己的 Ledger。

**当前版本：`0.2.0a1` alpha · Python 3.11+ · [MIT 许可](LICENSE)。** 从源码构建 wheel 安装，尚未发布 PyPI 包。已验证环境及限制见[兼容表](docs/COMPATIBILITY.md)。

## Ledger 是什么意思

**Ledger 的英文意思是“账本”**。在本项目里，可以把它理解为“成果账本”：保存文件、报告、数据等成果，并记录它们的身份、版本、内容和来源。

例如，一份报告保存了三个版本，它能让你知道有哪些版本、每版是什么内容、记录的来源是什么，并准确取回指定版本。

仓库名中的 `infra` 表示基础设施，`artifact` 指成果或制品，`ledger` 指账本；`infra-artifact-ledger` 可以理解为“成果账本基础组件”。

## 它解决什么问题

| 使用者的问题 | Ledger 保存的依据 |
|---|---|
| 这是哪个成果？ | 稳定的成果 ID；身份与文件名、路径、摘要分开 |
| 当时用的是哪一版？ | 独立的版本 ID、版本父关系和保留的旧版本 |
| 当时的内容是什么，有没有损坏？ | 实际内容字节、长度和摘要，可读回校验 |
| 它与哪些材料或成果有关？ | 调用方登记的来源关系与外部引用 |
| 上次保存超时了，再试会不会重复？ | 原请求的幂等身份与成功结果，用于核对或重试 |

例如，一份报告从 **v1 修改成 v2**，两版内容都保留，应用可以记录 v2 来自 v1。以后明确读取 v1，就能取回当时那一版。报告是否正确、是否通过审核、采用哪个版本，由应用决定；内容校验不证明来源真实。

## 与其他组件的关系

Ledger 负责保存成果与材料。Local Hand 已提供归档接入；P 已实际保存并按准确版本读回候选成果供验收，K 已实际保存、读取知识材料，并接通 P 的知识采用、使用反馈及明确学习候选投递。各组件分别管理自己的业务状态。

| 组件 | 主要职责与本仓关系 |
| --- | --- |
| **infra-artifact-ledger** | 保存稳定身份、不可变版本、实际内容与来源关系，支持准确读回、校验和快照恢复 |
| [infra-local-hand](https://github.com/kongbu0621/infra-local-hand) | 执行任务并返回结果；其可选 `local-hand-archive` 已能把成功封存的结果保存到 Ledger，再按准确版本读回；原任务状态仍由 Local Hand 管理 |
| [control-production-plane（P）](https://github.com/kongbu0621/control-production-plane) | 已接入的生产消费者：经归档适配保存候选成果，按指定版本读回、核对实际内容并用于验收；生产推进与运行记录由 P 管理 |
| [domain-operational-knowledge-core（K）](https://github.com/kongbu0621/domain-operational-knowledge-core) | 已接入的知识消费者：通过公共接口保存、读取原始材料并核对准确版本；材料支撑候选、明确审查、采用和反馈，知识状态由 K 管理 |

这些使用方通过公共接口接入，各自保留自己的业务状态。其他应用也可独立使用 Ledger，无需安装 Local Hand、P 或 K；成果保存成功不代表生产验收通过或知识已经准入。

## 在应用中的位置

下图表示接入关系。报告工具、知识库和 Agent 是可采用本组件的应用场景；各项目可以独立安装、各自保存 Ledger。

[![主线从上到下：应用提交 → Python 库或 JSON 命令行 → Ledger → 应用按指定版本取回、核对内容、继续使用。右侧 SQLite 支撑同一事务提交及读取与校验。](docs/diagrams/application-position.svg)](https://raw.githubusercontent.com/kongbu0621/infra-artifact-ledger/d0545f188c36a206ab625141137e1c02ab694ec9/docs/diagrams/application-position.svg)

[查看原图（可缩放）](https://raw.githubusercontent.com/kongbu0621/infra-artifact-ledger/d0545f188c36a206ab625141137e1c02ab694ec9/docs/diagrams/application-position.svg)

Ledger 在本地 SQLite 的同一事务中保存记录、有界内容和成功幂等结果。应用使用公共库或 CLI，无需直接操作数据库表，也不需要模型账号、Agent 框架、云账号或维护者的私有仓库。

应用负责内容生成、业务判断、权限和任务执行；Ledger 保存工作流或脚本时只存储其内容。各项目需要交换记录时，可以使用包含元数据和实际内容的有界完整导出包；导入保留原有身份与历史，并增加本次导入回执。它不提供增量同步。

## 完整能力设计与落地状态

上图保留单个 Ledger 的应用主线；下图补全公共合同、两套实现、搬运、快照及配套扩展之间的关系。**已实现**不等于全部平台或生产资格已通过；黄色虚线框是**待实现**的可选接入，灰色是调用方或部署侧职责。

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 240}}}%%
flowchart TD
    A["应用 / Agent<br/>明确成果身份、版本与原请求"]
    I["已实现 · A1 公共库 / JSON CLI<br/>写入、查询、内容校验<br/>原请求幂等核对"]
    S["已实现 · 本地 Ledger 核心<br/>不可变记录、实际内容与成功回执<br/>在同一事务内提交"]
    P["已实现 · 完整可移植包<br/>导出 / 整体校验 / 冲突拒绝<br/>导入保留身份并新增导入回执"]
    B["已实现 · 独立 B 实现<br/>独立环境与存储格式<br/>仅声明的 A1 范围，不提供 A2"]
    R["已实现 · A 的 A2 恢复入口<br/>一致快照、发布、核验<br/>新目录恢复与恢复后检查"]
    N["部署侧 · 本地或 NAS<br/>封存快照与独立预期摘要<br/>挂载、凭据与备份安排"]
    X["待实现 · 可选配套接入<br/>NAS 归档接收 / 查询 / 读回适配层<br/>多节点成果方案；非在线共享 SQLite"]
    C["调用方核对身份与实际内容<br/>采用明确版本继续业务<br/>恢复后的消费者启用单独决定"]
    A --> I
    A -.->|"未来可选远程成果接入"| X
    I --> S
    S -->|"准确版本与内容"| C
    S -->|"完整导出"| P
    P -->|"按合同导入"| B
    B -->|"完整导出"| P
    B -->|"A1 公共接口准确读回"| C
    P -->|"向相容目标导入"| S
    S -->|"一致快照"| R
    R -->|"发布并回读核对"| N
    N -->|"按预期摘要核验后恢复"| R
    R -->|"新本地库核对通过"| C
    X -.->|"未来仅调用公共接口"| I
    class I,S,P,B,R built
    class X pending
    class A,N external
    class C outcome
    classDef built fill:#e8f1fc,stroke:#35689c,color:#102a43
    classDef pending fill:#fff3d6,stroke:#9a6700,color:#493500,stroke-dasharray:5 3
    classDef external fill:#f1f3f5,stroke:#6b7280,color:#202938
    classDef outcome fill:#e6f4ea,stroke:#33734a,color:#173d26
```

A / B 通过可移植包互通，不能直接互开数据库文件；完整导入不等于增量同步。快照恢复保留原记录，不增加导入回执；恢复成功也不自动切换应用的当前写入实例。

待实现的 NAS 接收适配来自 [Local Hand 多节点成果方案](https://github.com/kongbu0621/infra-local-hand/blob/main/docs/MULTI_NODE_IMPLEMENTATION_PLAN.md#4-nas-成果归档最小新增实现)，尚不属于已实现的 Ledger 网络服务。云后端、流式搬运等仍是按需扩展方向，不由此图新增承诺；现有 A1 / A2 / A4 的实现与已测范围分别见[架构](docs/ARCHITECTURE.md)、[恢复合同](docs/a2/INTERFACE_PROFILE.md)和[兼容表](docs/COMPATIBILITY.md)。

## 快照与恢复

快照能力用于给受支持的单个 Ledger 生成一致备份，并恢复到全新本地目录。下面是包含 NAS 的恢复流程；**快照与恢复验收已在所列本地环境及 GX10 + CIFS（SMB 3.1.1）配置内完成。** 包含真实 NAS 保存与丢失恢复、受测故障处理、拒覆盖及恢复后继续使用；具体组合与未测边界见[兼容表](docs/COMPATIBILITY.md)。

```mermaid
flowchart TD
    A[("执行主机<br/>本地运行中的 Ledger")]
    B["生成一致快照<br/>校验内容与记录"]
    C[("NAS<br/>保存封存快照")]
    D["需要恢复时<br/>取回快照并完整校验"]
    E[("全新本地目录<br/>恢复 Ledger")]
    F["核对身份、版本与内容<br/>核对来源与成功幂等记录"]

    A --> B
    B -->|"发布并回读核对"| C
    C --> D
    D -->|"拒绝覆盖已有目标"| E
    E --> F
```

图中的执行主机、NAS 和恢复目录是部署角色，具体机器与目录由使用者配置。运行中的 SQLite 位于本机，NAS 保存封存快照；恢复保留原记录和幂等历史，不增加导入回执。

恢复范围是 **Ledger 实际保存的数据**，不包含应用另存的原文件、向量索引、模型或其他数据库。调用方负责准备挂载和保存独立的预期摘要；自动备份调度、挂载、凭据管理和归档轮换由部署侧安排。使用方式见[快照与恢复指南](docs/A2_USAGE.md)。

## 开始使用

1. 查看[兼容表](docs/COMPATIBILITY.md)，按[安装指南](docs/INSTALL.md)在项目独立虚拟环境中安装 `0.2.0a1` wheel。安装环境、源码和数据目录分别管理。
2. 按[接入指南](docs/USAGE.md#2-选择接入方式与数据归属)选择 Python 库或 JSON CLI，准备本地可靠文件系统上的新数据库路径。
3. 跟随[普通报告完整示例](docs/REUSE_EXAMPLE.md)，完成“初始化 → 建立身份 → 保存 v1/v2 → 指定版本读回 → 校验 → 原请求重试核对 → 新库导入”。
4. 需要备份时，按[快照与恢复指南](docs/A2_USAGE.md)使用自己的数据库、存储配置和全新恢复目标。

接入指南和报告示例用于学习基本读写；安装统一使用第 1 步的当前版本构建流程。接入不要求先安装 Code Driver、Local Hand 或模型服务。

| 接入方式 | 公共入口 |
|---|---|
| Python 读写与校验 | `infra_artifact_ledger` |
| Python 快照与恢复 | `infra_artifact_ledger.recovery` |
| JSON 命令行 | `artifact-ledger`，恢复入口为 `artifact-ledger snapshot` |

应用应保留实际采用的版本 ID，以及原写请求的幂等身份和输入。写入响应丢失时，沿原请求核对结果；具体处理见[失败后如何继续](docs/USAGE.md#6-失败后如何继续)。

## 当前能力与边界

| 能力 | 当前状态 |
|---|---|
| 身份、不可变版本、内容与来源记录；读取、校验、原请求重试核对 | 已有 alpha 实现和验证 |
| 有界完整导出 / 导入 | 已有同实现之间的搬运验证 |
| 一致快照、本地校验与新目录恢复 | 已有实现；所列 Linux x86_64 与 GX10 aarch64 环境的安装态本地闭环通过 |
| 真实 NAS 保存与丢失恢复 | 所列 GX10 + CIFS 配置内验收完成，含故障、冲突及状态延续；硬件断电等未测边界见兼容表 |
| P/K 真实消费 | 已有成果准确读回、知识材料与使用反馈、学习候选投递的现场记录；范围见[开发记录](docs/DEVELOPMENT.md#消费者接入状态2026-10-19) |
| A3 两类真实消费者 | Code Driver/P 与独立 Generic Agent 的实际采用及留证已在所列任务与实现范围内验收完成，见[两类消费者验收](docs/A3_CODE_DRIVER_CONSUMPTION.md) |
| A4 独立第二实现兼容 | 声明的 A1 candidate 范围及所列环境内验收完成，含最低 Python 3.11 实际安装运行；见[验收与边界](docs/DEVELOPMENT.md#a4-python-311-实际安装验收与阶段对账2026-10-30)。B 不提供 A2，完整生产资格未取得 |

具体软件、Python、SQLite、文件系统组合和证据以[兼容表](docs/COMPATIBILITY.md)为准；本地结果不代表所有平台或 NAS 配置可用。当前 alpha 适合在已验证范围内评估和试用。

部署范围是本地单一受信数据域、一个逻辑写入者。当前不提供网络服务、多租户权限、云同步或多机共写数据库。单个内容块（Blob）最多 64 MiB，一个版本包含的全部内容（含复用内容）最多 256 MiB；完整导出包与快照另有容量和格式限制，接入前查看[资源限额](docs/INTERFACE_PROFILE.md#3-资源限额)和[恢复前置条件](docs/A2_USAGE.md#2-准备目录数据库和摘要)。

## 文档

| 想了解什么 | 阅读入口 |
|---|---|
| 如何接入，完成一次真实读写 | [接入指南](docs/USAGE.md) · [报告完整示例](docs/REUSE_EXAMPLE.md) |
| 如何构建安装、备份和恢复 | [安装指南](docs/INSTALL.md) · [快照与恢复指南](docs/A2_USAGE.md) |
| 精确的参数、数据格式和错误语义 | [公共接口](docs/INTERFACE_PROFILE.md) · [恢复接口](docs/a2/INTERFACE_PROFILE.md) |
| 需求、责任边界与内部结构 | [需求](docs/REQUIREMENTS.md) · [架构](docs/ARCHITECTURE.md) |
| 支持哪些环境，有哪些验证依据 | [兼容表](docs/COMPATIBILITY.md) · [基本能力验证](docs/A1_VALIDATION.md) · [恢复能力验证](docs/A2_VALIDATION.md) |

开发过程、阶段记录和任务接续见[开发与接续记录](docs/DEVELOPMENT.md)；参与维护从 [AGENTS.md](AGENTS.md) 开始。
