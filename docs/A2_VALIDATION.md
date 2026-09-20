# A2 实现与验证记录

状态：**实现已提交；Cloud 验证通过；A2 完整验收 PARTIAL**。真实 GX10、受支持本地文件系统的安装态正向闭环、真实 NAS 保存/丢失恢复均为 **NOT_RUN**。本文不把单元测试或模拟挂载写成实机通过。

## 1. 授权、源码和制品链

| 项目 | 固定值 |
|---|---|
| 规则 R | `91ac1ad72a423079785725fafd4eb139f5cd7943` |
| 文档 A | `29ae340addde172781aa2199f864dbb25ea29ccc` |
| Owner 决定 B | [A2-OWNER-CLOSURE-20260920-01](decisions/A2_OPENING_DECISION.md) |
| 设计 PR #5 合并提交 | `8cd1f389c8a2b70d4e77abdf76b9237ef47483df` |
| 独立 CLOSED 记录 C | `6772038cc8b12186907ba05acb1a13456fc479ee` |
| 本次实现 D1 | `ca17ca8b8a1a7b4e2186bb1d24d5ee662faa3c87` |
| D1 tree | `d51a3a3dc40c44193b4b8e592ddb4ac316b23e35` |
| wheel | `infra_artifact_ledger-0.2.0a1-py3-none-any.whl` |
| wheel SHA-256 | `223639e788e3bb1c4c225eb278cf33ab54637be1de611e76ebaec97ec33145f2` |
| wheel 字节数 | 65155 |
| 固定执行汇总完成时间 | 2026-09-20T22:16:42.463817+00:00 |

Owner 授权转录见 [A2_OPENING_DECISION](decisions/A2_OPENING_DECISION.md)。C 只记录 CLOSED，D1 直接以 C 为父提交。五份批准的 A2 需求/架构/计划/接口/验收文档字节保持不变；本文补充实际证据，不将其历史 Candidate/NOT_RUN 原文当成新的权限判断。

本记录由后续文档提交加入；上述 wheel 从 D1 构建，不能把后续文档提交 SHA 冒充该 wheel 的构建来源。软件为 `0.2.0a1` alpha；A1 wire/schema 标识不变。未发布 PyPI 包，未配置 NAS 服务，未启用业务库。

## 2. 实际环境与隔离

执行环境为 ChatGPT Work 的 Cloud 容器，Linux 6.18.44 / x86_64 / glibc 2.39。必验 Python **3.11.16**、补充 Python **3.12.14**，两者 SQLite **3.53.1**。build/runtime 分离；构建工具为 pip 25.0.1、setuptools 84.0.0、wheel 0.48.0、packaging 26.3。运行包只依赖标准库。

固定源码构建 wheel，使用 `--no-index --no-deps` 安装到另一个 Python 3.11 venv。安装态命令在 checkout 之外执行，显式 `-I` 并清除 PYTHONPATH/PYTHONHOME；子进程测试跟随实际导入包位置，不强制导入 checkout。实际解释器、模块 site-packages 路径、版本见 [runtime](evidence/2026-09-20-a2-cloud/runtime.log.txt)。源预检辅助进程由同一解释器 `-I` 启动安装包内文件，相关测试在安装包上重跑。

[实际文件系统记录](evidence/2026-09-20-a2-cloud/storage-profile.log.txt) 显示 Cloud 根文件系统是 **overlay，包含 fsync=volatile**，不属于产品支持的 ext4/xfs/btrfs 本地 profile。生产 API 和安装的 console entry 在该文件系统都返回 `UNSUPPORTED_STORAGE/not_published`，退出码 3；测试确认未创建快照、源字节/成员不变。此项是**拒绝行为 PASS**，不是正向部署 PASS。

正向逻辑测试显式模拟挂载分类，仍真实执行 SQLite、逐块读写、硬链接、fsync、独立进程及故障后的状态检查；只称 **LOGIC_ONLY**。测试中 mock 的 NFS 端点不构成真实 NAS 证据。

## 3. 固定 D1 的执行结果

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

## 4. 关键边界与失败链路

- 源头隔离预检在任何暂存创建之前完成；WAL、hot journal、非 UTF-8、坏头、异常子进程和绑定变化都有拒绝/无副作用检查。调用进程原有 A1 未提交事务保持锁；其真实未提交 Artifact 不混入快照。T0 握手检查后续写入不混入固定读视图，backup 后释放源锁，再校验私有副本。
- 两个独立解释器同时争同一 snapshot_id 或恢复目标；精确一个成功，另一为 `TARGET_EXISTS/not_applicable`。恢复竞争使用内容不同的合法快照，最终完整状态属于获胜者，旧代不变。
- create/publish/restore 在真实写入部分字节后注入 EIO/ENOSPC，检查 `not_published`、没有完成 marker/公开恢复文件和原数据不变。marker/link 已尝试后的同步、清理或挂载身份失败保留现场并返回 `PUBLICATION_UNKNOWN/unknown`。正常发布回读已执行；发布后回读失败注入尚未执行。
- 独立子进程在恢复 hardlink 后 `os._exit(73)`，实际留下 nlink=2 和私有暂存。`check_restore` 只读核对成功，目录成员、字节、inode/link 数保持不变；它不宣称清理、过去同步或业务启用已完成。
- 原始路径先逐层 nofollow，再规范化，覆盖合法 `..` 以及 `symlink/../`。scratch 不得位于只读快照/恢复输入内部；配置归档根遵循同一规则。配置原生类型、16 KiB 预算、固定后调用方修改不改变目标都有检查。
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

metadata **16 MiB**、refs **32 MiB**、全部 TEXT **64 MiB**、metadata **50,000 行**、refs **250,000 行**在真实 SQL 预检层执行边界检查（含 UTF-8 中文、ASCII）。这些 quota fixture 有意不保证完整业务语义，只证明预检计量与超限拒绝；**不宣称每个可达合法 metadata/refs 上限均已完成端到端恢复**。T09 因此保持 PARTIAL。JSON 限制另有格式层用例，畸形字段不会因刚好等于解析上限而变成合法对象。

逐用例耗时/RSS 见 [A2 资源日志](evidence/2026-09-20-a2-cloud/a2-resources.log.txt)；RSS 为测试进程累计峰值 KiB，非单个调用峰值或部署内存保证。文件预算 1 GiB 不是 RSS 上限。不同 suite 并行运行，耗时不应作为独占机器性能基准。

## 6. T01–T15 证据映射

以下“逻辑通过”只指列出的 Cloud 用例，不表示整行实机验收全部完成；规范中的全部正反例仍需逐项对照。

| 项目 | 当前证据 | 状态/剩余边界 |
|---|---|---|
| T01 | `test_snapshot_sqlite` + `test_snapshot_recovery`：T0/未提交/完整状态 | 逻辑通过 |
| T02 | 隔离头预检、WAL/sidecar、UTF-16、锁与期限 | 实际 SQLite 用例通过；环境限定本表版本 |
| T03 | `test_snapshot_format/storage/recovery`：成员、链接、JSON、ID | 逻辑通过 |
| T04 | 物理/外键/业务/幂等/Blob/summary 损坏反例 | 逻辑通过 |
| T05 | mountinfo、fd mount_id、端点/root/子挂载与路径替换模型 | 模型通过；真实 NAS 挂载变化 NOT_RUN |
| T06 | 部分复制、marker 发布、正常回读、同步失败后的实际状态 | PARTIAL；发布后回读失败注入、真实 NAS/设备耐久性未验 |
| T07 | `test_snapshot_concurrency` 两独立进程争同 ID | 本地系统调用逻辑通过；真实 NAS 竞争 NOT_RUN |
| T08 | stdout 失败分类、发布后 unknown 与核对 | PARTIAL；create 完全丢响应的部署核对演练未跑 |
| T09 | 实际文件/SQL/行数/JSON 边界，1 GiB 合法封存，403 MB payload | PARTIAL；合法 metadata/refs 极限全链路未声称完成 |
| T10 | 同 fd 复制、字节/hash/路径/挂载变化检查 | 逻辑通过 |
| T11 | 既有目标拒覆盖，内容不同的双进程恢复竞争 | 逻辑通过 |
| T12 | 复制/链接/清理/同步失败，真实进程终止后 nlink=2 只读核对 | 逻辑通过；真实 NAS 失败模型未验 |
| T13 | 全表/全 Blob、旧 key 重放、新请求 | 合成数据逻辑通过 |
| T14 | 五入口、CLI/错误/配置、独立 wheel、辅助子进程、A1 回归 | PARTIAL；受支持本地 FS 安装态正向与 GX10 NOT_RUN |
| T15 | 真 NAS 丢失恢复工具及自身 13 项测试 | 工具已交付；真实演练 NOT_RUN |

## 7. 实机移交与完成条件

本会话没有可调用的 GX10 shell 或已接入的 A2 Local Hand 执行通道；现有 Local Hand 信息不能自动等价为本任务已获机器访问。A2 授权保持有效，本记录不要求重复开工批准，也不新增或修改 Local Hand 准入配置。

下一步按 [A2_RUNBOOK](A2_RUNBOOK.md) 固定 **D1**，在 GX10 创建项目独立 build/runtime 环境，执行源码/资源/安装态验收；再提供部署侧核实的 NAS 配置，在专属合成范围运行 [a2_nas_exercise.py](../tools/acceptance/a2_nas_exercise.py)。工具只在 NAS 独立校验成功后移除自己创建且证明归属的本地可恢复副本，然后由新进程只从 NAS 恢复并逐项核对。

工具不卸载共享盘、不关闭设备、不配置 NAS 服务，也不删除已有项目/业务库。真实挂载丢失/断网须在隔离测试范围留独立证据；工具的 `synthetic_nas_roundtrip` PASS 不代替这些项目。受支持本地文件系统闭环、GX10、真实 NAS、T06/T08/T09 剩余证据未齐前，不宣称 S1–S5 全部验收完成。

没有 GitHub Actions 运行结果、硬件断电、设备缓存、生产切换、冗余备份、A3/A4 或供应商普遍兼容性声明。将来合并时保留 C→D1→证据提交的祖先链，不 squash 掉独立 CLOSED 记录；本轮仅提交 PR，不自动合并。
