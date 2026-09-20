# A2 合法资源边界：构造、实测与总 TEXT 上界

本文补充 [A2 验收矩阵](a2/ACCEPTANCE.md) 的 T09 证据解释，不修改批准的预算或接口。测试代码为 [test_snapshot_semantic_resources.py](../tests/test_snapshot_semantic_resources.py)。已有预检器边界仍有作用；本轮另外证明可达边界上的完整合法 Ledger 能完成恢复。

状态：四组合法边界在 Cloud/Python 3.11 完成开发态实测。**LOGIC_ONLY**：只模拟文件系统类型分类，SQLite、文件内容、挂载/fd 身份、复制、hardlink、fsync 和恢复均实际执行。结果不证明 Cloud overlay 可作为持久目标，不证明 GX10 或真实 NAS 已通过。固定源码、最终制品及归档日志以 [A2_VALIDATION](A2_VALIDATION.md) 的对应执行记录为准。

## 1. 合法输入如何构造

全部库通过公开 A1 `initialize`、`execute` 的 append/import 操作建立。测试不直接插入缺字段记录、不扩大常量、不用额外 schema 或无语义空白凑预算。每个边界点，包括超过 A2 上限的点，先通过 SQLite `integrity_check`、`foreign_key_check` 和 A1 `Ledger.verify()`。

| 维度 | 合法构造 |
|---|---|
| metadata 16 MiB | 公开创建 Artifact，追加三个 Version；每个 Version 的 `external_source_refs` 是唯一、符合 512 字符约束的合法引用。单次请求/单条记录仍在 A1 8 MiB 内，累计记录正文达到 A2 16 MiB。前两个 Version 作为固定基础，第三个的字符串数量及长度按实际规范编码调整。包含中文与 ASCII，以 SQL 的 UTF-8 字节测量确认恰好 −1、等于、+1。 |
| records + operations 50,000 行 | 一次公开 import 批量导入无 Version 的合法 Artifact；这种 Artifact 在 A1 中允许存在。导入另外产生一个 ImportReceipt 和一个成功幂等记录，因此使用 49,997 / 49,998 / 49,999 个 Artifact，得到 49,999 / 50,000 / 50,001 行。避免逐次 create 造成重复全图校验。 |
| refs 250,000 行 | 约 1,000 个 Artifact 加约 250 个合法历史 ImportReceipt。各 Receipt 内引用唯一，不同 Receipt 可以引用相同 Artifact。公开 import 正常建立派生 refs，计入这次新 Receipt 对全部 Artifact 的引用，得到 249,999 / 250,000 / 250,001 行。 |
| refs 32 MiB | 使用 1,000 个 Artifact、248 个全量历史 Receipt、一个含 999 引用的 Receipt、一个含 1 引用的 Receipt，以及本次新 Receipt；合计恰好 250,000 条 refs。Receipt ID 长度均在 StableId 的 256 ASCII 字符以内。调整全量、999 条和单条 Receipt 的 ID 长度，可分别以 1,000 / 999 / 1 字节步长调整 refs TEXT 总量，构造 32 MiB−1、32 MiB、32 MiB+1。 |

历史 Receipt 的内容仅为合成数据，按 A1 导入语义接受；它们不被解释成现实世界的来源真实性证明。创建时间和成功幂等结果由实际公开写入路径保留或产生。

## 2. 独立计量与完整链路

测试直接通过 SQL 计算以下值，不从待测恢复函数的 summary 推算预算：

- `metadata_rows`：`records` 与 `operations` 的行数之和。
- `metadata_bytes`：这两张表的 `data` 列 `length(CAST(data AS BLOB))` 之和。
- `refs_rows`：`refs` 的行数。
- `refs_bytes`：`refs.source_id`、`field`、`target_id` 三列的 UTF-8 字节之和。
- `text_bytes`：固定 schema 五张表全部业务 TEXT 列的 UTF-8 字节之和，包括 `payloads.blob_ref` 和 `ledger_format.profile`。

上述字节含义以数据库编码为 UTF-8 为前提；A2 在业务 TEXT 计量前检查编码。测试创建的库同样是 UTF-8。

对于可接受点，执行公开入口 `create → verify → restore → check_restore`，并检查：

1. 每个入口返回的 summary 与独立 A1 verify 一致。
2. 恢复文件实际 SHA-256 匹配封存数据库摘要。
3. 原库与恢复库的 `ledger_format`、`records`、`payloads`、`operations`、`refs` 五表逐行计算精确值摘要，并逐表比较；不以计数相同替代内容相同。
4. 源数据库文件摘要保持不变，操作暂存最终清空。

对于 +1 点，先证明完整 A1 合法，再要求 A2 返回 `RESOURCE_LIMIT/not_published`；输出根保持为空，源数据库摘要不变。超过 A2 的预算并不意味着源 Ledger 不符合 A1。

## 3. 开发态实际结果

Python **3.11.16**，SQLite **3.53.1**；四个 opt-in 测试含十二个边界场景，共 **128.818 秒**，全部通过。测试进程累计峰值 RSS 为 **234,744 KiB**；该数值包含先前场景的峰值，不是单个函数的峰值，也不是部署内存保证。每次公开操作保留原有 300 秒累计检查点期限，没有扩大时限。

| 预算维度 | 实际值 | A1 完整验证 | A2 结果 |
|---|---:|---|---|
| metadata bytes | 16,777,215 | PASS | 完整链路 PASS |
| metadata bytes | 16,777,216 | PASS | 完整链路 PASS |
| metadata bytes | 16,777,217 | PASS | RESOURCE_LIMIT，源不变、无输出 |
| metadata rows | 49,999 | PASS | 完整链路 PASS |
| metadata rows | 50,000 | PASS | 完整链路 PASS |
| metadata rows | 50,001 | PASS | RESOURCE_LIMIT，源不变、无输出 |
| refs bytes | 33,554,431 | PASS | 完整链路 PASS，同时 250,000 条 refs |
| refs bytes | 33,554,432 | PASS | 完整链路 PASS，同时 250,000 条 refs |
| refs bytes | 33,554,433 | PASS | RESOURCE_LIMIT，源不变、无输出 |
| refs rows | 249,999 | PASS | 完整链路 PASS |
| refs rows | 250,000 | PASS | 完整链路 PASS |
| refs rows | 250,001 | PASS | RESOURCE_LIMIT，源不变、无输出 |

关键交叉约束也实际计量：16 MiB metadata 库仅有 14 条 metadata 行、9 条 refs；50,000 行库的 metadata 为 7,600,136 bytes；32 MiB refs 库的 metadata 为 2,441,176 bytes、1,252 行，总业务 TEXT 为 36,038,972 bytes。因此这些成功点没有用突破另一预算的方法达到目标。

复核命令：

```bash
A2_RESOURCE_TESTS=1 PYTHONPATH=src python3.11 -m unittest discover -s tests -p test_snapshot_semantic_resources.py -v
```

未设置 `A2_RESOURCE_TESTS=1` 时四项跳过。普通 discovery 的 skip 不能作为上述实际边界通过证据；需保留专门执行日志。

## 4. 为什么完整合法库不能独立达到总 TEXT 64 MiB

这不是“尚未找到例子”的假定，而是固定 A1 语义与 A2 同时预算共同给出的严格上界。只讨论完整合法、符合固定 schema、UTF-8 编码的 A1 Ledger。

令：

| 符号 | 精确定义 |
|---|---|
| D | `records.data` 与 `operations.data` 的实际 UTF-8 字节之和，即 metadata bytes |
| R | `refs.source_id`、`refs.field`、`refs.target_id` 的实际 UTF-8 字节之和 |
| N | `records` 与 `operations` 的行数之和 |
| I | `records.id/kind`、`operations.scope/kind/key/result_ref`、`payloads.blob_ref` 的实际 UTF-8 字节之和；不包含 D 或 R |
| T | 全部业务 TEXT 字节之和，即 `D + R + I + 18`；18 是固定 `bounded-local-v0.1` 的 ASCII 字节数 |

**第一步：为 I 中每份字节找到 D 中互不重叠的对应值。**

- 每个 `records.id` 对应其记录 JSON 的 owned ID 值；A1 校验 SQL ID 与该值相同。
- 每个 operation 的四个索引字符串对应同一条 operation JSON 的 `idempotency_scope_ref`、`operation_kind`、`idempotency_key`、`result_ref`，且 SQL 索引与原记录逐项一致。
- `payloads.blob_ref` 集合精确等于 Blob metadata 集合。合法闭包要求每个 Blob 至少被 ContentRoot 或 Manifest entry 引用。为每个不同 Blob 选择一个对应的 `blob_ref` 引用值出现，即可覆盖 payload 索引字节；不同 Blob 选择不同出现，且这些引用字段不与前两项的 owned ID 或 operation 索引字段重叠。复用同一 Blob 不增加 payload 行，故不会要求重复分配。
- `records.kind` 不必逐字出现在 JSON 值中；从该对象必需 JSON 结构开销中支付这段长度即可。

合法 JSON 字符串 token 的原始内容字节不会少于其解码后的 UTF-8 字节。非 canonical 空白、字段顺序和转义不破坏这个下界：空白增加长度，顺序不改变长度，转义不能把这些值压缩得更短；例如 surrogate pair 的 12 个转义 ASCII bytes 解码为 4 个 UTF-8 bytes。

**第二步：每行至少还剩 29 bytes 的结构开销。**

只计算顶层必需键的字节、键引号和冒号、对象括号和逗号，完全不计值及其引号、数组括号或任何嵌套结构；再扣除 `records.kind` 的长度，已经得到以下保守下界：

| 行类型 | 结构开销下界，bytes |
|---|---:|
| Artifact | 52 |
| Version | 145 |
| ContentRoot（blob 型，最小者） | 29 |
| Blob | 58 |
| Manifest | 30 |
| ProvenanceLink | 67 |
| ImportReceipt | 49 |
| operation（不用额外扣 kind） | 115 |

最小值 29 的计算可直接复核：ContentRoot 的必需键为 `content_root_ref`（16）、`kind`（4）、`blob_ref`（8）。三个键的 JSON 结构占 `16 + 4 + 8 + 3×3 + 2 + 2 = 41` bytes，其中每键另有两引号和冒号，另有对象括号及两逗号；扣去 SQL `kind="content_root"` 的 12 bytes 后，仍有 29 bytes。manifest 型 ContentRoot 的键更长。可选字段和嵌套结构只会增加余量。

所以对于 N 行合法 metadata：

\[
I \le D - 29N.
\]

同时满足 A2 的 `D ≤ 16 MiB` 与 `R ≤ 32 MiB` 时：

\[
T = D + R + I + 18
\le 2D + R + 18 - 29N
\le 64\,\text{MiB} + 18 - 29N.
\]

若 `N ≥ 1`，则 `T ≤ 64 MiB − 11 bytes`，严格低于总 TEXT 上限。若 `N = 0`，合法引用与 payload 覆盖要求其余业务集合也为空，只有 18 bytes 的 profile。**因此在其余预算同时满足的前提下，不存在总 TEXT 恰为 64 MiB 的完整合法 A1 库。**

这个证明不允许删除总 TEXT 检查。任意损坏 SQLite、异常索引键或未通过 Ledger 语义校验的输入不受上述合法对象关系约束，仍必须在解码与完整图验证之前接受该预算检查；已有 SQL 预检器 limit−1/= /+1 用例覆盖这一层。

## 5. 证据适用范围

本轮补齐的是合法 metadata/refs 可达边界的恢复链及总 TEXT 的不可独立达到证明。文件大小、累计业务 payload、JSON 预算和预检器畸形输入边界由其他专项分别留证；不将本轮四项当作全部 A2 完成证明。

真实本地文件系统、GX10、NAS 挂载能力、设备缓存和断电耐久性仍按其各自验收记录判断。数学上界只适用于当前固定 schema、字段和闭包语义；未来改变这些条件时必须重新推导。
