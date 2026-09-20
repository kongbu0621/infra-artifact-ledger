# infra-artifact-ledger

可独立复用的制品记录组件：保存身份、不可变版本、内容校验信息与来源关系。

**当前状态：A0 设计提案，尚无可安装的软件；文档开工 Gate 为 OPEN。** 本仓库目前不提供运行代码、可执行示例或已通过的持久化保证。仓库为 Public，许可证拟采用 MIT，待 Owner 随设计确认；目前尚未添加 LICENSE，不把公开可见等同于已授权复用。

计划提供 Python 库和命令行工具。在本地单一受信数据域内，用 SQLite 同一事务保存元数据和有界内容字节；通过公共数据格式支持后续其他实现。消费者不需要模型账号、Agent 框架、云账号或访问维护者的私有仓库。

典型用途是保存文档、数据集、工作流定义或模型输出的精确版本，再由上层应用读取和校验。Ledger 记录工作流或脚本时只保存内容，不执行它们。知识库可以引用这些版本并自行管理证据、审核和知识状态。

| 文档 | 回答的问题 |
|---|---|
| [需求](docs/REQUIREMENTS.md) | 为谁解决什么问题，什么结果算完成 |
| [架构](docs/ARCHITECTURE.md) | 责任边界、事务、模块与扩展方式 |
| [实施计划](docs/IMPLEMENTATION_PLAN.md) | A1 如何实现、验证和交付 |
| [公共接口提案](docs/INTERFACE_PROFILE.md) | 记录、操作、错误和完整字节搬运格式 |
| [仓库形成记录](docs/REPOSITORY_FORMATION.md) | 独立仓库的理由、复用证据与待验证事项 |
| [开工状态](docs/PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md) | 当前范围、固定基线与 Owner 决策 |

后续路线：A1 本地可用 → A2 一致快照与真实存储恢复 → A3 两类真实消费者 → A4 独立第二实现验证替换。A1 可以先支持受限消费者试点；两份示例脚本不能代替 A3，单实现导出再导入也不能代替 A4。

拟议包名为 `infra-artifact-ledger`，Python import 为 `infra_artifact_ledger`，CLI 为 `artifact-ledger`。名称尚未注册、包尚未发布。最低运行基线拟为 Python 3.11，Linux/Python 3.11 是 A1 必验组合；Python 3.12 可作附加兼容验证，不能代替 3.11 验收。其他版本或平台未经验证时不宣称支持。正式安装命令、版本兼容表及许可证随 A1 交付。

维护与贡献从 [AGENTS.md](AGENTS.md) 开始。仓库当前欢迎设计评审；首次实现须先完成记录在案的文档确认。
