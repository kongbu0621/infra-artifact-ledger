# infra-artifact-ledger

可独立复用的制品记录组件：保存身份、不可变版本、内容校验信息与来源关系。

这里的“制品”指需要保留精确内容和版本的数字成果，例如报告、数据集、工作流 JSON 或模型输出。本仓库的独立目标，是让个人、团队和其他软件能够回答：这是哪个成果、用的是哪一版、内容是否完整、与哪些来源有关、失败重试是否重复登记。

**当前状态：A1 `0.1.0a1` alpha 已实现 Python 库、JSON CLI 与本地 SQLite backend，可从源码构建 wheel 并离线安装。** 尚未发布 PyPI 包，也不是稳定版。仓库为 Public，[MIT LICENSE](LICENSE) 已加入。P1–P5 授权及独立 CLOSED 记录见 [Gate](docs/PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md)；精确源提交、实际测试和安装证据见 [验证记录](docs/A1_VALIDATION.md)，支持范围见 [兼容表](docs/COMPATIBILITY.md)。

通过 Python 库和命令行工具，在本地单一受信数据域内，用 SQLite 同一事务保存元数据和有界内容字节；公共数据格式为后续其他实现提供对接边界。消费者不需要模型账号、Agent 框架、云账号或访问维护者的私有仓库。

典型用途是保存文档、数据集、工作流定义或模型输出的精确版本，再由上层应用读取和校验。Ledger 记录工作流或脚本时只保存内容，不执行它们。知识库可以引用这些版本并自行管理证据、审核和知识状态。

## 为谁复用，怎样接入

| 使用者与场景 | 复用本组件的能力 | 使用者继续负责 |
|---|---|---|
| 普通报告或数据处理工具 | 保存成果 v1/v2、读取指定版本、检查实际内容 | 生成报告、决定展示或采用哪一版 |
| 本地知识库 | 固定引用原始材料与派生成果的精确版本、保存来源关系 | 检索、Ontology、知识审核与有效性判断 |
| Code Driver 或其他独立 Agent | 记录生成文件、分析结果及其来源，失败时核对原写入 | 模型选择、工具执行、业务判断 |
| 工作流应用或 Harness 节点 | 保存工作流定义、输入输出的版本与内容 | 节点调度、跳转、略过与权限 |

接入方式是 Python 库或 JSON CLI。各项目可以使用同一个包并各自保存 Ledger；需要交换记录时使用有界完整导出包。数据存储位置、共享访问和云端替代实现各有约束，不能由“可复用”推断为共享数据库或任意厂商产品已互换。

从 [复用与接入指南](docs/USAGE.md) 构建安装并选择方式，再按 [普通报告完整示例](docs/REUSE_EXAMPLE.md) 完成“初始化 → 建立身份 → 保存 v1/v2 与来源 → 指定版本读回 → 校验 → 重试核对 → 新库导入”。两份指南的合成报告流程已在隔离安装环境执行；接入不要求先安装 Code Driver、Agent 或模型服务。

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
| [验证记录](docs/A1_VALIDATION.md) | 固定源提交、测试、资源边界、安装及故障证据 |
| [仓库形成记录](docs/REPOSITORY_FORMATION.md) | 独立仓库的理由、复用证据与待验证事项 |
| [开工状态](docs/PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md) | 当前范围、固定基线与 Owner 决策 |
| [A2 需求提案](docs/a2/REQUIREMENTS.md) | 一致快照、NAS 保存与新目标恢复的目标和限制 |
| [A2 架构提案](docs/a2/ARCHITECTURE.md) | 本地快照、挂载存储与恢复职责 |
| [A2 实施计划](docs/a2/IMPLEMENTATION_PLAN.md) | S1–S5 的顺序与完成条件 |
| [A2 接口提案](docs/a2/INTERFACE_PROFILE.md) | 独立快照格式、预算、库/CLI 与失败状态 |
| [A2 验收矩阵](docs/a2/ACCEPTANCE.md) | 故障、边界与真实 NAS 演练；当前均未运行 |
| [A2 开工状态](docs/a2/GATE.md) | Candidate / OPEN；当前只推进设计 |

后续路线：A1 本地可用 → A2 一致快照与真实存储恢复 → A3 两类真实消费者 → A4 独立第二实现验证替换。A1 可以先支持受限消费者试点；两份示例脚本不能代替 A3，单实现导出再导入也不能代替 A4。

A2 已进入设计提案阶段，尚无 snapshot/restore 实现或 NAS 验收结果；当前可执行能力与版本仍为 A1。新提案单独保存，不改写已批准 A1 文档或沿用 A1 开工授权。

软件包名为 `infra-artifact-ledger`，版本 `0.1.0a1`；Python import 为 `infra_artifact_ledger`，CLI 为 `artifact-ledger`。使用本仓固定源码构建的 wheel；不要用未核验来源的同名 PyPI 包替代。最低运行基线为 Python 3.11，Linux/Python 3.11 是 A1 必验组合；其他版本和平台的状态以兼容表为准。公共 metadata 的 `contract_version=0.1.0` 和 `contract_status=candidate` 保持不变，不能从合同版本推断软件稳定性。

维护与贡献从 [AGENTS.md](AGENTS.md) 开始。当前已有独立实现授权，欢迎审查实现与验收证据；实际集成消费者仍为 0，A2/A3/A4 尚未完成。
