# Working in this repository

本仓库建设独立的 Artifact Ledger。先阅读 [README](README.md)、[需求](docs/REQUIREMENTS.md)、[架构](docs/ARCHITECTURE.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md) 和 [接口提案](docs/INTERFACE_PROFILE.md)。文档均为 Candidate；不要将计划写成已实现能力。

## Current execution boundary

程序仓库文档 Gate 的本地采用关系、固定规则来源、当前状态与权限见 [Gate declaration](docs/PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md)。该文件是每次执行的必读入口。当前 **OPEN，Authorized implementation scope: none**。可以继续整理和评审文档；代码、测试源码、实现骨架、依赖集成和运行配置均须等待有效关闭记录。

维护者使用固定的私有 companion 规则来源；本仓库不复制其正文。执行者不能读取规则、不能核实基线或 Owner 决策时，应保持 OPEN 并说明具体缺失。公开消费者理解拟议接口和将来使用软件，不需要读取该 companion。不得把规则来源不可读改写为自动获得实现授权。

## Engineering boundaries

- 元数据、可用 payload 与成功幂等结果必须共同提交；禁止先宣告成功再补内容。
- 原始 ID、版本和关系不可静默改写。不得把摘要、文件名或存储路径当作 Artifact 身份。
- 本仓库不拥有证据真实性、知识准入、授权决策、Agent 调度或默认当前版本。
- 每个变更保持单一可审阅目的。维护公共接口，不引入消费者私有字段、账户或内部目录依赖。
- 用真实、准确的名称描述行为和数据范围；执行记录内容不属于本仓库能力。出现工具误判时保留具体错误、澄清真实功能，不通过伪装用途或绕过审批改变行为。
- Review 应与风险对应。文档检查不能证明事务、崩溃恢复或实际替换；实现阶段需保留相应执行证据。
- 不提交用户真实 payload、凭据、个人聊天记录、私有理论正文或内部机器配置。
- 不自动合并、发布包、升级规则、扩大实现范围或改变仓库可见性。后续许可与发布状态以 Owner 决策和实际仓库事实为准。

新增消费者适配、云端实现或未来 Harness 集成时，先证明其输入、输出、错误与能力范围符合公共合同；供应商可替换是待验证能力，不能仅凭 Adapter 接口作完成声明。
