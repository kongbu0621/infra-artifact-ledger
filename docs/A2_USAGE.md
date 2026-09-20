# A2 一致快照与恢复使用指南

本文面向使用 `infra-artifact-ledger` 的独立 Python 应用、命令行消费者及部署者。软件版本为 `0.2.0a1` alpha，可从核对过的源码构建 wheel；没有公开包发布承诺。实际通过的平台、源提交和未测项目见 [A2 验证记录](A2_VALIDATION.md) 与 [兼容表](COMPATIBILITY.md)，不能把实现或单元测试当作 GX10/NAS 已验收。首次安装与实机验收按 [运行手册](A2_RUNBOOK.md)。

字段、预算与错误的规范来源为 [A2 接口](a2/INTERFACE_PROFILE.md)。本指南不改变已批准设计，也不将示例路径作为软件默认值。

## 1. 解决什么问题

A2 为一个受支持的本地 Ledger 生成一致、可校验的 SQLite 快照，可将其保存到经过实际能力验证的 NAS 挂载目录，并恢复到全新本地目录。个人工具、团队工具或 Agent 均可独立复用，不需要 Code Driver、模型账户或其他项目。

恢复保留已有 Artifact、Version、Blob 和操作幂等记录；它不走 portable 导入，不新增 ImportReceipt，不将数据合并到现有数据库。快照只覆盖该 Ledger 数据库实际保存的内容，不覆盖知识库的外部原文件、向量索引、模型、配置、其他数据库或 NAS 上的 Git 仓库。完整知识库备份需要上层另行定义一致性与恢复范围。

## 2. 准备目录、数据库和摘要

| 对象 | 调用前条件 |
|---|---|
| 源 `db` | A1 schema 1、`bounded-local-v0.1`、DELETE journal、UTF-8 的本地 Ledger；文件已存在 |
| `output_root` | 已存在的本地快照根目录；程序在其中独占创建 `snapshot_id` 子目录 |
| `scratch_parent` | 已存在的可靠本地暂存根目录；每次调用创建自己的私有子目录 |
| `target_dir` | 恢复目标必须完全不存在，连已有空目录或断开的符号链接也拒绝 |
| 本地文件系统 | 当前候选 ext4/xfs/btrfs，且满足拒覆盖创建、硬链接和文件/目录同步能力；tmpfs、overlay、未知类型不作为支持目标 |
| NAS | 调用方已挂载；使用经过真实能力验证的 nfs/nfs4/cifs 配置，不由组件挂载或修改服务 |
| 调用窗口 | 保持源文件、父路径、挂载与 journal 模式稳定；DELETE 事务可以并发，但读事务可能延迟写者提交 |

A2 支持 A1 数据库的一个明确子集：封存文件最多 1 GiB，另有业务记录、TEXT 字节及 JSON 预算。SQLite WAL、UTF-16、其他 schema/profile 均拒绝，不自动改模式、转码或迁移。完整限额见规范接口；不存在调大限额选项。

调用前保存 `snapshot_id`、源提交及目标根路径。`snapshot_id` 是自行生成的 32 位小写十六进制字符串；`source_commit` 是本次实际构建来源的完整 40 位小写 Git SHA。它是来源自报，验收时仍须与构建记录对账，不等于签名。

`create` 成功后，调用方要在独立的操作记录中保留返回的 `snapshot_path`、`snapshot_id`、`manifest_sha256`、`database_sha256` 和来源提交。后续 publish/verify/restore 传入此前保存的预期 manifest 摘要，不通过重新读取待校验归档来替换该预期值。

## 3. Python 接入

安装后只从公共模块 `infra_artifact_ledger.recovery` 导入五个函数及 `RecoveryError`，无需导入内部存储模块。所有公开函数只接受关键字参数。

| 函数 | 完整参数 |
|---|---|
| `create` | `db, output_root, snapshot_id, source_commit` |
| `publish` | `snapshot, storage_config, expected_manifest_sha256, scratch_parent` |
| `verify` | `snapshot, expected_manifest_sha256, scratch_parent, storage_config=None` |
| `restore` | `snapshot, target_dir, expected_manifest_sha256, scratch_parent, storage_config=None` |
| `check_restore` | `target_dir, expected_database_sha256, scratch_parent` |

下面是在调用方已经准备好目录、源库及本次构建 SHA 后的本地流程。变量由应用显式提供；恢复目录必须是新的。成功结果保存到应用的操作记录后，再开展后续步骤。

```python
from infra_artifact_ledger.recovery import create, verify, restore, check_restore

created = create(
    db=source_database,
    output_root=snapshot_root,
    snapshot_id=new_snapshot_id,
    source_commit=verified_source_commit,
)
identity = created["data"]
# 在应用的独立操作记录中持久保存 identity 与 verified_source_commit。

verified = verify(
    snapshot=identity["snapshot_path"],
    expected_manifest_sha256=identity["manifest_sha256"],
    scratch_parent=local_scratch,
)
restored = restore(
    snapshot=identity["snapshot_path"],
    target_dir=new_restore_directory,
    expected_manifest_sha256=identity["manifest_sha256"],
    scratch_parent=local_scratch,
)
checked = check_restore(
    target_dir=new_restore_directory,
    expected_database_sha256=identity["database_sha256"],
    scratch_parent=local_scratch,
)
assert verified["data"]["summary"] == restored["data"]["summary"] == checked["data"]["summary"]
```

正常 `restore` 返回后，`target_dir` 只有 `ledger.sqlite`；再由调用方决定何时使用 A1 `open` 打开它。`check_restore` 必须在业务首次写入前运行；应用写入后，原始数据库整文件摘要通常不再适用。

## 4. CLI 接入

使用该项目 runtime venv 内的 `artifact-ledger`。每次普通调用输出一行 JSON，包含尾随 LF 的总大小不超过 64 KiB；`--help` 输出文本。不同语言用独立 argv 元素启动进程，不拼接 shell 字符串。

下面变量代表调用方已经核对并保存的输入。命令表不生成路径、不初始化源库，也不自动解析前一条输出。

```sh
"$LEDGER_CLI" snapshot create \
  --db "$LEDGER_SOURCE_DB" \
  --output-root "$LEDGER_SNAPSHOT_ROOT" \
  --snapshot-id "$LEDGER_SNAPSHOT_ID" \
  --source-commit "$LEDGER_SOURCE_SHA"

"$LEDGER_CLI" snapshot verify \
  --snapshot "$LEDGER_SNAPSHOT_PATH" \
  --expected-manifest-sha256 "$LEDGER_EXPECTED_MANIFEST_SHA256" \
  --scratch-parent "$LEDGER_SCRATCH_PARENT"

"$LEDGER_CLI" snapshot restore \
  --snapshot "$LEDGER_SNAPSHOT_PATH" \
  --target-dir "$LEDGER_NEW_TARGET_DIR" \
  --expected-manifest-sha256 "$LEDGER_EXPECTED_MANIFEST_SHA256" \
  --scratch-parent "$LEDGER_SCRATCH_PARENT"

"$LEDGER_CLI" snapshot check-restore \
  --target-dir "$LEDGER_NEW_TARGET_DIR" \
  --expected-database-sha256 "$LEDGER_EXPECTED_DATABASE_SHA256" \
  --scratch-parent "$LEDGER_SCRATCH_PARENT"
```

`create` 的输出需先核对 `status=OK`、`publication_state=published`，再保存其 `data` 中的两个摘要与路径，用于后续命令。重复选项、缺失或不适用参数、未知子命令均拒绝；不能用重复选项覆盖前值。

## 5. NAS 保存与读回

NAS 使用显式 storage config。下例只是公开示意，替换为部署者核实的值后另存于部署侧，不把实际账户、内网配置或凭据提交仓库：

```json
{
  "format": "infra-artifact-ledger-storage/v1",
  "profile": "mounted-posix-v1",
  "storage_ref": "example-archive",
  "mount_point": "/mnt/example-nas",
  "mount_root": "/",
  "mount_source": "example-host:/example-export",
  "fs_type": "nfs4",
  "archive_root": "/mnt/example-nas/ledger-synthetic-archive"
}
```

必须恰含这八个字段，所有键和值是普通字符串。API 接受内置 `dict`；CLI `--storage-config FILE` 接受 UTF-8 JSON 文件。配置上限 16 KiB；文件只读取、解析一次，API 在产生文件系统副作用前取得自己的固定副本。复制完成后修改原 dict 不影响本次调用；调用方不得在初始复制期间并发修改它。

`archive_root` 预先存在且位于确切挂载点下。程序核对 mount source、type、point、root、最深挂载以及目录 fd 的 mount ID；挂载消失、变更或出现非预期子挂载时不会回退写入下面的本机目录。不能只根据目录存在或 NAS 能 ping 通判定可用。

在真实能力验收通过后，从本地封存快照发布：

```sh
"$LEDGER_CLI" snapshot publish \
  --snapshot "$LEDGER_LOCAL_SNAPSHOT_PATH" \
  --storage-config "$LEDGER_STORAGE_CONFIG" \
  --expected-manifest-sha256 "$LEDGER_EXPECTED_MANIFEST_SHA256" \
  --scratch-parent "$LEDGER_SCRATCH_PARENT"

"$LEDGER_CLI" snapshot verify \
  --snapshot "$LEDGER_NAS_SNAPSHOT_PATH" \
  --storage-config "$LEDGER_STORAGE_CONFIG" \
  --expected-manifest-sha256 "$LEDGER_EXPECTED_MANIFEST_SHA256" \
  --scratch-parent "$LEDGER_SCRATCH_PARENT"

"$LEDGER_CLI" snapshot restore \
  --snapshot "$LEDGER_NAS_SNAPSHOT_PATH" \
  --storage-config "$LEDGER_STORAGE_CONFIG" \
  --target-dir "$LEDGER_NEW_TARGET_DIR" \
  --expected-manifest-sha256 "$LEDGER_EXPECTED_MANIFEST_SHA256" \
  --scratch-parent "$LEDGER_SCRATCH_PARENT"
```

NAS 快照路径使用 publish 成功结果中的 `snapshot_path`；同一代的三成员字节保持不变。已有 generation 一律拒绝，不论它是否完整。归档数据只复制到本地私有暂存后做 SQLite 验证，不把 NAS 上的 `ledger.sqlite` 作为运行数据库打开。

Python 对应调用为 `publish(snapshot=local_snapshot, storage_config=config, expected_manifest_sha256=expected_hash, scratch_parent=local_scratch)`。NAS 来源的 verify/restore 必须传同一目标配置；本地来源省略。成功的 `durability_scope=filesystem_acknowledged` 表示本次系统调用、同步及核对完成，不等于 NAS 服务端断电恢复已经实测。

## 6. 失败后怎么做

API 领域失败抛出 `RecoveryError`，读取 `code`、`message`、`stage`、`publication_state`；可以调用 `to_envelope(operation)` 得到 CLI 同形结果。缺必需关键字或传未知关键字是 Python `TypeError`。CLI 错误对象位于 `error` 字段，无法确定操作时 `operation=null`。

| publication_state | 调用方处理 |
|---|---|
| `not_published` | 当前操作未公开对象，或已证明公开未发生；仍可能遗留本次专属目录。先检查原身份，不自动清理或同 ID 重做 |
| `published` | 本次公开、同步及最终核对已完成；保存身份与摘要，后续仍要按需要校验 |
| `unknown` | 保留现场、原 ID、摘要和路径；按原路径只读核对，不覆盖、不删除、不重新分配同一目标 |
| `not_applicable` | 只读核对，或既有目标冲突；不判断那个既有对象以前是否发布成功 |

| CLI exit | 错误码 |
|---|---|
| 2 | INVALID_INPUT |
| 3 | UNSUPPORTED_FORMAT / UNSUPPORTED_STORAGE |
| 4 | TARGET_EXISTS |
| 5 | NOT_FOUND / INCOMPLETE_SNAPSHOT |
| 6 | INTEGRITY_FAILURE |
| 7 | RESOURCE_LIMIT |
| 8 | BUSY / TIMEOUT |
| 9 | IO_ERROR / PUBLICATION_UNKNOWN |

成功退出码为 0。300 秒是累计检查点期限，包括本次解析、复制、SQLite 与阶段检查，不是 OS 阻塞 I/O 的硬超时。stdout 断开或进程终止时可能没有完整 JSON；仅看退出码不能推断没有副作用。

publish 响应丢失时，使用先前保存的 manifest 摘要核对原 NAS 路径。restore 响应丢失时，在消费者还未写入目标前用 `check_restore` 和原数据库摘要核对。该核对允许中断留下的私有暂存目录和数据库额外硬链接，但拒绝 SQLite journal/wal/shm 条目，包括断链。它不清理、不同步目标，也不证明过去发布已成功、目录已经整理好或可以立刻交业务使用。

**create 首次响应完全丢失**，调用方可能还没有 manifest 摘要。先确认目录确实属于本次独占调用；归属不清则保留并停止自动采用。确认后，在 64 KiB 上限内读取 manifest 原始字节并计算候选 SHA-256，核对 `snapshot_id` 和 `producer.source_commit`，再将候选摘要传给 verify，检查 marker、数据库及全部 Ledger 语义。此时只确认当前目录自洽，不能声称匹配事前独立摘要、证明精确 T0 或历史调用成功。通过并确认归属后，才保存该摘要用于后续操作；不完整目录保持未解决。

## 7. 验证和复用边界

[A2 运行手册](A2_RUNBOOK.md) 区分源码测试、隔离 wheel、本地实机和真实 NAS 演练。单元测试中的存储替身不构成 ext4/xfs/btrfs 或 NAS 实测；没有真实配置时明确标记 `NOT_RUN`，能力不足标 `BLOCKED`。

每台机器、每个项目分别使用 build/runtime venv，代码、环境和数据分离。后续其他消费者可以安装同一 wheel、选择自己的本地 Ledger、目录与 NAS 配置；无需复制本项目部署目录，也不共享其他项目的虚拟环境。跨实现替换、跨库知识恢复、自动调度、生产切换和归档轮换尚需各自的范围与验收。
