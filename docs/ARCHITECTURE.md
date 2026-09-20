# 架构：有界本地 Ledger 与可移植边界

状态：Owner 已批准的 A1 架构基线，当前实现为 `0.1.0a1` alpha；Decision Authority：Owner。本文是 [需求](REQUIREMENTS.md) 的结构设计；操作字段以 [公共接口](INTERFACE_PROFILE.md) 为准，执行顺序见 [实施计划](IMPLEMENTATION_PLAN.md)。实际验证与未证明边界见 [验证记录](A1_VALIDATION.md) 和 [兼容表](COMPATIBILITY.md)。

## 1. 上下文与责任

唯一主责任是持久数字制品记录。消费者拥有业务语义、知识审核、权限和运行决策；Ledger 拥有制品 ID、不可变版本、内容绑定、来源记录及成功幂等结果。

依赖从消费者指向公共服务接口，再指向存储实现。Ledger 核心不依赖任何消费者。物理路径、数据库连接及供应商参数只属于适配层，不进入可移植身份。公共合同自包含；维护者的规则来源不是库的运行或安装依赖。

| 组件 | 责任 | 禁止越界 |
|---|---|---|
| Python API / JSON CLI | 解析请求、调用服务、稳定错误与结果；数据和诊断分流 | 不拼 shell 执行 payload，不自行选版本 |
| Record validation | 严格字段、类型、唯一性、引用闭包、DAG、Manifest 摘要 | 不把外部引用解析为本仓所有权 |
| Ledger service | 三类写操作、指纹、冲突、原子提交和公共查询 | 不依赖业务 Agent/Knowledge 状态 |
| SQLite store | 事务、全局 ID 索引、不可变记录、bytes 与成功结果持久化 | 不向消费者暴露表布局作为跨实现合同 |
| Portable package | 固定 metadata bytes、payload、完整性清单及搬运限制 | 不代替快照，不展开逻辑 entry_key 路径 |

服务层与存储层逻辑分离，A1 只实现一个 SQLite backend；不额外创建 plugin registry、远程服务或全能适配框架。独立第二实现必须用相同公共合同接受验证，不能仅换类名。

## 2. 数据与状态

Artifact 的身份与每个 Version 分离。Version 指向一个 ContentRoot，可绑定单个 Blob 或一个有序 Manifest。版本父关系是同 Artifact 内的 DAG；多个父节点只记录显式合并关系，合并后的 bytes 由调用者提供。普通 provenance graph 不要求是版本 DAG。

七类 owned ID 共享一个碰撞空间：Artifact、Version、ContentRoot、Blob、Manifest、ProvenanceLink、ImportReceipt。外部 opaque refs 独立。相同字节可对应不同 Blob ID 或 Artifact；摘要与存储位置均不能替代身份。时间只是记录元数据，不决定因果和当前状态。

新写入状态从“尚未存在”通过一次事务变为“已提交且可读取”。验证失败保持原状态；提交结果无法确认时对外呈现 `DURABILITY_UNKNOWN`，持久状态实际只允许完整提交或未提交。消费者使用原幂等身份查询/重试来消除不确定性。

没有修改版本、当前版本指针或删除 API。`payload_availability=erased` 在通用记录语言中有含义，但 A1 受限 profile 拒绝它；不自动补回 bytes。将来支持删除时必须先定义独立删除连续性和旧备份恢复策略。

## 3. 事务与并发

首次创建库只接受新目标路径；打开既有库严格检查本地格式版本，不初始化覆盖、不隐式升级。文件位于本地可靠文件系统。当前 SQLite 使用 rollback journal（DELETE）与 `synchronous=FULL`，启用外键；所有新记录、引用索引、BLOB 和幂等结果进入同一事务。完整性不能仅依赖数据库外键，还需应用级全局约束验证。

写路径先有界解析及校验字节，再 `BEGIN IMMEDIATE`，在事务内重查幂等键、引用与所有碰撞，写入后提交；只在确认成功后响应。首次提交时间在事务内生成；重试返回原记录。读取/整库导出在同一读事务中取得一致视图，不能分次读取拼成不同瞬间的状态。

Python 连接采用 `isolation_level=None`，由服务显式控制 BEGIN/COMMIT/ROLLBACK，避免隐式事务与上述边界混用；这在 [Python 3.11 的 sqlite3 接口](https://docs.python.org/3.11/library/sqlite3.html#transaction-control) 内可用。SQLite backend 的锁等待参数固定 5 秒，不在内部无限重试；它不代表所有 I/O 的总耗时上限。每个 Ledger handle 由创建线程使用，提供 close/上下文管理释放连接；不同进程使用各自连接。

部署只支持一个逻辑写入者。意外并发仍由 SQLite 锁串行化，不能破坏正确性：同请求最多一个持久结果；冲突请求至多一个成功；锁等待超时须证明本次未提交（尚未开始修改或已完整回滚）才可报告可重试状态。不能证明回滚或提交时一律报告不确定，不能把 I/O error 推断成没有提交。

未取得事务的锁冲突可报告 BUSY。COMMIT 阶段也可能因读连接持锁而返回 SQLITE_BUSY，事务此时仍可能有效，不能仅凭该错误码宣称未提交；本 backend 不在该调用内重试 COMMIT，先完成并确认回滚后才以 BUSY/not_committed 返回，否则进入 DURABILITY_UNKNOWN。该边界依据 [SQLite 事务语义](https://www.sqlite.org/lang_transaction.html)。已确认 COMMIT 成功后，响应准备或资源清理失败也不撤销该事实：若仍能报告，保留 committed 和原结果引用；若输出通道已经断开，调用者以原幂等身份核对。

事务前验证后到真正写入前仍可能被其他请求改变，因此关键检查必须在事务内重做。导入要求整体判断，禁止逐条自动提交。此前成功结果仍成立但 bytes 后来损坏时，读取/verify 单独报告完整性失败；重试不重写历史来“修复”它。

## 4. 可移植与冲突边界

A1 导出整个 committed Ledger。输出包含完整 metadata 原始 bytes、每个可用 payload 及校验信息；精确包格式由接口文档定义。SQLite 文件不属于 portable 格式；metadata 校验通过也不能代替实际 bytes 验证。

导入前验证版本/profile、strict JSON、ID、闭包、DAG、Manifest、字节长度与摘要；事务内再次比较目标。相同 Artifact ID 只有完全相同身份记录、版本和来源历史才可收敛。子集、超集、不同分支均冲突；不承担增量同步，也不重命名身份规避冲突。

其他 owned 记录和幂等身份逐个检查不可变一致性。输入历史保持原 ID 和时间。一次新的成功导入新增自己的 ImportReceipt；同一幂等请求重试只返回原 Receipt。导入目标在所有检查通过前不可暴露部分数据。

本 profile 为有界数据设计，整库超限时明确拒绝导出。调用方不能把“大包分段导入”当作已支持的历史同步能力。将来的流式、分段、远程存储需要单独版本与符合性证据。

## 5. 技术选择与扩展

Python 3.11 和标准库作为最低实现基线；当前接口没有需要 3.12 专属能力的要求，先验证 3.11 可降低采用门槛。SQLite 同事务保存 bounded BLOB 降低元数据和独立文件之间的失败组合。暂不采用“SQL metadata + 目录对象存储”或云对象存储，因为它们需要额外提交协议和 orphan reconciliation。A1 仍应有内部 store 边界，以便以后用相同不变量检验替换。

字段格式、CLI JSON 和 bundle 分别有版本。未知版本或 profile 不能尽力猜测。0.x 接口的实质改变要明确版本和迁移方案；读写旧数据前验证兼容性。包版本不自动等于 metadata contract 版本。上游兼容标记保持固定，不意味着私有仓库成为公共消费者依赖。

未来本地或云端实现以能力声明说明上限、支持操作和未支持行为，随后通过行为与状态搬运验证。优化、升级或切换 backend 不能改变 ID 和已经提交的历史。工作流的跳转/略过属于上层编排，不能跳过本组件的原子性与完整性不变量。

## 6. 恢复、交付与未证明假设

A1 证明进程重启、同机导出/导入及明确故障注入边界；不提供 snapshot/restore 实现。A2 才增加一致快照、真实存储搬运与只写新目标恢复；同时核对身份、版本、内容、关系及幂等结果。Git 仓库备份与运行数据备份不是同一条恢复链。

A1 不做自动 migration、覆盖式 rollback 或 live SQLite 的普通文件复制。遇到未知本地格式拒绝打开；程序回退只在明确支持既有数据格式时进行，不以降级二进制修复内容损坏。软件安装、运行目录与数据文件分离；卸载软件不删除用户 Ledger。

上限附近的内存峰值、SQLite/OS I/O 故障分类、实际序列化与跨入口闭环已纳入当前 [验证记录](A1_VALIDATION.md)，只能在记录明确的环境与注入边界内解释。未来真实消费者的适配成本仍待验证。A1 目标是正确性和可定位错误，不承诺吞吐量或真实掉电耐久性。支持范围扩大前必须补证，不能用接口设计本身作完成证明。
