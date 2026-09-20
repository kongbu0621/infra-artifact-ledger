# A2 实施计划提案

状态：**Candidate；A2 Gate OPEN；当前仅推进文档准备**。Decision Authority：Owner。需求、架构、接口见 [REQUIREMENTS](REQUIREMENTS.md)、[ARCHITECTURE](ARCHITECTURE.md)、[INTERFACE_PROFILE](INTERFACE_PROFILE.md)。开工顺序见 [GATE](GATE.md)。

## 1. 输入与交付边界

从A1已合并基线 `bd5128e7cebc844d8fca622c791681f7c65184f8` 出发。A1的GX10日志不覆盖A2；本阶段尚无实现、NAS能力探测或恢复通过证据。本设计PR只加文档与导航，不加可执行prototype、测试源码、依赖、schema或运行配置。

提议scope为 `A2-snapshot-nas-restore-v0.1`，包含S1–S5。首个真实闭环为GX10 → Owner NAS → GX10全新验收目录；精确挂载信息在部署侧采集。每机器每项目build/runtime venv分开。NAS服务配置、生产数据处理、业务切换、Local Hand接纳、Git Authority和自动调度不包含。

## 2. 实现阶段拟变更位置

| 路径 | 责任 |
|---|---|
| `src/infra_artifact_ledger/recovery.py` | 五个公共函数与RecoveryError |
| `src/infra_artifact_ledger/snapshot_format.py` | manifest/marker、严格解析、精确字节与预算 |
| `src/infra_artifact_ledger/snapshot_sqlite.py` | 只读T0、backup、本地副本校验与恢复 |
| `src/infra_artifact_ledger/snapshot_storage.py` | mountinfo/fd、mounted-posix-v1、复制、发布 |
| `src/infra_artifact_ledger/cli.py` | 新snapshot子命令，旧命令合同保持 |
| `sqlite_store.py` / `service.py` | 必要的内部验证/拒覆盖helper提取，不改A1可观察行为 |
| `tests/test_snapshot_*.py` / `tests/installed_snapshot_walkthrough.py` | 事务/文件边界、预算、失败与隔离安装 |
| `tools/acceptance/` | 专属合成目录的NAS验收，不自动挂载/改服务/删业务数据 |
| pyproject、USAGE、COMPATIBILITY、A2_VALIDATION | 实现后版本、使用说明、真实支持及执行证据 |

这些位置表示责任，局部helper拆分可调整，不能扩大公共API。只用标准库，不复制私有代码或依赖其他项目未合并实现。A1保持离线可用。

## 3. 顺序与检查点

| 步骤 | 交付与先决条件 | 验收 |
|---|---|---|
| S1 本地快照 | 独立A2 CLOSED提交已存在；格式/预算、只读T0、backup、完整校验 | T01–T04、T09 |
| S2 存储发布 | S1；独占generation、挂载身份、marker最后发布 | T05–T08、T10；NAS能力不足暂停该目标 |
| S3 新目录恢复 | S1/S2；固定fd、同份暂存验证/发布、拒覆盖和核对 | T11–T13 |
| S4 回归与安装 | S1–S3；Linux3.11 wheel独立安装，GX10附加验证 | T14，A1全部必需回归 |
| S5 真实NAS演练 | S1–S4、真实storage profile通过、隔离合成路径确认 | T15，只从NAS恢复并全量对账 |

T编号对应 [验收矩阵](ACCEPTANCE.md)。S1–S4通过不等于S5完成；NAS不支持硬链接/目录同步时不能以skip完成A2。必须记录失败、修订适配方案并按受影响范围重新确认。阶段不扩展为多机写库或云服务。

NAS profile尚未确认不阻止本地S1的设计及后续获批实现；它阻止真实目标采用和S5完成，不允许把本地模拟或S1–S4结果算作整个A2通过。

## 4. 复用与验证

复用A1 schema检查、Ledger.verify、错误分类、拒覆盖发布原则。现有open为mode=rw，不直接打开NAS归档；验证在本地私有副本进行。恢复复制完整SQLite状态，不调用import_bundle；内部重构保留A1同用例前后结果。

验证覆盖限额/limit+1、未知格式、真实错误阶段、时间预算、路径替换、已有目标/sidecar、响应丢失，必须检查原库/旧归档实际不变。SQLite统计与完整性检查设progress handler检查期限；Python图遍历等长循环也应有检查点，不因backup有回调就声称整操作已限时。OS阻塞I/O仍不保证硬超时。

记录实际磁盘/RSS/耗时，不把1GiB文件预算当RSS保证。至少一个合法Ledger同时满足累计payload>256MiB、SQLite文件>384MiB，并在A2预算内成功恢复；A1 portable按自身限额拒绝。实际封存大小和metadata/refs上限需边界证据；不可只缩小常量跑微型fixture。固定库文件以SQLite页为单位，文件字节limit±1可由有界复制/畸形输入测试覆盖，合法数据库成功边界用相邻合法页大小，不伪造“任意字节长度的合法SQLite”。

各预算的可达性与更紧约束按验收矩阵分别记录；解析器边界通过不等于固定schema接受该对象。无法构造的“合法恰好上限”不虚报成功，实际可达大对象仍须全链路验证。

## 5. 交付与回退

三层文档、接口和验收矩阵形成A2文档基线A。Owner明确按固定R/A/scope关闭后，先提交独立CLOSED记录C，再从C实现D。设计PR不自动合并、不改A1授权、不记录为A2开工批准。

实现后升级wheel，无schema迁移；A1旧行为回归。回退软件不删/覆归档或恢复目录，旧软件不支持新命令则明确不可用；不以回退binary修复数据。旧快照轮换、不完整目录清理、应用切换由独立运维决定。

A2完成需S1–S5证据、真实profile、Linux3.11与GX10结果、自包含使用文档和未测边界；之后才更新实际兼容表与完成状态。A3消费者、A4跨实现替换和跨库知识恢复另行定义，不借A2自动完成。
