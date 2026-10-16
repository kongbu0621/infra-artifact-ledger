# 开发与接续记录

本文面向维护者，集中保存开发推进关系、当前任务接续和历史文档入口。项目用途、接入方式与当前能力见 [README](../README.md)，执行指引见 [AGENTS.md](../AGENTS.md)。

本文的接续说明从 README 迁入；详细验证、失败现场和审批记录仍保留在各自原文档中。文档整理不新增执行结果，也不改变已有授权、产品接口或验收状态。

## 当前主任务与接续点（2026-10-15）

**本仓原来被阻塞的开发与验收是当前主任务，`infra-local-hand` 是为解除本地执行、验证和
结果取回阻塞而建设的支撑工具。** Owner 于 2026-10-15 明确纠正这一推进关系：应从本仓
中断处继续，不再让用户另选项目或反复制造演示任务。已有本地 CLI 或本地 Codex 能在原授权
范围内推进的工作直接接续；Local Hand 只补实际必需的能力缺口，不要求先建成完整通用执行器。

当前接续范围是已批准的 `A2-snapshot-nas-restore-v0.1` S1–S5 未完成项，
以 [A2 验证记录](A2_VALIDATION.md)、[兼容表](COMPATIBILITY.md)和保留现场为事实依据。
2026-10-15 的接续调整只记录任务顺序，没有新增执行结果。2026-10-16 的[独立本地实测](evidence/2026-10-16-a2-local/README.md)现已补录：两版 Python 各完成 API/CLI 的 create、verify、restore、check_restore，并核对恢复内容、幂等记录和原合成库不变。它只覆盖所列本地环境，不代表整个 A2 完成。

1. 先核对并复用已有实现、安装和适用验证结果，从原中断点接续；结果未知的操作先查原记录，避免重复写入。
2. 复用已经通过的 Linux x86_64 / Python 3.11、3.12 / ext4 安装态本地闭环，只补仍缺失或实际失效的部分；入口见 [A2 运行手册](A2_RUNBOOK.md)。手册给出完整流程，接续只选缺口对应步骤；已核实适用的 A2 build/runtime 环境不必重建，新写入演练仍用专属的新输出与恢复目标，保留旧现场。GX10 的修复版本地结果另见[独立证据](evidence/2026-10-16-a2-gx10/README.md)；其他未覆盖机器组合仍需独立证据，同一次有效结果覆盖的对应项目不重复运行。
3. 接续原范围内剩余的真实 NAS 等现场项。实际环境不具备时如实保留未完成项，不以本地替身冒充，也不据此取消这部分需求。本地闭环通过不等于 NAS 保存与丢失恢复通过。
4. Local Hand 仅针对上述任务的实际阻塞补齐能力；它是否需要改动由任务决定。其当前 NAS 作业屏蔽保持不变，配套任务说明见 [Local Hand PR #41](https://github.com/kongbu0621/infra-local-hand/pull/41)；本仓接续不依赖该 PR 先合入，也不由此获得修改 Local Hand 部署或 NAS 服务的权限。

接续现场项时，先按[私有部署记录](A2_RUNBOOK.md#0-接续现有部署)核对原执行端和实际访问绑定；已有且核实适用的安装从[复用入口](A2_RUNBOOK.md#21-复用已核实的安装)继续。当前机器没有配置，不等于 NAS 未配置。

进展以本仓原任务实际完成了什么衡量。已有证据、失败记录和用户数据保留；不因本次接续重做
全部历史测试、另建审批套件，或把知识库、完整理论闭合、A3/A4 提前作为 A2 的前置条件。
本仓继续保持独立库/CLI，不引入对 Local Hand、ChatGPT 或模型账号的运行时依赖。

## 阶段与历史记录

| 阶段 | 目标与当前状态 | 记录入口 |
|---|---|---|
| A1 本地 Ledger | Python 库、JSON CLI、本地 SQLite 及有界完整搬运已有 alpha 实现与验证 | [授权与范围](PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md) · [验证记录](A1_VALIDATION.md) |
| A2 快照与恢复 | create / publish / verify / restore / check_restore 已实现；安装态本地闭环通过，真实 NAS 及剩余现场项未完成，整体 PARTIAL | [授权与范围](a2/GATE.md) · [验证记录](A2_VALIDATION.md) |
| A3 真实消费者 | Code Driver 与独立 Generic Agent 两类消费者采用；实际集成消费者仍为 0 | [后续计划](IMPLEMENTATION_PLAN.md#6-后续入口与停止条件) |
| A4 独立第二实现 | 跨实现状态搬运与行为符合性待验证 | [需求与阶段边界](REQUIREMENTS.md#4-成功标准与阶段边界) |

A1 可以先支持受限消费者试点；两份示例脚本不能代替 A3，单实现导出再导入也不能代替 A4。A2/A3/A4 尚未完成，各阶段分别取得证据。

A2 设计已通过 PR #5 合并，并获独立开工授权；后续实现以独立 CLOSED 记录为祖先。五份已批准设计文档保留基线原文，其中 Candidate / OPEN / NOT_RUN 描述的是设计形成时的状态；当前权限见 A2 Gate，实现及实际验收见 A2 验证记录。GX10、本地文件系统与真实 NAS 的证据分别记录，不把计划、模拟测试或历史 A1 通过自动转成 A2 实机通过。

软件版本与公共合同分别管理：当前软件为 `0.2.0a1` alpha，公共 metadata 的 `contract_version=0.1.0` 和 `contract_status=candidate` 保持不变，不能从合同版本推断软件稳定性。准确来源和兼容范围见[兼容表](COMPATIBILITY.md)。

## 维护文档索引

| 主题 | 文档 |
|---|---|
| 仓库形成与独立复用理由 | [仓库形成记录](REPOSITORY_FORMATION.md) |
| A1 需求、架构与公共合同 | [需求](REQUIREMENTS.md) · [架构](ARCHITECTURE.md) · [公共接口](INTERFACE_PROFILE.md) |
| A1 P1–P5 计划与授权 | [实施计划](IMPLEMENTATION_PLAN.md) · [主 Gate](PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md) |
| A1 实现及现场验证 | [A1 验证记录](A1_VALIDATION.md) |
| A2 一致快照、NAS 保存与恢复设计 | [需求基线](a2/REQUIREMENTS.md) · [架构基线](a2/ARCHITECTURE.md) · [接口基线](a2/INTERFACE_PROFILE.md) |
| A2 S1–S5 计划与授权 | [实施计划](a2/IMPLEMENTATION_PLAN.md) · [A2 Gate](a2/GATE.md) |
| A2 验收要求、实际结果与剩余项目 | [验收矩阵](a2/ACCEPTANCE.md) · [A2 验证记录](A2_VALIDATION.md) · [兼容表](COMPATIBILITY.md) |
| 实际部署与接续操作 | [A2 运行手册](A2_RUNBOOK.md) · [A2 使用指南](A2_USAGE.md) |

## 文档维护约定

- README 面向使用者：说明项目用途、接入方式、当前能力和必要限制。
- 本文保存开发推进与接续入口；执行指引维护在 AGENTS.md，详细结果继续追加到相应验证记录。
- 需求、架构和接口文档保留其明确的版本与历史基线，当前状态通过授权及验证记录判断。
- 已有证据、失败记录和未知结果保持原意；具体机器配置、凭据、用户数据和原始输出留在部署侧。

### 图示维护约定

README 的应用关系图应让读者第一眼看到本组件的用途：应用提交成果，经公共入口进入 Ledger，最后按指定版本取回、核对内容并继续使用。主线自上而下排列，取回使用节点位于最下方；SQLite 放在侧边，表达存储支撑。节点位置和箭头方向共同服务于这一阅读顺序。

- [SVG 图稿](diagrams/application-position.svg)固定 README 的展示布局；[Mermaid 关系源](diagrams/application-position.mmd)保留可审查的节点、文字和有向连线。修改语义时同步两份文件，不依赖 Mermaid 自动排版重现 SVG。
- 本图保持五个节点和五条有向连线：应用 → 入口 → Ledger → 取回使用；另有 Ledger → SQLite 的“同一事务提交”和 SQLite → Ledger 的“读取与校验”。存储双向关系不意味着自动执行工作流，也不把本图变成每次调用的严格时序。
- 调整布局不能增删真实关系、反转箭头，或把支撑组件排成使用流程的最终目标。节点和边的文字应与公共接口及 README 正文一致。
- 修改后查看 GitHub 分支 README 的实际显示，核对主线、最下方的使用结果、箭头归属、文字宽度及是否裁切；同时查看常用宽度、窄屏和深浅背景。窄屏缩小后难以阅读时，应能点开原图放大。语法解析成功不等于展示符合本意。

上述约定落实本图的阅读目的；其他图按自己的用途选择方向，不能把“结果必须在最下方”机械套用于循环图、部署图或关系网。文档排版检查不改变软件能力与验收状态。
