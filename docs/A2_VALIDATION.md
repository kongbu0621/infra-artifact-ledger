# A2 实现与验证记录

状态：**实现已提交；Cloud 验证通过；A2 完整验收 PARTIAL**。真实 GX10、受支持本地文件系统的安装态正向闭环、真实 NAS 保存/丢失恢复均为 **NOT_RUN**。本文不把单元测试或模拟挂载写成实机通过。当前源码依据 D3（§3.3）；D1/D2 表格保留为各自固定版本的历史结果。

## 1. 授权、源码和制品链

| 项目 | 固定值 |
|---|---|
| 规则 R | `91ac1ad72a423079785725fafd4eb139f5cd7943` |
| 文档 A | `29ae340addde172781aa2199f864dbb25ea29ccc` |
| Owner 决定 B | [A2-OWNER-CLOSURE-20260920-01](decisions/A2_OPENING_DECISION.md) |
| 设计 PR #5 合并提交 | `8cd1f389c8a2b70d4e77abdf76b9237ef47483df` |
| 独立 CLOSED 记录 C | `6772038cc8b12186907ba05acb1a13456fc479ee` |
| 首轮实现 D1（历史） | `ca17ca8b8a1a7b4e2186bb1d24d5ee662faa3c87` |
| D1 tree | `d51a3a3dc40c44193b4b8e592ddb4ac316b23e35` |
| D1 证据提交 | `f13d2fb23379ed372d0c84b89714fcfaa344f993` |
| 第一轮复审修复 D2（历史） | `47b525ebb0add3a6aebc1cb13119f4046eea42b8` |
| D2 tree | `5f50017d84c883b4486a356489bc33eb14a29993` |
| D2 证据提交 | `f564174644258025288a659c207511936e179fd1` |
| 第二轮复审修复 D3 | `478ce4b39bc82df2bb1f6543de208e54ac100d8f` |
| D3 tree | `b0987df359b23a949e854764824b09acc6eb39be` |

Owner 授权转录见 [A2_OPENING_DECISION](decisions/A2_OPENING_DECISION.md)。C 只记录 CLOSED，D1 直接以 C 为父提交。五份批准的 A2 需求/架构/计划/接口/验收文档字节保持不变；本文补充实际证据，不将其历史 Candidate/NOT_RUN 原文当成新的权限判断。

祖先链为 C → D1 → D1 证据提交 → D2 → `f564174` → D3 → 本轮证据文档提交。D1、D2、D3 的执行结果及 wheel 摘要分别列于下文；不把后续修复后的结果套给旧版本，也不能把后续证据文档提交 SHA 冒充 wheel 的构建来源。软件为 `0.2.0a1` alpha；A1 wire/schema 标识不变。未发布 PyPI 包，未配置 NAS 服务，未启用业务库。

## 2. 实际环境与隔离

执行环境为 ChatGPT Work 的 Cloud 容器，Linux 6.18.44 / x86_64 / glibc 2.39。必验 Python **3.11.16**、补充 Python **3.12.14**，两者 SQLite **3.53.1**。build/runtime 分离；构建工具为 pip 25.0.1、setuptools 84.0.0、wheel 0.48.0、packaging 26.3。运行包只依赖标准库。

固定源码构建 wheel，使用 `--no-index --no-deps` 安装到另一个 Python 3.11 venv。安装态命令在 checkout 之外执行，显式 `-I` 并清除 PYTHONPATH/PYTHONHOME；被测 `infra_artifact_ledger` 从 venv 的 site-packages 导入，子进程跟随该包位置。本轮测试模块和 NAS 验收工具脚本仍来自固定 D3 checkout，**并非 wheel 自带验收脚本**；`-I` 不改变这一点。源预检辅助进程则由同一解释器 `-I` 启动安装包内文件。实际解释器、模块路径、版本见 [D3 runtime](evidence/2026-09-20-a2-review2/runtime.log.txt)；[D2 runtime](evidence/2026-09-20-a2-review1/runtime.log.txt) 与 [D1 runtime](evidence/2026-09-20-a2-cloud/runtime.log.txt) 保留为历史。

[D3 实际文件系统记录](evidence/2026-09-20-a2-review2/storage-profile.log.txt) 显示 Cloud 根文件系统是 **overlay，包含 fsync=volatile**，不属于产品支持的 ext4/xfs/btrfs 本地 profile。生产 API 和安装的 console entry 在该文件系统都返回 `UNSUPPORTED_STORAGE/not_published`，退出码 3；测试确认未创建快照、源字节/成员不变。此项是**安装态拒绝行为 PASS**，不是正向部署 PASS。[D1 文件系统记录](evidence/2026-09-20-a2-cloud/storage-profile.log.txt) 保留为历史。

正向逻辑测试显式模拟挂载分类，仍真实执行 SQLite、逐块读写、硬链接、fsync、独立进程及故障后的状态检查；只称 **LOGIC_ONLY**。测试中 mock 的 NFS 端点不构成真实 NAS 证据。

## 3. 分别固定源码的执行结果

### 3.1 D1 历史结果

D1 wheel 为 `infra_artifact_ledger-0.2.0a1-py3-none-any.whl`，SHA-256 为 `223639e788e3bb1c4c225eb278cf33ab54637be1de611e76ebaec97ec33145f2`，65,155 bytes。汇总完成时间为 `2026-09-20T22:16:42.463817+00:00`。以下结果仅属于 D1，不含本轮复审修复。

| 项目 | 结果 | suite 耗时 | 日志 |
|---|---|---:|---|
| Python 3.11 全量源码 | 308 项发现，292 执行通过，16 跳过 | 52.124s | [source311](evidence/2026-09-20-a2-cloud/source311.log.txt) |
| Python 3.12 全量源码 | 308 项发现，292 执行通过，16 跳过 | 53.748s | [source312](evidence/2026-09-20-a2-cloud/source312.log.txt) |
| A1 实际容量边界 | 8 项发现，8 执行通过，0 跳过 | 97.867s | [a1-resources](evidence/2026-09-20-a2-cloud/a1-resources.log.txt) |
| A1 响应节点边界 | 1 项发现，1 执行通过，0 跳过 | 31.812s | [a1-responses](evidence/2026-09-20-a2-cloud/a1-responses.log.txt) |
| A2 实际资源边界 | 7 项发现，7 执行通过，0 跳过 | 61.166s | [a2-resources](evidence/2026-09-20-a2-cloud/a2-resources.log.txt) |
| 独立 wheel 的 A2 逻辑 | 142 项发现，135 执行通过，7 跳过 | 10.534s | [installed-a2-logic](evidence/2026-09-20-a2-cloud/installed-a2-logic.log.txt) |
| 两版本 compileall | PASS | 见 receipt | [3.11](evidence/2026-09-20-a2-cloud/compile311.log.txt)、[3.12](evidence/2026-09-20-a2-cloud/compile312.log.txt) |
| wheel 构建与隔离安装 | PASS | 见 receipt | [build](evidence/2026-09-20-a2-cloud/build.log.txt)、[install](evidence/2026-09-20-a2-cloud/install.log.txt) |
| 安装态 A1 文档读写闭环 | PASS | 见 receipt | [installed-a1](evidence/2026-09-20-a2-cloud/installed-a1.log.txt) |
| 安装态 A2 不支持文件系统拒绝 | PASS | 见 receipt | [unsupported-storage](evidence/2026-09-20-a2-cloud/installed-unsupported-storage.log.txt) |
| diff 检查 | PASS | 见 receipt | [diff-check](evidence/2026-09-20-a2-cloud/diff-check.log.txt) |

普通 discovery 的 16 项跳过是 9 项 A1 和 7 项 A2 资源专项；它们随后分别单独执行。安装态 A2 discovery 的资源项同样不算执行通过，其实际资源证据是表中单独的 D1 源码 suite。逐命令 exit code、参数、墙钟时间见 [summary.json](evidence/2026-09-20-a2-cloud/summary.json)。suite 时间和进程启动到退出的 receipt 时间是不同测量。

### 3.2 D2 第一轮复审结果（历史）

D2 wheel 为 `infra_artifact_ledger-0.2.0a1-py3-none-any.whl`，SHA-256 为 `5ca6586124b82ed0de67e04200f9a68db8079f2720ef2c99bfc63bb2b1fea994`，65,669 bytes。汇总完成时间为 `2026-09-20T22:53:39.354750+00:00`。17 条验证命令均 exit 0；原始参数与退出状态见 [D2 summary.json](evidence/2026-09-20-a2-review1/summary.json)，归档日志摘要见 [log-index.json](evidence/2026-09-20-a2-review1/log-index.json)。

| 项目 | 结果 | suite 耗时 | 日志 |
|---|---|---:|---|
| Python 3.11 全量源码 | 349 项发现，329 执行通过，20 跳过 | 56.143s | [source311](evidence/2026-09-20-a2-review1/source311.log.txt) |
| Python 3.12 全量源码 | 349 项发现，329 执行通过，20 跳过 | 58.315s | [source312](evidence/2026-09-20-a2-review1/source312.log.txt) |
| A1 实际容量边界 | 8 项发现，8 执行通过，0 跳过 | 98.064s | [a1-resources](evidence/2026-09-20-a2-review1/a1-resources.log.txt) |
| A1 响应节点边界 | 1 项发现，1 执行通过，0 跳过 | 32.231s | [a1-responses](evidence/2026-09-20-a2-review1/a1-responses.log.txt) |
| A2 实际资源边界 | 7 项发现，7 执行通过，0 跳过 | 62.831s | [a2-resources](evidence/2026-09-20-a2-review1/a2-resources.log.txt) |
| A2 合法 metadata/refs 资源边界 | 4 项发现，4 执行通过，0 跳过；含 12 个合法边界点 | 133.397s | [a2-semantic-resources](evidence/2026-09-20-a2-review1/a2-semantic-resources.log.txt) |
| 独立 wheel 的 A2 逻辑 | 183 项发现，172 执行通过，11 跳过 | 13.528s | [installed-a2-logic](evidence/2026-09-20-a2-review1/installed-a2-logic.log.txt) |
| 两版本 compileall | PASS | 见 receipt | [3.11](evidence/2026-09-20-a2-review1/compile311.log.txt)、[3.12](evidence/2026-09-20-a2-review1/compile312.log.txt) |
| wheel 构建与隔离安装 | PASS | 见 receipt | [build](evidence/2026-09-20-a2-review1/build.log.txt)、[install](evidence/2026-09-20-a2-review1/install.log.txt) |
| 安装态 A1 文档读写闭环 | PASS | 见 receipt | [installed-a1](evidence/2026-09-20-a2-review1/installed-a1.log.txt) |
| 安装态 A2 不支持文件系统拒绝 | PASS | 见 receipt | [unsupported-storage](evidence/2026-09-20-a2-review1/installed-unsupported-storage.log.txt) |
| 构建/运行/挂载环境取证 | PASS | 见 receipt | [build-environment](evidence/2026-09-20-a2-review1/build-environment.log.txt)、[runtime](evidence/2026-09-20-a2-review1/runtime.log.txt)、[storage-profile](evidence/2026-09-20-a2-review1/storage-profile.log.txt) |
| D2 提交 diff 检查 | PASS | 见 receipt | [diff-check](evidence/2026-09-20-a2-review1/diff-check.log.txt) |

普通 discovery 的 20 项跳过为 A1 的 8+1 项和 A2 的 7+4 项资源专项；表中四个专项合计 **20 项全部单独执行通过**。安装态逻辑 suite 的 11 项跳过为 A2 的 7+4 项专项，不计入其 172 项通过；专项证据来自固定 D2 源码执行。两种 Python、源码/安装态及专项之间存在重复用例，不能将这些数字相加作为互不重复测试数量。

### 3.3 D3 第二轮复审结果

固定代码 `478ce4b39bc82df2bb1f6543de208e54ac100d8f`；wheel `infra_artifact_ledger-0.2.0a1-py3-none-any.whl`，SHA-256 `ce0ebf6cfb705555c36a1cdf21fe0016df2be3d45fd10238d16f377adbd72ce7`，66,778 bytes。完成时间 `2026-09-20T23:26:55.156419+00:00`。15 条构建、安装、测试、编译及环境命令全部 exit 0；[汇总](evidence/2026-09-20-a2-review2/summary.json)与[日志摘要](evidence/2026-09-20-a2-review2/log-index.json)绑定同一源码/tree。后续证据提交未改运行代码、测试或工具。

| 项目 | 结果 | suite 耗时 | 日志 |
|---|---|---:|---|
| Python 3.11 全量源码 | 388 项发现，368 执行通过，20 跳过 | 58.695s | [source311](evidence/2026-09-20-a2-review2/source311.log.txt) |
| Python 3.12 全量源码 | 388 项发现，368 执行通过，20 跳过 | 61.453s | [source312](evidence/2026-09-20-a2-review2/source312.log.txt) |
| A2 实际资源专项 | 7 项发现，7 执行通过，0 跳过 | 62.259s | [a2-resources](evidence/2026-09-20-a2-review2/a2-resources.log.txt) |
| A2 合法 metadata/refs 边界 | 4 项发现，4 执行通过，0 跳过 | 133.794s | [a2-semantic-resources](evidence/2026-09-20-a2-review2/a2-semantic-resources.log.txt) |
| 独立 wheel 的 A2 逻辑 | 222 项发现，211 执行通过，11 跳过 | 17.080s | [installed-a2-logic](evidence/2026-09-20-a2-review2/installed-a2-logic.log.txt) |
| 两版本编译、wheel 构建、新 venv 安装 | PASS | 见 receipt | [compile311](evidence/2026-09-20-a2-review2/compile311.log.txt)、[compile312](evidence/2026-09-20-a2-review2/compile312.log.txt)、[build](evidence/2026-09-20-a2-review2/build.log.txt)、[install](evidence/2026-09-20-a2-review2/install.log.txt) |
| 安装态 A1 文档闭环、A2 不支持文件系统拒绝 | PASS | 见 receipt | [installed-a1](evidence/2026-09-20-a2-review2/installed-a1.log.txt)、[unsupported-storage](evidence/2026-09-20-a2-review2/installed-unsupported-storage.log.txt) |

新增 **39 项**针对性回归。普通 discovery 的 20 项跳过含 A1 的 8+1 与 A2 的 7+4；本轮 A2 **11 项**资源专项实际重跑通过，仍包含 1 GiB 合法库、403 MB payload 和十二个合法 metadata/refs 边界点。A1 大容量/响应资源专项没有重跑，保留 §3.2 的 D2 证据；A1 源码文件保持不变，普通 A1 回归与安装态文档闭环本轮仍执行。安装态 11 项跳过不计入 211 项通过，跨套件数字也不合计为独立用例数量。

本轮确认并修复的缺陷：

- **源保护**：DELETE 文件头配合残留非空 `-wal`，SQLite `mode=ro` 仍可能创建 32 KiB `-shm`。现于源预检前后及源 SQLite 打开前，仅用 nofollow stat 拒绝所有 `-wal/-shm` 条目；含空文件、断链和目录。正常 DELETE `-journal`/并发事务行为保留。用例核对源及既有 sidecar 字节不变、没有新增成员/输出，也确认不进入 SQLite 打开。不把事后检查宣传为抵抗违反稳定路径/journal 模式前置的任意竞态。
- **复合失败**：已有目标冲突后，目录或暂存关闭 EIO 曾覆盖 `TARGET_EXISTS/not_applicable`。统一保留实际首错、附记关闭失败；没有首错时仍报告关闭错误且不重试已释放 fd。另验证从调用者 `except` 块进入正常操作，不把外层已处理异常误当作本次首错而吞掉关闭失败。
- **发布状态与阶段**：回调已完成公开、同步、最终核对与清理后，报告期限或响应预算失败现在保留 `TIMEOUT`/`RESOURCE_LIMIT` 与 `published/report`；回调未完成的发布后失败仍为 `PUBLICATION_UNKNOWN/unknown`。create/restore 的实际 API 与 CLI 均有回归；新增组合故障用例确认部分 stdout 后失败不会追加第二份 envelope。目录绑定检查保留实际 sync/publish 等阶段，不落回 validate。
- **路径和挂载文本**：Linux `//` 根路径写法不再导致合法路径误拒；保留后续全部 `..` 与逐层 nofollow。mountinfo 按实际 ASCII LF/space 解码，保留 Unicode 空白及 CR 字段，不因文本读取的换行转换丢失挂载绑定。此类 mountinfo 用例是模型验证，并非真实特殊挂载验收。
- **NAS 演练归属**：工具修复初始化/遍历 fd 泄漏及重复关闭，保留原始父路径，逐层冻结 dev/inode/mount；被 `..` 返回的前序目录也参与核对，替换后停止删除。报告、inventory 与关闭使用本函数局部错误状态，不受调用者外层 except 影响。全部使用合成目录，未执行真实 NAS。

对应回归：`test_snapshot_review2_sqlite`（5）、`test_snapshot_review2_storage`（6）、`test_snapshot_review2_protocol`（15）、`test_snapshot_review2_nas`（12）、`test_snapshot_review2_mount_text`（1）。证据范围仍为 LOGIC_ONLY。没有新增协议字段、没有修改五份批准规范、没有解除实机验收条件。

## 4. 关键边界与失败链路

- 源头隔离预检在任何暂存创建之前完成；WAL、hot journal、非 UTF-8、坏头、异常子进程和绑定变化都有拒绝/无副作用检查。调用进程原有 A1 未提交事务保持锁；其真实未提交 Artifact 不混入快照。T0 握手检查后续写入不混入固定读视图，backup 后释放源锁，再校验私有副本。
- 两个独立解释器同时争同一 snapshot_id 或恢复目标；精确一个成功，另一为 `TARGET_EXISTS/not_applicable`。恢复竞争使用内容不同的合法快照，最终完整状态属于获胜者，旧代不变。
- create/publish/restore 在真实写入部分字节后注入 EIO/ENOSPC，检查 `not_published`、没有完成 marker/公开恢复文件和原数据不变。marker/link 已尝试后的同步、清理或挂载身份失败保留现场并返回 `PUBLICATION_UNKNOWN/unknown`。D2 补充 T06 的四个 LOGIC_ONLY 用例：create marker 发布后，分别对 DB/manifest 回读注入 EIO 或摘要变化。四者都返回 unknown 并保留已发布代；故障解除后的只读 verify 接受完整代、拒绝被改写代，不修改现场。
- D2 补充 T08 的五个 LOGIC_ONLY 用例：独立 create 子进程丢失全部响应后，仅在调用者能确认专属代归属、原始 snapshot_id/source_commit 和完整自洽性时记录当前核对结果；不完整代、source_commit 不匹配、归属未确认、manifest 超限均不接纳、不覆盖重试。新算出的摘要只证明当前专属代自洽，不冒充事前独立摘要或过去 create 成功的证明。四项回读和五项丢响应见 `test_snapshot_publication_review`；实际部署 T06/T08 仍未执行。
- 独立子进程在恢复 hardlink 后 `os._exit(73)`，实际留下 nlink=2 和私有暂存。`check_restore` 只读核对成功，目录成员、字节、inode/link 数保持不变；它不宣称清理、过去同步或业务启用已完成。
- 原始路径先逐层 nofollow，再规范化，覆盖合法 `..` 以及 `symlink/../`。scratch 不得位于只读快照/恢复输入内部；配置归档根遵循同一规则。配置原生类型、16 KiB 预算、固定后调用方修改不改变目标都有检查。
- D2 修复 fd 关闭失败时的归属转移与重复关闭风险，保留首要异常并释放其余自有 fd；绑定、路径检查和 fsync 失败按实际阶段分类。CLI 显式 `null` 配置不能退回本地模式，异常诊断先限制内容规模。NAS 验收工具的报告写入或最后 close 失败保留原操作错误；已有报告位置和 `evidence_saved` 状态随错误保留，相关三项 close 回归包含在 D2。本轮进一步补齐的复合失败与外层异常场景见 §3.3。
- SQL 五表逐行、所有 Blob、版本分支、Manifest、来源关系、导入 Receipt 和幂等结果对账；重放不增记录，新的合成写入只发生在恢复测试库。

这些是已执行的具体失败模型，不是所有系统调用时序或真实断电耐久性的穷尽证明。

## 5. 实际资源边界

合法业务 payload **402,653,185 bytes**（384 MiB+1），SQLite 文件 **403,103,744 bytes**。A1 `export_bundle()` 按自身预算返回 `RESOURCE_LIMIT/not_applicable`；A2 create→verify→restore→check_restore、逐 Blob 对账、幂等重放成功。

另用 SQLite 自身分配并释放临时 padding 表，恢复精确 A1 schema，保留合法 freelist 页，真实构造封存文件边界；未伪造 header 或以稀疏扩展冒充合法库：

| 文件字节数 | 4096-byte 页数 | 结果 |
|---:|---:|---|
| 1,073,737,728 | 262,143 | 完整快照/校验/恢复/核对成功 |
| 1,073,741,824 | 262,144 | 同链路成功，达到 1 GiB 上限 |
| 1,073,750,016 | 262,146 | SQLite integrity/FK/A1 语义校验成功；A2 超限拒绝且输出根为空 |

第 262,145 页覆盖 SQLite 保留 lock-byte；本 SQL 分配构造的首个超限点为 +8192，而非 +4096。依据 [SQLite 文件格式 §1.4](https://www.sqlite.org/fileformat.html#the_lock_byte_page)。文件字节 limit−1/= /+1 另在 stat 预检层检查，不能把这些稀疏 fixture 写成合法 SQLite 成功证据。

metadata **16 MiB**、refs **32 MiB**、全部 TEXT **64 MiB**、metadata **50,000 行**、refs **250,000 行**的 SQL 预检层边界仍保留（含 UTF-8 中文、ASCII）。这些预检 fixture 有意不保证完整业务语义，只证明计量与超限拒绝，不能代替合法业务恢复链。

D2 另以 A1 公共 API 构造四组完整合法库，共 **12 个 limit−1/= /+1 点**：metadata 字节、metadata 行数、refs 行数、refs 字节。每个点先通过 SQLite integrity/FK 和 A1 语义核验；限内/等限执行 create→verify→restore→check_restore 并核对完整表摘要，超限点拒绝且源数据不变、输出根为空。本轮重跑结果见 [D3 a2-semantic-resources](evidence/2026-09-20-a2-review2/a2-semantic-resources.log.txt)，构造与范围见 [A2_RESOURCE_BOUNDARIES](A2_RESOURCE_BOUNDARIES.md)。这补齐 T09 可达合法 metadata/refs 边界的 LOGIC_ONLY 恢复链。

总 TEXT **64 MiB** 在其他预算同时满足时不存在完整合法等限库：[同文证明](A2_RESOURCE_BOUNDARIES.md#4-为什么完整合法库不能独立达到总-text-64-mib) 给出 `T ≤ 64 MiB + 18 − 29N`；有 metadata 时 `N ≥ 1`，故严格小于上限；空库仅 18 bytes。此结论不允许删除总 TEXT 检查，损坏输入的 limit−1/= /+1 仍由预检器覆盖。JSON 限制另有格式层用例，畸形字段不会因刚好等于解析上限而变成合法对象。

本轮逐用例耗时/RSS 见 [D3 A2 资源日志](evidence/2026-09-20-a2-review2/a2-resources.log.txt)及上述 D3 语义资源日志；[D2](evidence/2026-09-20-a2-review1/a2-resources.log.txt) 与 [D1](evidence/2026-09-20-a2-cloud/a2-resources.log.txt) 资源日志保留为历史。RSS 为测试进程累计峰值 KiB，非单个调用峰值或部署内存保证。文件预算 1 GiB 不是 RSS 上限。不同 suite 并行运行，耗时不应作为独占机器性能基准。

## 6. T01–T15 证据映射

以下“逻辑通过”只指列出的 Cloud 用例，不表示整行实机验收全部完成；规范中的全部正反例仍需逐项对照。

| 项目 | 当前证据 | 状态/剩余边界 |
|---|---|---|
| T01 | `test_snapshot_sqlite` + `test_snapshot_recovery`：T0/未提交/完整状态 | 逻辑通过 |
| T02 | 隔离头预检、DELETE 头部残留 WAL/SHM 拒绝、UTF-16、锁与期限 | 实际 SQLite 用例通过；环境限定本表版本 |
| T03 | `test_snapshot_format/storage/recovery`：成员、链接、JSON、ID | 逻辑通过 |
| T04 | 物理/外键/业务/幂等/Blob/summary 损坏反例 | 逻辑通过 |
| T05 | mountinfo、fd mount_id、端点/root/子挂载与路径替换模型 | 模型通过；真实 NAS 挂载变化 NOT_RUN |
| T06 | 部分复制、marker 发布、同步失败，新增 DB/manifest 发布后 EIO/摘要变化四项 | LOGIC_ONLY 通过；真实部署/NAS/设备耐久性 NOT_RUN |
| T07 | `test_snapshot_concurrency` 两独立进程争同 ID | 本地系统调用逻辑通过；真实 NAS 竞争 NOT_RUN |
| T08 | stdout/丢响应核对，未完成发布的 unknown 与已完成发布后的 report 失败保留 published | LOGIC_ONLY 通过；真实部署丢响应核对 NOT_RUN |
| T09 | 文件/SQL/JSON 边界、1 GiB 合法封存、403 MB payload、四组合法库 12 点及 TEXT 上界证明 | 已列资源链 LOGIC_ONLY 通过；不形成部署资源/耐久性认证 |
| T10 | 同 fd 复制、字节/hash/路径/挂载变化检查 | 逻辑通过 |
| T11 | 既有目标拒覆盖，内容不同的双进程恢复竞争 | 逻辑通过 |
| T12 | 复制/链接/清理/同步失败，真实进程终止后 nlink=2 只读核对 | 逻辑通过；真实 NAS 失败模型未验 |
| T13 | 全表/全 Blob、旧 key 重放、新请求 | 合成数据逻辑通过 |
| T14 | 五入口、CLI/错误/配置、独立 wheel、辅助子进程、A1 回归 | PARTIAL；受支持本地 FS 安装态正向与 GX10 NOT_RUN |
| T15 | NAS 丢失恢复工具及归属清单、wheel、错误保留等工具回归 | 工具已交付并有逻辑回归；真实演练 NOT_RUN |

## 7. 实机移交与完成条件

本会话没有可调用的 GX10 shell 或已接入的 A2 Local Hand 执行通道；现有 Local Hand 信息不能自动等价为本任务已获机器访问。A2 授权保持有效，本记录不要求重复开工批准，也不新增或修改 Local Hand 准入配置。

下一步按 [A2_RUNBOOK](A2_RUNBOOK.md) 固定 **D3**，在 GX10 创建项目独立 build/runtime 环境，执行源码/资源/安装态验收；再提供部署侧核实的 NAS 配置，在专属合成范围运行 [a2_nas_exercise.py](../tools/acceptance/a2_nas_exercise.py)。工具先构造并关闭合成库和快照，在 NAS 操作之前冻结精确全树、inode/挂载、常规文件 nlink 与内容摘要清单；NAS 能力预检、发布和独立校验成功后，再次逐项核对，才移除归属可证明的本地可恢复副本。期间新增文件、替换或改写均阻止删除。新进程仅从 NAS 恢复并逐项核对，不能依靠已删除的本地源库或快照回退；这些仍是待实机执行的步骤。

工具对同一打开 fd 计算 wheel 摘要并核对安装包字节，但 `--source-commit` 是调用者声明，不能凭 wheel 字节匹配证明 Git 来源；必须另存固定 checkout、构建日志、wheel 摘要、安装日志和实际模块路径的构建链。报告 `evidence_saved=false` 表示未确认报告已可靠记录，不表示操作没有副作用，也不证明报告路径不存在。完整说明见运行手册。

工具不卸载共享盘、不关闭设备、不配置 NAS 服务，也不删除已有项目/业务库。真实挂载丢失/断网须在隔离测试范围留独立证据；工具的 `synthetic_nas_roundtrip` PASS 不代替这些项目。受支持本地文件系统闭环、GX10、真实 NAS、T06/T08 实际部署证据未齐前，不宣称 S1–S5 全部验收完成。

没有 GitHub Actions 运行结果、硬件断电、设备缓存、生产切换、冗余备份、A3/A4 或供应商普遍兼容性声明。将来合并时保留 C→D1→`f13d2fb`→D2→`f564174`→D3→本轮证据提交的祖先链，不 squash 掉独立 CLOSED 记录；本轮仅提交 PR，不自动合并。
