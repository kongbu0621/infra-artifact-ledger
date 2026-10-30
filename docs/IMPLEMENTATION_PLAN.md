# 实施与验收计划

状态：Owner 已批准的 A1 实施与验收计划；Decision Authority：Owner。授权 scope：`A1-local-ledger-v0.1`。当前 `0.1.0a1` alpha 已实现 P1–P5 对应代码与验收程序，实际结果以 [验证记录](A1_VALIDATION.md) 和 [兼容表](COMPATIBILITY.md) 为准；准确 R/A/scope 及独立 CLOSED 记录见 Gate。需求来自 [REQUIREMENTS](REQUIREMENTS.md)，技术约束来自 [ARCHITECTURE](ARCHITECTURE.md)，字段级规范来自 [INTERFACE_PROFILE](INTERFACE_PROFILE.md)。

§1–§6 保留 A1 当时的实施基线；当前推进入口为 [A4 实施与验收准备](#7-a4-实施与验收准备2026-10-29)。历史固定审批顺序不作为本次准备或已有授权内工作的重复前置。

## 1. 输入基线与开工顺序

本轮基于初始仓库提交 `e26a9d6a524a8f0c0ab50b56cb8f03c61d12c23a`，当时仅有 README。A0 将本文、需求、架构及其规范性引用形成固定 commit A，由 [Gate](PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md) 记录。文档内容、实现范围或引用规范发生实质变化时，旧 A 的确认不能覆盖新设计。

Owner 对确切规则 R、文档 A 和 `A1-local-ledger-v0.1` 作出关闭决定后，先单独记录 CLOSED commit C，再从 C 开始实现 commit D。C 不混入实现。文档合并本身不改变 Gate 状态。后续按小 PR 推进，每个 PR 提供 diff、执行命令、结果和未证明事项。

A1 的范围包含下述 P1–P5、MIT LICENSE、本地安装与测试所需配置；不包含网络服务、NAS snapshot/restore、真实消费者系统改造、独立第二实现、erase/GC 或 CI 托管工作流。必要机器验证先在本地执行；当前仓库没有 Actions，后续如需新增托管门禁，单独说明成本、作用与采用范围。

## 2. 具体工程布局与依赖

以下是 A1 实现布局；这些文件在独立 CLOSED 提交之后加入，不属于 A0 文档提交：

| 实现路径 | 职责 |
|---|---|
| `src/infra_artifact_ledger/records.py`、`validation.py` | 记录类型、strict JSON、闭包与跨记录约束 |
| `src/infra_artifact_ledger/fingerprint.py` | 请求规范编码与 SHA-256；独立 Manifest 摘要规则 |
| `src/infra_artifact_ledger/service.py` | 公共写入/读取、幂等和错误语义 |
| `src/infra_artifact_ledger/sqlite_store.py` | 本地格式版本、事务、索引和 BLOB |
| `src/infra_artifact_ledger/portable.py` | 有界完整包与 descriptor，精确字节校验 |
| `src/infra_artifact_ledger/cli.py`、`__init__.py` | 公共库出口与单次 JSON CLI |
| `tests/`（输入工厂为 `acceptance_helpers.py`） | 操作级、故障、CLI、兼容性与独立安装验证；运行时生成合成数据，不提交大型 payload |
| `pyproject.toml`、`.gitignore`、`LICENSE` | 包、命令入口、临时产物排除、Owner 已确认的 MIT 许可 |
| `docs/COMPATIBILITY.md` | A1 实测的软件、数据格式与平台版本支持表 |

A0 提供的 [USAGE.md](USAGE.md) 与 [REUSE_EXAMPLE.md](REUSE_EXAMPLE.md) 已在 P5 更新为 alpha 构建与接入指南；隔离安装的库示例及 CLI 报告闭环已经执行，源提交、实际结果与未支持范围保存在验证记录中。

运行依赖使用 Python 3.11 标准库的 `sqlite3`、`json`、`hashlib`、`base64`、`argparse` 等，不需要模型 SDK。最低运行版本为 3.11，不使用 3.12 专属 API。测试使用 `unittest`；构建工具版本在 P1 固定并记录，构建依赖不进入运行依赖。外部安装验证使用离线 wheel 安装（在已准备的环境），避免测试悄悄读取私有 checkout。Linux/Python 3.11 下的编译、全部必需测试和安装闭环均为 A1 必验；只在 3.12 或更高版本通过不满足此条件。

本地数据库的既定设计含：格式头/版本；跨七类 ID 的唯一 `owned_identity`；各类 immutable record；版本父边与引用索引；按 BlobRef 关联的 BLOB；以三元幂等身份为唯一键的成功结果。这里的概念名不是公共 SQL 表名合同。记录正文与查询索引必须同事务生成，不允许两个不同 truth。数据库 schema 版本从 1 开始；A1 只初始化新库，未知版本拒绝打开，无自动 migration。无第三方迁移框架。

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

## 7. A4 实施与验收准备（2026-10-29）

已按[需求 §6](REQUIREMENTS.md#6-a4-当前需求与验收范围2026-10-29) 和 [架构 §7](ARCHITECTURE.md#7-a4-第二实现方案2026-10-29) 实现独立 B，完成下述第 1 步以及第 2–4 步中首个闭环所需的检查。实际 A/B 源码、wheel、33 项专项检查、互通和正式容量子项见[本轮记录](DEVELOPMENT.md#a4-独立-b-首个闭环2026-10-29)。完整矩阵、Python 3.11 及生产资格仍未完成；不把源码 HEAD 当作已有业务安装版本。

### 实施顺序

1. **先建立可交付的小闭环。** 在拟定 B 目录独立实现 init/open、三类写入、所需读取与 portable 路径；用新合成报告完成 A create/append/export → B import/get/read_blob/operation → B append。此时仅报告已覆盖用例，其余公共能力与边界仍待补齐，不提前称为完整 profile 兼容。
2. **完成声明范围与针对性反例。** 补齐全部 A1 公共库/CLI、verify、history、export 与重放/失败处理；按下表覆盖合同，而不机械移植 A 的内部测试。若发现公共合同歧义，记录具体输入与差异，修订受影响说明，不能把 A 当前返回自动当作规范。
3. **独立安装验收。** A/B 分开构建 wheel、使用各自独立验收环境和本轮新数据目录；从仓库外以隔离解释器及明确命令路径执行，核对实际导入来源，B 环境没有 A 包或源码路径。原业务环境不复用为 B 安装目标。实施代码做编译及受影响测试；最低 Python 3.11 的支持声明须有相应执行证据。
4. **形成有范围的结论。** A→B 首次搬运与后续使用通过后，再把 B 的新增历史导出到另一个全新 A 库核对，避免向原 A 库导入不同历史产生预期冲突。记录每项实际结果和未测能力，保留输入、原请求、回执及失败；主干提交不代替验收或部署。

### 验收矩阵

下表是完整验收要求，不表示全部已通过；当前已执行子项与剩余缺口见下方[对账](#当前覆盖与接续2026-10-30)，历轮记录保留各自时点。完整字段和阈值仍以 [公共接口 §2–§8](INTERFACE_PROFILE.md) 为准。合成样本只使用新 ID、新库及非业务内容；既有 P、Generic Agent、A2 原任务及 NAS 均不参与。

| 项目 | 关键输入与操作 | 判定依据 |
| --- | --- | --- |
| A4-01 独立入口 | 两个独立 wheel、各自 Python 库/CLI；移除 A 的可导入路径后运行 B | 安装来源可定位；B 不委托 A；新目标拒覆盖、未知本地格式拒绝；公共签名和 envelope 正确 |
| A4-02 完整搬运 | A 含空 Artifact、单 Blob、多文件 Manifest、分支/多父版本、Provenance、历史 Receipt 和三类幂等记录；导出到空 B | 七类 owned 记录、幂等三元组/指纹/时间逐项核对，全部实际 bytes/长度/摘要一致；B 恰增本次 Receipt 与幂等结果 |
| A4-03 空值与编码 | 空 Ledger、零字节 Blob、相同 bytes 不同 ID、Unicode 组合/分解字符、有序父集合与 Manifest、带控制字符的合法 key | 身份与顺序不合并；缺省字段不擅自补入指纹；固定规范字节和预期 SHA-256 一致；CLI 文件请求可查询并重放控制字符 key |
| A4-04 跨实现重放 | B 重放 A 已提交的原 create/append/import 请求及原内容；重放 B 自己的导入 | 返回原 result_ref/recorded_at，replayed=true，不新增记录；只用原请求和原包，不重新导出替代 |
| A4-05 继续写入 | B 在导入后新增版本，读取旧/新版本；原导入 key+原包重放；新 key+新 Receipt 导入旧包 | 旧历史不变，原导入仍返回原 Receipt；新导入因完整历史不同拒绝；B 导出到全新 A 后核对全部记录与 bytes 及新增回执 |
| A4-06 冲突与原子性 | 七类跨类型 ID 碰撞、同 key 不同指纹、包内幂等碰撞、Receipt 冲突、同 Artifact 历史子集/超集/异分支、后段非法记录 | 分别按合同返回 IDENTITY_CONFLICT / IDEMPOTENCY_CONFLICT / 对应校验错误；失败没有部分记录或内容公开，目标原状态保留 |
| A4-07 输入与封装 | strict JSON、未知版本/profile、erased、错误 Manifest 摘要、metadata/descriptor 摘要与长度不符、非规范 Base64、缺失/重复/额外 payload、缺件输出 | 按单一明确违规分别构造反例；分类与 commit_state 正确，原状态不变；不以重算外层摘要掩盖内层错误 |
| A4-08 容量 | 合同各长度、字节、深度与节点阈值的边界及超一；复用 Blob 仍计 Version 闭包，同 bytes 不同 BlobRef 分别计数 | 合法边界完成对应操作，超限有界拒绝且无写入；报告实际资源测量，不以缩小测试常量证明正式上限，也不默降 B 上限 |
| A4-09 并发与故障 | 同请求/冲突请求竞争、提交前失败、COMMIT 锁冲突、回滚无法确认、提交后处理失败或丢响应；原进程退出后查询 | 最多一个持久结果；BUSY 仅在未提交可证时返回；未知不改成失败或成功；确认提交保留 committed 及原身份，按原请求核对不重复写入 |
| A4-10 持久读取与损坏 | 关闭后另进程 open/get/history/operation/read_blob/verify；仅在本轮库副本注入损坏；读取/导出输出失败 | 正常重启可读；metadata 查询不冒充内容完整，read_blob/verify 拒绝损坏；重放不补写损坏内容；纯读取失败为 not_applicable |

### 当前覆盖与接续（2026-10-30）

下表对账本轮及已有证据，不重新执行已有业务或把历史“待验”当作当前缺口。当前 A 安装保持 `fcb2230`，B 原验收环境更新至 `4cc1308`；本轮仅修复 B 的额外 payload 错误分类。此前安装态结果按所列固定来源复用，实际安装差异及复验见[精确分类记录](DEVELOPMENT.md#a4-精确错误分类验收2026-10-30)。这不是完整 A4 验收通过声明。

| 矩阵项 | 已有证据与本轮结论 | 后续动作 |
| --- | --- | --- |
| A4-01 独立入口 | 独立 wheel、发行包/模块来源、无 A 的 B 环境、公共入口、新目标及外来本地格式拒绝已有安装态证据 | 复用；最低 Python 3.11 单列补验 |
| A4-02 完整搬运 | 空 Artifact、分支/多父、七类记录、历史 Receipt/三类操作、准确字节及新增差异已由安装态四进程链核对 | 复用，不再列为首轮未完成项 |
| A4-03 空值与编码 | 空库/空 Blob、同 bytes 不同 ID、Unicode/顺序、固定指纹及控制字符 key 的 CLI 查询/重放已有公共检查 | 复用；长度极值归 A4-08 |
| A4-04 跨实现重放 | B 重放 A 原 create/append/import 及 B 原 import；导入后继续 append 再重放仍返回原 Receipt/时间 | 复用，不能重新导出替换原请求包 |
| A4-05 继续写入 | B 新版本、旧/新读回、新 key 导入旧历史拒绝、B→全新 A 的完整差异核对已通过 | 复用 |
| A4-06 冲突与原子性 | 42 组七类跨类型 ID、历史子集/超集/异分支、三元组/指纹/Receipt 冲突、22 个插入后故障及原请求接续已有记录 | 复用对应安装态结果，不泛称身份/历史全部待验 |
| A4-07 输入与封装 | 73 组 API/CLI 单一违规及五组 CLI 缺失输入文件，A/B 各 151 项准确 code/commit_state、退出码、原状态保留、修正后接续和重放通过；额外 payload 分类缺陷已修复并安装，既有输出缺件证据复用 | 所列范围完成，复用已记录结果 |
| A4-08 容量 | 旧 Blob/闭包/累计历史、实际封装/metadata/descriptor/解码 payload、JSON 文件/响应/深度/节点证据保留；六类字符串的 26 个字段轴最小/最大及相邻长度在 A/B 各 540 个 API/CLI 用例通过 | 复用所列类型与阈值证据；其他单一违规的精确分类归 A4-07 |
| A4-09 并发与故障 | 同请求竞争、七类冲突竞争、COMMIT 锁、提交前回滚、unknown、提交后丢响应/清理以及中断已有安装态证据 | 复用明确故障注入范围；不新增“穷尽所有调度/真实掉电”前置要求 |
| A4-10 持久读取与损坏 | 四进程读回核对完整历史和实际 bytes；损坏副本、读取/导出失败、元数据与内容验证的区别已有安装态检查 | 复用所列用例及来源；不扩大为真实设备失效证明 |

具体输入、度量与复现入口见[实际容量记录](DEVELOPMENT.md#a4-验收对账与实际容量2026-10-30)及[字符串长度验收](DEVELOPMENT.md#a4-字符串长度边界验收2026-10-30)。A4-07 精确分类已在[所列范围](DEVELOPMENT.md#a4-精确错误分类验收2026-10-30)完成；下一项为已有合适环境中的 Python 3.11 实际安装运行。GX10 本轮检查的六个现存 Ledger 环境均为 3.12.3，平台缺口如实保留，不新建环境、不升级业务部署。A3 已完成范围和 A4/生产资格分别报告。

故障注入可使用各实现自己的测试边界，但结果须从公共入口核对，并说明注入范围。正常和拒绝路径比较公共记录/实际内容；物理文件和内部 SQL 布局不作跨实现相等判据。缺少真实掉电、A2 恢复、其他平台或不同数据库引擎证据时逐项保留，不由本矩阵推出生产资格。

完成标准：B 独立性可核对，声明的 A1 公共范围与上述矩阵均有安装态证据，A→B 搬运和后续使用及 B→全新 A 核对成立，没有未解释的身份、内容或提交语义差异。只完成首个闭环则记录部分完成；A4 与完整生产资格分别判断。验证足够后收敛，不为本阶段重跑已完成的 A2/A3 或建立新审批套件。
