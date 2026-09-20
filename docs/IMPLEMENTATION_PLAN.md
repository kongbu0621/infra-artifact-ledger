# A1 实施与验收计划

状态：Candidate，A0 文档提案；Decision Authority：Owner。拟议 scope：`A1-local-ledger-v0.1`。P1–P5 已获 Owner 授权，准确 R/A/scope 及独立 CLOSED 记录见 Gate；本文件本身不构成运行实现。需求来自 [REQUIREMENTS](REQUIREMENTS.md)，技术约束来自 [ARCHITECTURE](ARCHITECTURE.md)，字段级规范来自 [INTERFACE_PROFILE](INTERFACE_PROFILE.md)。

## 1. 输入基线与开工顺序

本轮基于初始仓库提交 `e26a9d6a524a8f0c0ab50b56cb8f03c61d12c23a`，当时仅有 README。A0 将本文、需求、架构及其规范性引用形成固定 commit A，由 [Gate](PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md) 记录。文档内容、实现范围或引用规范发生实质变化时，旧 A 的确认不能覆盖新设计。

Owner 对确切规则 R、文档 A 和 `A1-local-ledger-v0.1` 作出关闭决定后，先单独记录 CLOSED commit C，再从 C 开始实现 commit D。C 不混入实现。文档合并本身不改变 Gate 状态。后续按小 PR 推进，每个 PR 提供 diff、执行命令、结果和未证明事项。

A1 的范围包含下述 P1–P5、MIT LICENSE、本地安装与测试所需配置；不包含网络服务、NAS snapshot/restore、真实消费者系统改造、独立第二实现、erase/GC 或 CI 托管工作流。必要机器验证先在本地执行；当前仓库没有 Actions，后续如需新增托管门禁，单独说明成本、作用与采用范围。

## 2. 具体工程布局与依赖

以下路径均为计划，A0 不创建它们：

| 拟新增路径 | 职责 |
|---|---|
| `src/infra_artifact_ledger/records.py`、`validation.py` | 记录类型、strict JSON、闭包与跨记录约束 |
| `src/infra_artifact_ledger/fingerprint.py` | 请求规范编码与 SHA-256；独立 Manifest 摘要规则 |
| `src/infra_artifact_ledger/service.py` | 公共写入/读取、幂等和错误语义 |
| `src/infra_artifact_ledger/sqlite_store.py` | 本地格式版本、事务、索引和 BLOB |
| `src/infra_artifact_ledger/portable.py` | 有界完整包与 descriptor，精确字节校验 |
| `src/infra_artifact_ledger/cli.py`、`__init__.py` | 公共库出口与单次 JSON CLI |
| `tests/`、`tests/fixtures/` | 操作级、故障、CLI、兼容性与独立安装验证；仅合成数据 |
| `pyproject.toml`、`.gitignore`、`LICENSE` | 包、命令入口、临时产物排除、Owner 已确认的 MIT 许可 |
| `docs/COMPATIBILITY.md` | A1 实测的软件、数据格式与平台版本支持表 |

A0 已提供 [USAGE.md](USAGE.md) 与 [REUSE_EXAMPLE.md](REUSE_EXAMPLE.md) 的接入设计及完整合成场景。它们不是现有可运行示例；P5 将其更新为与具体构建对应的操作指南，记录实际执行结果和未支持范围。

运行依赖使用 Python 3.11 标准库的 `sqlite3`、`json`、`hashlib`、`base64`、`argparse` 等，不需要模型 SDK。最低运行版本为 3.11，不使用 3.12 专属 API。测试使用 `unittest`；构建工具版本在 P1 固定并记录，构建依赖不进入运行依赖。外部安装验证使用离线 wheel 安装（在已准备的环境），避免测试悄悄读取私有 checkout。Linux/Python 3.11 下的编译、全部必需测试和安装闭环均为 A1 必验；只在 3.12 或更高版本通过不满足此条件。

本地数据库计划含：格式头/版本；跨七类 ID 的唯一 `owned_identity`；各类 immutable record；版本父边与引用索引；按 BlobRef 关联的 BLOB；以三元幂等身份为唯一键的成功结果。记录正文与查询索引必须同事务生成，不允许两个不同 truth。数据库 schema 版本从 1 开始；A1 只初始化新库，未知版本拒绝打开，无自动 migration。无第三方迁移框架。

API、CLI JSON、portable metadata 及包格式均按接口文档实现，不另造隐式 defaults。运行配置仅显式数据库路径和规定操作输入；上限固定在 profile，不能用环境变量悄悄改变公共语义。路径为本机 locator，不写入 portable 记录。

## 3. 实施顺序

| 步骤 | 交付 | 前置及完成条件 |
|---|---|---|
| P1 公共边界 | 包/CLI 入口、记录与错误类型、严格解析、指纹、Manifest 校验、许可与版本标识 | CLOSED C 已存在；公共示例与独立人工计算向量一致；恶意/畸形输入被明确拒绝 |
| P2 原子本地记录 | 初始化/打开、create、append、精确读取与 verify、幂等查询 | P1；重启可读，分支/多父可记录，冲突不改状态，同 key 重放不新增记录 |
| P3 完整搬运 | 一致整库 export、descriptor、整包 import、ImportReceipt | P2；空库/空包、边界包、损坏包、所有碰撞情形有证据；不部分导入 |
| P4 故障与并发核对 | 事务前后进程终止、响应丢失、锁竞争、I/O 故障分类、重试核对 | P2/P3；持久结果完整或不存在，未知结果不误报；同请求并发收敛；COMMIT 成功后异常仍保留 committed，COMMIT 锁冲突先确认回滚 |
| P5 外部使用验收 | 隔离安装后的库/CLI 完整闭环、使用文档、兼容表与证据记录 | P1–P4；按 USAGE 与普通报告示例从安装走到内容读回、重试和新库导入；无私有资料或模型依赖；Owner/Reviewer 能按相同命令复核 |

P1–P5 属于一个已拟议 A1 范围，可分 PR 交付，不必每个局部实现动作重新请求授权。若范围或固定文档实质变更则按 Gate 重新确认。没有现存旧实现，首次实现不能虚构“旧版本失败”；后续缺陷须尽可能保存同一复现的修复前失败与修复后通过证据。

## 4. 需求—结构—验证映射

| 需求 | 架构责任 | 步骤 / 必须覆盖的验收 |
|---|---|---|
| R01 / R02 | 全局 owned ID、immutable record、版本 DAG | P2：七类跨类型碰撞；重复 create；缺父、跨 Artifact 父、自环/环；分支；显式多父内容；namespace/type 不可改 |
| R03 / R04 | ContentRoot、Manifest、bytes、typed provenance | P1/P2：单 Blob/多条目、空 payload、digest/length 错、逻辑键大小写和 Unicode、失效引用、provenance 类型正确；复用 Blob 的闭包总量限制；文件校验后变化不改变所提交 bytes；存脚本不执行 |
| R05 | 同事务成功结果、服务端指纹 | P1/P2/P4：同 key 同输入重放、同 key 异输入拒绝、body 数组顺序/缺省差异、合法控制字符 key 经 CLI 查询/重放、并发重试、提交后响应丢失、损坏后不伪造新事件 |
| R06 | 整体 import 校验和 portable 包 | P3：精准 metadata bytes、payload 哈希/长度、缺失/多余/重复 payload、上限及上限+1、payload-map 超限、未知版本、erased 拒绝、当前幂等身份与输入历史冲突 |
| R06 | 完整历史冲突判断 | P3：相同 Artifact 的相同历史收敛；子集/超集/不同分支拒绝；导入失败无 Receipt；新请求可新增 Receipt；重放不新增 |
| R07 | 事务提交边界与结果核对 | P4：写入前、中、commit 前、commit 后/响应前终止；COMMIT 成功后响应准备异常、stdout 断开；未取得事务与 COMMIT 持锁分别核对；重启后全量核对，未知结果使用原 key |
| R08 / R09 | 公共入口、版本/profile、无向上依赖 | P5：Linux/Python 3.11 的 wheel 隔离安装、独立进程库和 CLI、不同新库往返；SQLite schema 不作为公共 API；明确这不构成 A3/A4 |
| R10 | 一致快照/新目标恢复 | 不在 A1；A2 提案须定义真实存储、快照来源与全量比较，当前不得写“已恢复验证” |

P5 的复用验收以一个脱离 Code Driver 的普通报告消费进程为主线，在全新项目虚拟环境中从固定 wheel 安装；分别执行 Python 公共入口与独立 CLI 进程，核对指定版本的真实内容、来源和重试结果。另行确认两个独立 Ledger 的数据不会串写。CLI 接入检查覆盖参数数组、stdout JSON/退出码和 stderr 诊断；子进程被终止或无有效响应时按未知结果核对原幂等身份。指南中的每个步骤须记录源提交、构建版本、实际命令、退出码和读回内容的比较结果；任何尚未运行的入口或平台仍标为未验证。这里验证公开接入方式，不计为 A3 的两个真实消费者或 A4 的独立第二实现。

接入边界专项：P1/P5 核对包根公开导出 initialize/open/LedgerError、handle 方法与 execute 的关键字参数组合，涵盖 None、空映射及新空 Blob 的空 bytes，以及 CLI 必需/不适用选项、空 payload-map 与 0 字节文件的等价映射；P2/P4 核对同一 with 内先成功后失败，退出后此前提交仍可读；P2/P4/P5 核对仅 Blob bytes 损坏时 metadata/成功操作查询与 read_blob/verify 的不同结论；P3/P4 核对导入后目标新增版本时，原 key/原包仍重放原 Receipt，而新 key/新 Receipt 导入旧包因完整历史不同而拒绝。以上均沿用既有事务和重放语义，不新增批量事务或修复内容能力。

输入边界另需检查：重复 JSON key、NaN/Infinity、浮点/指数 byte_length、超安全整数、非法 UTF-8/BOM/lone surrogate、未知字段、重复 ID/entry_key、引用不闭合、悬空成功结果，以及 import body 历史时间和 fingerprint 保留。Manifest 摘要和请求指纹不得混用。

P4 分别核对四类结果：COMMIT 前失败并确认回滚 → not_committed；COMMIT 是否完成不可确认 → unknown；COMMIT 成功后仍能报告的非持久化异常 → committed 且保留原结果；输出通道失效 → 可能无 JSON，由原 key 核对。普通成功与重放成功都必须有 replayed 布尔值。读取和导出错误不声称改变 Ledger。

资源验收须记录机器环境、Python/SQLite 版本、输入大小与内存峰值。对每个上限测试 limit 与 limit+1，并确认超限不修改库；不以几条小 fixture 证明有界性。不设未经测量的吞吐 KPI；如约定上限造成不可接受资源开销，停止宣称支持并修订 profile。

## 5. 部署、回退与证据

A1 通过 wheel 安装，不自动创建 daemon、联网服务或用户数据目录。首次初始化显式指定新目标；打开既有目标不覆盖。回退软件版本前检查数据格式支持，不支持则拒绝；不得自动降级数据库。A1 导出包可用于已声明 profile 内的新库导入，但不能宣称替代 A2 一致快照恢复。

每个实现 PR 的证据记录包含：完整 source SHA、环境/依赖版本、命令与退出码、关键日志或内容摘要、故障注入点、验收项对应关系和已知限制。测试生成的用户数据不提交。Review 检查用例是否约束失败模型，不以测试数量或一次绿色输出代替闭环。

A1 完成定义：P1–P5 的必需检查通过、公开入口和文档可复现、没有未解释的原子性/身份/内容缺陷、无私有依赖、许可已落地、剩余限制准确公开。编译检查只能证明源码可编译；事务、内容、安装和 CLI 需要各自执行证据。

## 6. 后续入口与停止条件

A2 独立定义 snapshot/NAS/new-target restore；A3 在 A1 可用后接真实消费者；A4 接独立第二实现。均不因 A1 closure 自动授权。知识系统可以开始消费精确 ArtifactVersion，但证据、候选、审核和知识状态仍由消费者管理。

出现字段级规范冲突、同 ID 历史语义不清、无法证明原子提交、平台不支持必要持久化行为、公共材料不足以独立使用时，先报告具体证据、修订受影响设计，再继续该部分。NAS Git Authority 接纳与软件数据恢复分别推进，不捏造设备权限、存储位置或备份证据。
