# A2 快照与恢复接口提案

状态：**Candidate，尚未实现；A2 Gate OPEN**。Decision Authority：Owner。本文件是 A2 字段、预算、命令与状态的唯一规范，不修改 [A1 公共接口](../INTERFACE_PROFILE.md)。接口示意是设计规格，不是当前可执行 API。

## 1. 格式与预算

快照格式 `infra-artifact-ledger-snapshot/v1`，backend `sqlite`，data profile `bounded-local-v0.1`，schema 1，DELETE journal。snapshot_id 为调用者给出的32位小写十六进制字符串，不接受路径分隔符。SHA-256为64位小写十六进制；整数拒bool/float/指数形式；日期为有效UTC `YYYY-MM-DDTHH:MM:SSZ`。

| 对象 | 首版硬预算（候选） |
|---|---|
| 封存数据库文件 | 1 GiB = 1,073,741,824 bytes；backup中动态核对目标页数，复制检查实际字节 |
| records + operations | 合计最多50,000行；data列UTF-8合计最多16 MiB |
| refs | 最多250,000行；三个TEXT列UTF-8合计最多32 MiB |
| 全部业务表TEXT列 | 累计最多64 MiB，包括records/operations的索引键、refs和ledger_format；避免畸形索引键绕过data预算 |
| manifest / marker / storage config | 分别64 KiB / 4 KiB / 16 KiB |
| 每份A2 JSON | 最多1,024节点、容器深度8；UTF-8 strict，拒BOM/重复键/未知字段/非有限数；字节限额同时适用 |
| 复制块 / backup步长 | 1 MiB / 256 pages |
| 检查点期限 | 每次操作300秒，单调时钟累计；在SQLite progress、复制块和阶段切换检查，不是OS阻塞I/O硬超时 |
| CLI完整响应 | 64 KiB，包含LF；诊断有界，不回显payload/凭据 |

所有TEXT字节统计用BLOB长度而非字符数，在固定只读视图/私有副本内先统计后解码。A1单Blob、单Version内容闭包、字段和记录JSON约束继续适用。整库快照不套A1 portable的384 MiB/解码payload256 MiB限额；所有A2预算必须同时满足。无调大限额参数、截断或自动分包；改变预算需明确版本/方案和证据。

## 2. 目录、manifest与marker

generation目录名为snapshot_id，恰有三个普通文件：`ledger.sqlite`、`manifest.json`、`COMMITTED.json`；拒子目录、符号链接、FIFO、设备、socket、sidecar、额外文件。数据库文件长期封存时link count为1；marker通过hardlink拒覆盖发布可能短暂共享inode，不能仅以它的link count判坏。临时目录必须在generation外；完成后不再改三成员。

manifest恰含下表字段；producer采用 `ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False` JSON编码、无尾随LF。读者校验原始bytes的hash，不重新序列化替代原文。

| 字段 | 类型与语义 |
|---|---|
| format | 固定 `infra-artifact-ledger-snapshot/v1` |
| snapshot_id | 32位ID，与目录名相同 |
| created_at | UTC日期，描述创建，不代表精确T0或选择“最新版” |
| backend / schema_version / data_profile / journal_mode | 固定 `sqlite` / `1` / `bounded-local-v0.1` / `delete` |
| producer | 恰有package_version（非空、≤64 ASCII字符）、source_commit（40位小写Git SHA）；构建来源自报不是签名 |
| database | 恰有name（固定ledger.sqlite）、byte_length（正整数≤1 GiB）、sha256 |
| summary | 恰有counts、verified_blob_count、verified_byte_length，均按A1 verify实际计算；计数为非负整数且符合文件/行数预算 |

counts恰含 artifacts、versions、content_roots、blobs、manifests、provenance_links、import_receipts、idempotency_records。verify重新计算summary并逐项比较；自报值不是验证。

COMMITTED.json恰有 `format="infra-artifact-ledger-snapshot-commit/v1"`、snapshot_id、manifest_sha256，使用同一JSON编码。marker最后发布；无marker是不完整，有marker但数据/摘要/语义不符仍拒绝。manifest不含自身hash；调用者另外保存预期manifest摘要。

## 3. 库入口与CLI映射

拟新增公共模块 `infra_artifact_ledger.recovery`，关键字参数，同步返回envelope，失败抛RecoveryError。主机路径沿用A1非空路径边界，并满足本文目录/成员限制。scratch_parent是已存在的可靠本地目录，每次创建独立私有暂存；不使用隐式用户配置。

| Python函数及完整参数 | 拟新增CLI，前缀为 `artifact-ledger snapshot` |
|---|---|
| `create(*, db, output_root, snapshot_id, source_commit)` | `create --db PATH --output-root PATH --snapshot-id ID --source-commit SHA` |
| `publish(*, snapshot, storage_config, expected_manifest_sha256, scratch_parent)` | `publish --snapshot PATH --storage-config FILE --expected-manifest-sha256 HASH --scratch-parent PATH` |
| `verify(*, snapshot, expected_manifest_sha256, scratch_parent, storage_config=None)` | `verify --snapshot PATH --expected-manifest-sha256 HASH --scratch-parent PATH [--storage-config FILE]` |
| `restore(*, snapshot, target_dir, expected_manifest_sha256, scratch_parent, storage_config=None)` | `restore --snapshot PATH --target-dir PATH --expected-manifest-sha256 HASH --scratch-parent PATH [--storage-config FILE]` |
| `check_restore(*, target_dir, expected_database_sha256, scratch_parent)` | `check-restore --target-dir PATH --expected-database-sha256 HASH --scratch-parent PATH` |

create在既有本地output_root下产生snapshot_id目录，package_version来自运行包；source_commit是调用方提供的实际构建来源，验收另行核实。先在output_root下专属暂存完成backup/校验，再创建generation并复制封存DB/manifest、按[发布协议](ARCHITECTURE.md#4-nas-发布与存储能力)最后发布marker。原子mkdir与marker分别防止重用身份和使用半成品；不创建不存在的output_root。

publish只从本地封存快照发布到storage_config的archive_root/snapshot_id，保持三成员bytes不变，不能输入NAS来源。已经存在的generation一律TARGET_EXISTS，不重拍/续写/覆盖；先verify原ID/摘要。返回成功前验证所复制的数据库与manifest一致。

verify/restore输入精确generation路径。NAS来源必须传storage_config且位于其archive_root；本地来源省略。Linux最深匹配mountinfo负责分类，不能将未提供config的网络路径默认为本地。首版本地候选类型为ext4/xfs/btrfs，仍须满足可靠文件系统能力并分别留兼容证据；tmpfs、overlay、未知类型不作为持久目标。单元测试模拟路径分类不构成该平台实测。

restore的target_dir必须完全不存在；成功数据库为target_dir/ledger.sqlite。scratch_parent用于输入验证，最终待发布文件在新目标目录内的私有暂存子目录生成，保证同FS；成功时暂存移除，target_dir只剩ledger.sqlite。现有空目录、断链等也拒绝。完成前目标不交应用；不能边恢复边由第三方写入该目录。

check_restore只接受未启用业务写入的恢复目录，以同一打开fd的副本全量校验并比对原数据库hash；检查前后inode/size/mtime变化即失败。它不修改/同步目标，也不认证过去的发布历史。临时副本校验若改变数据库bytes，必须拒绝不能把变化后的文件作为已验证原快照。

API storage_config是dict；CLI FILE有界解析。缺失/不适用参数、重复CLI选项均INVALID_INPUT。路径不经shell拼接，存储内容不执行。所有暂存创建、文件打开、SQLite操作计入同一次期限；空间预估只做早期拒绝，实际ENOSPC仍逐步处理，不把free space检查视为保留空间。

## 4. Storage config

恰有format（`infra-artifact-ledger-storage/v1`）、profile（`mounted-posix-v1`）、storage_ref、mount_point、mount_root、mount_source、fs_type、archive_root。storage_ref非空≤128 ASCII字符；路径绝对；mount_root是预期mountinfo root字段的绝对路径；mount_source非空≤1,024 UTF-8 bytes；fs_type首版接受nfs/nfs4/cifs候选，仍须通过实际能力测试。

archive_root在确切mount_point下且预先存在；不自动创建挂载点或挂载NAS。每次NAS操作核对source/type/point/root/最深挂载及目录fd的mnt_id；本轮mount ID前后保持，拒非预期子挂载、root不符、overlay和符号链接父路径。mountinfo不能识别所有完整根bind，合同不作此承诺；部署者确认的预期端点与本轮fd/mount绑定共同限定目标。配置不含凭据，不写入快照；真实值留部署侧。

所需mkdir/O_EXCL/hardlink/文件及目录fsync/回读能力见[架构](ARCHITECTURE.md)。preflight只在指定的新合成目录中验证，并绑定mount/profile；实际调用不能忽略本次失败。能力不支持返回UNSUPPORTED_STORAGE，不自动改用覆盖rename或跳过同步。

## 5. 结果与失败合同

CLI stdout每次至多一份JSON+LF；help为文本。API返回同envelope。成功字段恰为protocol（`infra-artifact-ledger-recovery/v1`）、status（OK）、operation、publication_state、data。operation取create/publish/verify/restore/check_restore。

create/publish的data恰有snapshot_id、manifest_sha256、database_sha256、snapshot_path、summary、durability_scope；restore恰有snapshot_id、manifest_sha256、database_sha256、database_path、summary、durability_scope；verify恰有snapshot_id、manifest_sha256、database_sha256、summary；check_restore恰有database_sha256、database_path、summary。summary格式同manifest，durability_scope固定filesystem_acknowledged，不声称服务端掉电已测。路径只作locator，不进入持久身份。

失败字段恰为protocol、status（ERROR）、operation、publication_state、error。error恰有code、message（≤1,024 UTF-8 bytes）、stage（validate/snapshot/copy/verify/publish/sync/report）。RecoveryError保留相同code/message/stage/publication_state；参数解析无法确定操作时operation为null。不用A1 commit_state描述文件发布，不新增业务幂等记录。

| publication_state | 含义 |
|---|---|
| not_published | 确认未尝试公开marker/恢复文件；可能遗留本次专属目录/暂存 |
| published | 本次公开、同步与最终核对完成；成功后不保证介质永不损坏 |
| unknown | 已尝试公开且无法确认发布/同步结果；保留现场，按原路径/摘要核对 |
| not_applicable | verify/check_restore只读检查；或已有目标冲突，不判断既有对象发布状态 |

| code | CLI exit | 条件 |
|---|---|---|
| INVALID_INPUT | 2 | 字段/路径/参数/JSON非法 |
| UNSUPPORTED_FORMAT / UNSUPPORTED_STORAGE | 3 | 版本、WAL、schema/profile或存储能力不支持 |
| TARGET_EXISTS | 4 | generation或恢复目录存在，不覆盖 |
| NOT_FOUND / INCOMPLETE_SNAPSHOT | 5 | 输入缺失 / 缺marker或成员 |
| INTEGRITY_FAILURE | 6 | 摘要、成员、summary、SQLite或Ledger语义不一致 |
| RESOURCE_LIMIT | 7 | 任何预算超限 |
| BUSY / TIMEOUT | 8 | 锁冲突 / 检查点期限耗尽，不能推断远端无副作用 |
| IO_ERROR / PUBLICATION_UNKNOWN | 9 | I/O失败；公开结果不明时固定PUBLICATION_UNKNOWN |

成功exit0，verify/check_restore成功为not_applicable。异常若发生在已尝试公开之后且无法确认状态，PUBLICATION_UNKNOWN/unknown优先于其他错误。能证明link未创建任何新对象的冲突保持TARGET_EXISTS/not_applicable。公开后清理失败不删除目标；如果最终核对/同步尚未全部完成则unknown。

输出通道失败/进程终止可能无envelope，调用者保留原ID/hash/目标路径，不能仅凭exit猜副作用。check_restore不匹配报INTEGRITY_FAILURE并保留现场；应用已写入的目标不再适用原文件hash核对。没有自动resume、覆盖或清理已有对象。

create有单独边界：调用前只有snapshot_id/output_root/source_commit，成功响应完全丢失时可能尚未得到manifest hash。调用方先确认该目录属于本次独占操作，在64KiB上限内读取manifest原始bytes计算候选hash，核对snapshot_id及producer.source_commit，再把候选hash传给verify，核对marker、数据库与完整语义。此流程只确认当前目录自洽，不能声称已匹配一份事先独立保存的摘要、认证T0或证明历史调用成功；确认归属后才保存该摘要作为今后publish/restore的预期值。缺失或不完整目录保持未解决，不用同ID覆盖重做。若本次目录归属无法确认则保留现场，不能自动采用。publish和restore的预期hash在调用前已存在，不使用此例外。

## 6. 兼容

A2拟增包版本0.2.0a1，仍Alpha；本提案不改pyproject。A1 schema/metadata/portable及旧CLI输出保持；既有open可读成功恢复的schema1库。新快照格式不代表其他backend可恢复；未知版本拒绝。Python最低3.11、运行标准库及MIT延续A1，构建来源由实际验收核实。
