# infra-artifact-ledger

可独立复用的制品记录组件：保存身份、不可变版本、内容校验信息与来源关系。

这里的“制品”指需要保留精确内容和版本的数字成果，例如报告、数据集、工作流 JSON 或模型输出。本仓库的独立目标，是让个人、团队和其他软件能够回答：这是哪个成果、用的是哪一版、内容是否完整、与哪些来源有关、失败重试是否重复登记。

**当前实现：`0.2.0a1` alpha，保留 A1 的 Python 库、JSON CLI 与本地 SQLite backend，新增 A2 五个恢复入口：create / publish / verify / restore / check_restore。** 可从固定源码构建 wheel 并离线安装，尚未发布 PyPI 包，也不是稳定版。仓库为 Public，采用 [MIT LICENSE](LICENSE)。A1 的 P1–P5 授权与历史证据见 [主 Gate](docs/PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md)、[A1 验证记录](docs/A1_VALIDATION.md)；A2 的 S1–S5 独立授权见 [A2 Gate](docs/a2/GATE.md)，实际执行与未测项见 [A2 验证记录](docs/A2_VALIDATION.md)。2026-10-16 已完成 Linux x86_64、真实 ext4、Python 3.11/3.12 的安装态本地 A2 合成闭环；GX10 与真实 NAS 验收仍未执行，整个 A2 仍为 PARTIAL。实际版本和范围见 [兼容表](docs/COMPATIBILITY.md)。

通过 Python 库和命令行工具，在本地单一受信数据域内，用 SQLite 同一事务保存元数据和有界内容字节；公共数据格式为后续其他实现提供对接边界。消费者不需要模型账号、Agent 框架、云账号或访问维护者的私有仓库。

典型用途是保存文档、数据集、工作流定义或模型输出的精确版本，再由上层应用读取和校验。Ledger 记录工作流或脚本时只保存内容，不执行它们。知识库可以引用这些版本并自行管理证据、审核和知识状态。

## 当前主任务与接续点（2026-10-15）

**本仓原来被阻塞的开发与验收是当前主任务，`infra-local-hand` 是为解除本地执行、验证和
结果取回阻塞而建设的支撑工具。** Owner 于 2026-10-15 明确纠正这一推进关系：应从本仓
中断处继续，不再让用户另选项目或反复制造演示任务。已有本地 CLI 或本地 Codex 能在原授权
范围内推进的工作直接接续；Local Hand 只补实际必需的能力缺口，不要求先建成完整通用执行器。

当前接续范围是已批准的 `A2-snapshot-nas-restore-v0.1` S1–S5 未完成项，
以 [A2 验证记录](docs/A2_VALIDATION.md)、[兼容表](docs/COMPATIBILITY.md)和保留现场为事实依据。
2026-10-15 的接续调整只记录任务顺序，没有新增执行结果。2026-10-16 的[独立本地实测](docs/evidence/2026-10-16-a2-local/README.md)现已补录：两版 Python 各完成 API/CLI 的 create、verify、restore、check_restore，并核对恢复内容、幂等记录和原合成库不变。它只覆盖所列本地环境，不代表整个 A2 完成。

1. 先核对并复用已有实现、安装和适用验证结果，从原中断点接续；结果未知的操作先查原记录，避免重复写入。
2. 复用已经通过的 Linux x86_64 / Python 3.11、3.12 / ext4 安装态本地闭环，只补仍缺失或实际失效的部分；入口见 [A2 运行手册](docs/A2_RUNBOOK.md)。手册给出完整流程，接续只选缺口对应步骤；已核实适用的 A2 build/runtime 环境不必重建，新写入演练仍用专属的新输出与恢复目标，保留旧现场。GX10 等未覆盖机器组合仍需独立证据，同一次有效结果覆盖的对应项目不重复运行。
3. 接续原范围内剩余的真实 NAS 等现场项。实际环境不具备时如实保留未完成项，不以本地替身冒充，也不据此取消这部分需求。本地闭环通过不等于 NAS 保存与丢失恢复通过。
4. Local Hand 仅针对上述任务的实际阻塞补齐能力；它是否需要改动由任务决定。其当前 NAS 作业屏蔽保持不变，配套任务说明见 [Local Hand PR #41](https://github.com/kongbu0621/infra-local-hand/pull/41)；本仓接续不依赖该 PR 先合入，也不由此获得修改 Local Hand 部署或 NAS 服务的权限。

进展以本仓原任务实际完成了什么衡量。已有证据、失败记录和用户数据保留；不因本次接续重做
全部历史测试、另建审批套件，或把知识库、完整理论闭合、A3/A4 提前作为 A2 的前置条件。
本仓继续保持独立库/CLI，不引入对 Local Hand、ChatGPT 或模型账号的运行时依赖。

## 为谁复用，怎样接入

| 使用者与场景 | 复用本组件的能力 | 使用者继续负责 |
|---|---|---|
| 普通报告或数据处理工具 | 保存成果 v1/v2、读取指定版本、检查实际内容 | 生成报告、决定展示或采用哪一版 |
| 本地知识库 | 固定引用原始材料与派生成果的精确版本、保存来源关系 | 检索、Ontology、知识审核与有效性判断 |
| Code Driver 或其他独立 Agent | 记录生成文件、分析结果及其来源，失败时核对原写入 | 模型选择、工具执行、业务判断 |
| 工作流应用或 Harness 节点 | 保存工作流定义、输入输出的版本与内容 | 节点调度、跳转、略过与权限 |

接入方式是 Python 库或 JSON CLI。各项目可以使用同一个包并各自保存 Ledger；需要交换记录时使用有界完整导出包。数据存储位置、共享访问和云端替代实现各有约束，不能由“可复用”推断为共享数据库或任意厂商产品已互换。

按 [复用与接入指南](docs/USAGE.md) 选择方式，再按 [普通报告完整示例](docs/REUSE_EXAMPLE.md) 完成“初始化 → 建立身份 → 保存 v1/v2 与来源 → 指定版本读回 → 校验 → 重试核对 → 新库导入”。两份指南的 A1 合成报告流程已在隔离安装环境执行；安装本次 `0.2.0a1` 使用 [A2 构建与运行手册](docs/A2_RUNBOOK.md)，不用指南中的历史 A1 wheel 文件名。接入不要求先安装 Code Driver、Agent 或模型服务。

**A2 的独立用途**是给受支持的单个 Ledger 生成一致快照，保存到经过实测的挂载存储，再完整恢复到全新本地目录；保留版本、内容和原操作幂等记录，拒绝覆盖已有目标。应用通过 `infra_artifact_ledger.recovery` 或 `artifact-ledger snapshot` 调用，使用自己的数据库、目录和预期摘要；每台机器、每个项目的虚拟环境独立。接入参数和失败后核对见 [A2 使用指南](docs/A2_USAGE.md)。

这份恢复能力覆盖该 Ledger 实际保存的数据，不覆盖外部原文件、向量索引、模型或其他数据库。NAS 用于保存封存快照，不作为 SQLite 运行库直接打开。首版本地候选为 ext4/xfs/btrfs，NAS 候选为 nfs/nfs4/cifs；每个目标仍需能力和实机证据，不能由单元测试或 Adapter 存在推断可用。

## 文档与交付状态

| 文档 | 回答的问题 |
|---|---|
| [复用与接入指南](docs/USAGE.md) | 何时采用、如何接入、调用方保存什么、怎样验收 |
| [普通报告完整示例](docs/REUSE_EXAMPLE.md) | 一组内容、请求和命令如何串成独立使用流程 |
| [需求](docs/REQUIREMENTS.md) | 为谁解决什么问题，什么结果算完成 |
| [架构](docs/ARCHITECTURE.md) | 责任边界、事务、模块与扩展方式 |
| [实施计划](docs/IMPLEMENTATION_PLAN.md) | A1 如何实现、验证和交付 |
| [公共接口](docs/INTERFACE_PROFILE.md) | 记录、操作、错误和完整字节搬运格式 |
| [兼容表](docs/COMPATIBILITY.md) | 已验证的软件、平台与数据格式，以及未支持范围 |
| [A1 验证记录](docs/A1_VALIDATION.md) | A1 固定源提交、测试、资源边界、安装及故障证据 |
| [A2 使用指南](docs/A2_USAGE.md) | 五入口、CLI、摘要保管、NAS 配置及不明结果处理 |
| [A2 运行手册](docs/A2_RUNBOOK.md) | 独立环境构建、GX10 与真实 NAS 合成恢复验收 |
| [A2 验证记录](docs/A2_VALIDATION.md) | Cloud 与真实本地安装态证据、固定版本及剩余实机项目 |
| [仓库形成记录](docs/REPOSITORY_FORMATION.md) | 独立仓库的理由、复用证据与待验证事项 |
| [开工状态](docs/PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md) | 当前范围、固定基线与 Owner 决策 |
| [A2 需求提案](docs/a2/REQUIREMENTS.md) | 一致快照、NAS 保存与新目标恢复的目标和限制 |
| [A2 架构提案](docs/a2/ARCHITECTURE.md) | 本地快照、挂载存储与恢复职责 |
| [A2 实施计划](docs/a2/IMPLEMENTATION_PLAN.md) | S1–S5 的顺序与完成条件 |
| [A2 接口提案](docs/a2/INTERFACE_PROFILE.md) | 独立快照格式、预算、库/CLI 与失败状态 |
| [A2 验收矩阵](docs/a2/ACCEPTANCE.md) | 已批准的故障、边界与真实 NAS 验收要求；执行状态另见 A2 验证记录 |
| [A2 开工状态](docs/a2/GATE.md) | 已按固定基线授权 S1–S5；实现与验收分别记录 |

后续路线：A1 本地可用 → A2 一致快照与真实存储恢复 → A3 两类真实消费者 → A4 独立第二实现验证替换。A1 可以先支持受限消费者试点；两份示例脚本不能代替 A3，单实现导出再导入也不能代替 A4。

A2 设计已通过 PR #5 合并，并获独立开工授权；后续实现以独立 CLOSED 记录为祖先。五份已批准设计文档保留基线原文，其中 Candidate / OPEN / NOT_RUN 描述的是设计形成时的状态；当前权限见 A2 Gate，实现及实际验收见 A2 验证记录。GX10、本地文件系统与真实 NAS 的证据分别记录，不把计划、模拟测试或历史 A1 通过自动转成 A2 实机通过。

软件包名为 `infra-artifact-ledger`，版本 `0.2.0a1`；Python import 为 `infra_artifact_ledger`，CLI 为 `artifact-ledger`。使用本仓固定源码构建的 wheel；不要用未核验来源的同名 PyPI 包替代。最低运行基线为 Python 3.11，Linux/Python 3.11 是 A1/A2 必验组合；其他版本和平台的状态以兼容表为准。公共 metadata 的 `contract_version=0.1.0` 和 `contract_status=candidate` 保持不变，不能从合同版本推断软件稳定性。

维护与贡献从 [AGENTS.md](AGENTS.md) 开始。当前已有独立实现授权，欢迎审查实现与验收证据；实际集成消费者仍为 0，A2/A3/A4 尚未完成。
