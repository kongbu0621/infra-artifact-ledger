# Working in this repository

本仓库建设独立的 Artifact Ledger。先阅读 [README](README.md)、[需求](docs/REQUIREMENTS.md)、[架构](docs/ARCHITECTURE.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md) 和 [公共接口](docs/INTERFACE_PROFILE.md)。当前软件为 `0.2.0a1` alpha，保留 A1 原接口，已有 A2 create/publish/verify/restore/check_restore 五个恢复入口实现；wire 合同仍为 `candidate`，不意味着稳定发布。A1 历史证据见 [A1 验证记录](docs/A1_VALIDATION.md)，本次 Cloud 验证、固定源提交及未测项见 [A2 验证记录](docs/A2_VALIDATION.md) 与 [兼容表](docs/COMPATIBILITY.md)。A2 的 GX10、真实 NAS 验收尚未执行，整个 A2 尚未完成；实现存在和模拟测试通过不得改写为实机通过。

## Current execution boundary

程序仓库文档 Gate 的本地采用关系、固定规则来源、当前状态与权限见 [Gate declaration](docs/PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md)。该文件是每次执行的必读入口。当前 **CLOSED，Authorized implementation scope: A1-local-ledger-v0.1（P1–P5）**，Owner 决定见 Gate 引用记录。仅在固定范围内实现与验证；实现提交须以独立 CLOSED 记录提交为祖先。

维护者使用固定的私有 companion 规则来源；本仓库不复制其正文。执行者不能读取规则、不能核实基线或 Owner 决策时，应保持 OPEN 并说明具体缺失。公开消费者理解公共接口和使用软件，不需要读取该 companion。不得把规则来源不可读改写为自动获得实现授权。

## Engineering boundaries

A2 的需求、架构、计划、接口、验收与独立授权见 [A2 Gate](docs/a2/GATE.md)。A2 已按固定 R/A 获 Owner 明确批准，CLOSED 范围为 `A2-snapshot-nas-restore-v0.1` S1–S5；实现必须以独立 CLOSED 记录 C 为祖先。维护 A1 时继续使用已批准范围；改变 A2 规范内容须按 reopen 条件重新确认。五份设计文档保留基线原文，历史 OPEN 不覆盖后续明确授权，实际通过状态以执行证据为准。

A2 接入与实机操作从 [使用指南](docs/A2_USAGE.md)、[运行手册](docs/A2_RUNBOOK.md) 开始。每台机器、每个项目使用独立 build/runtime venv；NAS 演练限定本轮专属合成目录。当前环境未提供真实 NAS 时如实记录 NOT_RUN，不以本地替身或降低文件系统要求完成该验收。

- 元数据、可用 payload 与成功幂等结果必须共同提交；禁止先宣告成功再补内容。
- 原始 ID、版本和关系不可静默改写。不得把摘要、文件名或存储路径当作 Artifact 身份。
- 本仓库不拥有证据真实性、知识准入、授权决策、Agent 调度或默认当前版本。
- 每个变更保持单一可审阅目的。维护公共接口，不引入消费者私有字段、账户或内部目录依赖。
- 用真实、准确的名称描述行为和数据范围；执行记录内容不属于本仓库能力。出现工具误判时保留具体错误、澄清真实功能，不通过伪装用途或绕过审批改变行为。
- Review 应与风险对应。文档检查不能证明事务、崩溃恢复或实际替换；实现阶段需保留相应执行证据。
- 不提交用户真实 payload、凭据、个人聊天记录、私有理论正文或内部机器配置。
- 不自动合并、发布包、升级规则、扩大实现范围或改变仓库可见性。后续许可与发布状态以 Owner 决策和实际仓库事实为准。

新增消费者适配、云端实现或未来 Harness 集成时，先证明其输入、输出、错误与能力范围符合公共合同；供应商可替换是待验证能力，不能仅凭 Adapter 接口作完成声明。
