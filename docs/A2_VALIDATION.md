# A2 实现与验证记录

状态：**实现已提交；Cloud 验证通过；A2 完整验收 PARTIAL**。真实 GX10、受支持本地文件系统的安装态正向闭环、真实 NAS 保存/丢失恢复均为 **NOT_RUN**。本文不把单元测试或模拟挂载写成实机通过。当前源码依据 D8（§3.8）；D1–D7 保留为各自固定版本的历史记录，后续结果不追授给旧提交。

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
| 第二轮复审修复 D3（历史） | `478ce4b39bc82df2bb1f6543de208e54ac100d8f` |
| D3 tree | `b0987df359b23a949e854764824b09acc6eb39be` |
| D3 证据提交 | `14f68c70e59cf1bb1935c6a121f115447c37fd0d` |
| 第三轮复审修复 D4（历史） | `8a11030fd03f4d0bfb291786d1c557ba8fc94f89` |
| D4 tree | `f6eee546a33e3a4093ab603c35caa662ab1260b6` |
| 第四轮修复 D5（历史） | `8506574083a39339cb63d472c89703b4affaf4e8` |
| D5 tree | `76728c8d5cdd8d5777cab01bc654652f2a9e9118` |
| D5 证据提交 | `689b2d0e82f2d15b4e2bb49c91a0cf7047e6f296` |
| 第五轮修复 D6（历史） | `894cab8b1d55840bbc0cf130c22d4f57d67875d3` |
| D6 tree | `5f8bfb606ce1d9e1ef84b9f4d96c46192045f81e` |
| D6 证据提交 | `073057c2d1a72e12c669f1c4ee5fe3d0930e701a` |
| 第六轮修复 D7（历史） | `4af226210586f61bfb4e4c604b23edd6194fef59` |
| D7 tree | `1123371884fbd6507bcb67e495e1bd9fa31db8c9` |
| D7 证据提交 | `0de4e57345f6211cbd2f22e01994ab073c9f7344` |
| 本轮修复 D8 | `c9e4cb450c853578b1b4c15eab4a9a6458693d8e` |
| D8 tree | `5eacace3f61b81909c9b14646742d5102cd5fa00` |

Owner 授权转录见 [A2_OPENING_DECISION](decisions/A2_OPENING_DECISION.md)。C 只记录 CLOSED，D1 直接以 C 为父提交。五份批准的 A2 需求/架构/计划/接口/验收文档字节保持不变；本文补充实际证据，不将其历史 Candidate/NOT_RUN 原文当成新的权限判断。

祖先链为 C → D1 → `f13d2fb` → D2 → `f564174` → D3 → `14f68c7` → D4 → D5 → `689b2d0` → D6 → `073057c` → D7 → `0de4e57` → D8 → 本轮证据文档提交。各固定源码的执行结果及 wheel 摘要分别列于下文；不把后续修复后的结果套给旧版本，也不能把后续证据文档提交 SHA 冒充 wheel 的构建来源。软件为 `0.2.0a1` alpha；A1 wire/schema 标识不变。未发布 PyPI 包，未配置 NAS 服务，未启用业务库。

## 2. 实际环境与隔离

本轮执行环境为 ChatGPT Work 的 Cloud 容器，Linux 6.18.44 / x86_64 / glibc 2.39。必验 Python **3.11.16**、补充 Python **3.12.14**，两者 SQLite **3.53.1**。构建复用 D5 已记录的 build venv，使用 `system-site-packages` 继承本环境已有的构建工具，实测为 pip 25.0.1、setuptools 84.0.0、wheel 0.48.0、packaging 26.3；它不是完全隔离的依赖解析环境。Python 3.11 安装态 runtime venv 本轮独立新建；补充 Python 3.12 源码与编译验证复用此前 runtime venv。运行包只依赖标准库。实际版本见 [build-environment](evidence/2026-09-21-a2-review7/build-environment.log.txt) 与 [runtime](evidence/2026-09-21-a2-review7/runtime.log.txt)。

本地源文件集由固定 D7 证据提交 `0de4e57` 的文件集与本轮变更形成，先创建 D8 提交对象，再于构建前逐文件计算 Git blob 并与 D8 的远端 tree 对照，构建后复核同一文件集的 SHA-256；这是一份按固定 tree 核验的源文件集，**不是本地 Git checkout**。本轮核验 215 个文件，并核对 17 个运行包模块在源码、wheel 与已安装包中的字节一致；范围和逐项结果见 [source-integrity.json](evidence/2026-09-21-a2-review7/source-integrity.json)。构建和测试以这份固定内容为输入，后续证据文档修改不作为 wheel 的构建来源。

从固定源码以 `--no-index --no-build-isolation --no-deps` 构建 wheel，再以 `--no-index --no-deps` 安装到独立 Python 3.11 runtime venv。安装态命令在源码目录之外执行，显式 `-I` 并清除 PYTHONPATH/PYTHONHOME；被测 `infra_artifact_ledger` 来自 runtime venv 的 site-packages。测试模块和 NAS 验收工具脚本来自 D8 源文件集，**并非 wheel 自带验收脚本**；`-I` 不改变这一点。源预检辅助进程由同一解释器 `-I` 启动安装包内文件。[D5 runtime](evidence/2026-09-21-a2-review4/runtime.log.txt)、[D3 runtime](evidence/2026-09-20-a2-review2/runtime.log.txt)、[D2 runtime](evidence/2026-09-20-a2-review1/runtime.log.txt) 与 [D1 runtime](evidence/2026-09-20-a2-cloud/runtime.log.txt) 保留为历史。

[D8 实际文件系统记录](evidence/2026-09-21-a2-review7/storage-profile.log.txt) 显示 Cloud 根文件系统是 **overlay，包含 fsync=volatile**，不属于产品支持的 ext4/xfs/btrfs 本地 profile。生产 API 和安装的 console entry 在该文件系统都返回 `UNSUPPORTED_STORAGE/not_published`，退出码 3；测试确认未创建快照、源字节/成员不变。此项是**安装态拒绝行为 PASS**，不是正向部署 PASS，见 [installed-unsupported-storage](evidence/2026-09-21-a2-review7/installed-unsupported-storage.log.txt)。此前 [D7](evidence/2026-09-21-a2-review6/storage-profile.log.txt)、[D6](evidence/2026-09-21-a2-review5/storage-profile.log.txt)、[D5](evidence/2026-09-21-a2-review4/storage-profile.log.txt)、[D3](evidence/2026-09-20-a2-review2/storage-profile.log.txt) 与 [D1](evidence/2026-09-20-a2-cloud/storage-profile.log.txt) 文件系统记录保留为历史。

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

### 3.3 D3 第二轮复审结果（历史）

固定代码 `478ce4b39bc82df2bb1f6543de208e54ac100d8f`；wheel `infra_artifact_ledger-0.2.0a1-py3-none-any.whl`，SHA-256 `ce0ebf6cfb705555c36a1cdf21fe0016df2be3d45fd10238d16f377adbd72ce7`，66,778 bytes。完成时间 `2026-09-20T23:26:55.156419+00:00`。15 条构建、安装、测试、编译及环境命令全部 exit 0；[汇总](evidence/2026-09-20-a2-review2/summary.json)与[日志摘要](evidence/2026-09-20-a2-review2/log-index.json)绑定同一源码/tree。紧随 D3 的证据提交 `14f68c70e59cf1bb1935c6a121f115447c37fd0d` 未改运行代码、测试或工具；之后 D4–D6 的实现变更由下文分别记录。

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

### 3.4 D4 第三轮复审修复（历史）

固定代码 `8a11030fd03f4d0bfb291786d1c557ba8fc94f89`，tree `f6eee546a33e3a4093ab603c35caa662ab1260b6`，父提交为 D3 证据提交 `14f68c7`。D4 补齐恢复文件、CLI 配置读取和源头辅助进程清理中的首错保留；NAS 合成 fixture 加入多父合并及内容复用，恢复后在原 Artifact 上追加版本与 payload，核对父引用、字节和计数。这些落实既有验收矩阵要求，不改变五份批准规范。

D4 未单列公开的完整构建与验收证据集；不能把 D3 的 wheel、测试结果或日志套给它，也不能据此断言 D4 从未测试。本轮 D5 继承这些修复，并在下述固定来源上重新构建和验证。

### 3.5 D5 第四轮复审与证据收口（历史）

固定代码 `8506574083a39339cb63d472c89703b4affaf4e8`，tree `76728c8d5cdd8d5777cab01bc654652f2a9e9118`，直接以 D4 为父提交。wheel 为 `infra_artifact_ledger-0.2.0a1-py3-none-any.whl`，SHA-256 `643ecc7228faf5043d11a7d453710a15fbfb02983dd10c79bc34b88fb039fd92`，67,638 bytes。验证日期为 2026-09-21 UTC；14 条构建、安装、测试、编译与环境取证命令全部 exit 0。来源核验、构建、安装、解释器路径与日志形成同一条证据链，见 [summary.json](evidence/2026-09-21-a2-review4/summary.json)、[log-index.json](evidence/2026-09-21-a2-review4/log-index.json) 与 [source-integrity.json](evidence/2026-09-21-a2-review4/source-integrity.json)。

| 项目 | 结果 | suite 耗时 | 日志 |
|---|---|---:|---|
| Python 3.11 全量源码 | 424 项发现，404 执行通过，20 跳过 | 60.967s | [source311](evidence/2026-09-21-a2-review4/source311.log.txt) |
| Python 3.12 全量源码 | 424 项发现，404 执行通过，20 跳过 | 63.440s | [source312](evidence/2026-09-21-a2-review4/source312.log.txt) |
| A2 实际资源专项 | 7 项发现，7 执行通过，0 跳过 | 61.606s | [a2-resources](evidence/2026-09-21-a2-review4/a2-resources.log.txt) |
| A2 合法 metadata/refs 边界 | 4 项发现，4 执行通过，0 跳过 | 130.216s | [a2-semantic-resources](evidence/2026-09-21-a2-review4/a2-semantic-resources.log.txt) |
| 独立 wheel 的 A2 逻辑 | 258 项发现，247 执行通过，11 跳过 | 18.070s | [installed-a2-logic](evidence/2026-09-21-a2-review4/installed-a2-logic.log.txt) |
| 两版本 compileall | PASS | 见 receipt | [compile311](evidence/2026-09-21-a2-review4/compile311.log.txt)、[compile312](evidence/2026-09-21-a2-review4/compile312.log.txt) |
| wheel 构建、独立 runtime 安装 | PASS | 见 receipt | [build](evidence/2026-09-21-a2-review4/build.log.txt)、[install](evidence/2026-09-21-a2-review4/install.log.txt) |
| 安装态 A1 文档闭环、A2 不支持文件系统拒绝 | PASS | 见 receipt | [installed-a1](evidence/2026-09-21-a2-review4/installed-a1.log.txt)、[unsupported-storage](evidence/2026-09-21-a2-review4/installed-unsupported-storage.log.txt) |

D5 相比 D4 新增 **14 项**针对性回归：`test_snapshot_output_channels` 11 项，`test_snapshot_probe_binding` 3 项。普通 discovery 的 20 项跳过仍为 A1 的 8+1 与 A2 的 7+4 资源专项；安装态 A2 的 11 项专项跳过不计入 247 项通过。本轮 A2 的 7+4=11 项资源专项全部单独执行通过，包含 1 GiB 合法封存库、超过 portable 预算的 payload 和十二个合法 metadata/refs 边界点。A1 大容量与响应节点专项本轮未重跑，保留 D2 历史证据；本轮 A1 普通回归与安装态文档闭环已执行。不同解释器、安装态和专项存在重复用例，不能相加成独立用例数。

- **CLI 真实输出通道失败**：D4 在关闭读端的 pipe、`/dev/full` 等真实子进程场景中，原本准备返回 9，却因解释器退出时再次刷新失败变成 120；启动时缺失 stdout 另有未处理异常。D5 避免把本次响应留在标准文件缓冲中，处理短写、不可用的 stdout/stderr，并保持调用者流对象与已有有效输出顺序。回归覆盖缓冲/无缓冲进程、真实部分输出、完整单行响应、嵌入调用及自定义流；已处理的响应失败保持退出码 9，不追加第二份 envelope。此结论不保证修复调用者事先留下的损坏缓冲，也不保证强制终止后的退出码；没有完整响应时仍按原身份、摘要和路径核对副作用。
- **NAS 竞争探针的目录绑定加固**：D4 在首次目录检查后发生路径替换时，子进程按绝对路径 mkdir，可能在替换目录留下新空目录；最终检查会报错，没有假 PASS。本次将父目录 fd 传给两个子进程，mkdir/stat 始终相对同一固定目录；替换后仍拒绝成功，不向替换目录写入。新增正常竞争、检查后替换和第二个子进程启动失败三例，核对实际目录及进程回收。这是工具加固，不扩大抵御任意管理员并发修改的承诺，不代表真实 NAS 挂载丢失已验。
- **同一回归的 RED → GREEN**：CLI 11 项最终回归在 D4 上记录 `failures=9, errors=1`，在 D5 上全部通过；失败统计包含 subtest，不能称作 10 个独立失败用例。NAS 新增三项在 D4 上一项失败，修复后三项通过。旧实现、测试文件摘要、命令和日志定位见 [regression-summary.json](evidence/2026-09-21-a2-review4/regression-summary.json)；这些对照证明具体修复，不替代全量回归或实机验收。

没有新增协议字段、没有修改 A1 wire/schema 或五份批准规范，也未改变 R/A/scope。GX10、受支持本地文件系统安装态正向、真实 NAS 与 T06/T08 实际部署演练仍 NOT_RUN，整体 PARTIAL。

### 3.6 D6 第五轮复审与固定源码验证（历史）

固定代码 `894cab8b1d55840bbc0cf130c22d4f57d67875d3`，tree `5f8bfb606ce1d9e1ef84b9f4d96c46192045f81e`，父提交为 D5 证据提交 `689b2d0e82f2d15b4e2bb49c91a0cf7047e6f296`。wheel 为 `infra_artifact_ledger-0.2.0a1-py3-none-any.whl`，SHA-256 `3d65460945ae24c91ea86ebe5c79e7e779acae6d083dbe89cc634b7c0a163217`，67,823 bytes。验证日期为 2026-09-21 UTC；14 条构建、安装、测试、编译与环境取证命令全部 exit 0。固定来源、构建、安装与逐命令结果见 [summary.json](evidence/2026-09-21-a2-review5/summary.json)、[log-index.json](evidence/2026-09-21-a2-review5/log-index.json) 与 [source-integrity.json](evidence/2026-09-21-a2-review5/source-integrity.json)。

| 项目 | 结果 | suite 耗时 | 日志 |
|---|---|---:|---|
| Python 3.11 全量源码 | 429 项发现，409 执行通过，20 跳过 | 61.648s | [source311](evidence/2026-09-21-a2-review5/source311.log.txt) |
| Python 3.12 全量源码 | 429 项发现，409 执行通过，20 跳过 | 65.101s | [source312](evidence/2026-09-21-a2-review5/source312.log.txt) |
| A2 实际资源专项 | 7 项发现，7 执行通过，0 跳过 | 62.219s | [a2-resources](evidence/2026-09-21-a2-review5/a2-resources.log.txt) |
| A2 合法 metadata/refs 边界 | 4 项发现，4 执行通过，0 跳过 | 130.498s | [a2-semantic-resources](evidence/2026-09-21-a2-review5/a2-semantic-resources.log.txt) |
| 独立 wheel 的 A2 逻辑 | 263 项发现，252 执行通过，11 跳过 | 18.474s | [installed-a2-logic](evidence/2026-09-21-a2-review5/installed-a2-logic.log.txt) |
| 两版本 compileall | PASS | 见 receipt | [compile311](evidence/2026-09-21-a2-review5/compile311.log.txt)、[compile312](evidence/2026-09-21-a2-review5/compile312.log.txt) |
| wheel 构建、新 runtime 安装 | PASS | 见 receipt | [build](evidence/2026-09-21-a2-review5/build.log.txt)、[install](evidence/2026-09-21-a2-review5/install.log.txt) |
| 安装态 A1 文档闭环、A2 不支持文件系统拒绝 | PASS | 见 receipt | [installed-a1](evidence/2026-09-21-a2-review5/installed-a1.log.txt)、[unsupported-storage](evidence/2026-09-21-a2-review5/installed-unsupported-storage.log.txt) |

D6 新增 **5 项**回归：`test_snapshot_error_stages` 新增 2 项，`test_snapshot_source_journal` 新增 3 项。普通 discovery 的 20 项跳过仍为 A1 的 8+1 与 A2 的 7+4 资源专项；安装态 11 项资源跳过不计入 252 项通过。本轮 A2 的 7+4=11 项资源专项全部单独执行通过，包含 1 GiB 合法封存库、超过 portable 预算的 payload 和十二个合法 metadata/refs 边界点。A1 大容量/响应节点专项本轮未重跑，保留 D2 历史证据；A1 普通回归与安装态文档闭环本轮已执行。跨解释器、源码/安装态与定向实验有重复，不能合计为独立用例数。

- **最终回读错误阶段**：create 的封存发布路径和 restore 已完成最终同步、开始回读时，若数据库 hash 读取发生 I/O 错误，D5 的操作上下文仍停在 `sync`。D6 在两处进入最终回读前设置 `verify` 检查点。两项新增用例注入实际文件读 EIO，确认结果为 `PUBLICATION_UNKNOWN/verify/unknown`、已公开对象保留、源库不变；故障撤去后 verify/check_restore 可核对完整内容。修复准确标记发生阶段，不将已公开后的失败改成 not_published，也不据回读证明过去同步或断电耐久性。
- **源 journal 的打开前防护**：D6 只用 nofollow stat 检查 `-journal` 条目。非普通文件或 link count 不为 1 时，以 `INTEGRITY_FAILURE` 在源 SQLite 打开前拒绝；不打开/关闭该 sidecar，不固定正常 DELETE journal 的 inode，也不阻止正常事务创建/移除单链接 journal。预检前后及使用已缓存预检结果、即将连接源库时均检查。普通单链接 journal 只保留进入后续预检的资格，hot journal 仍按原合同拒绝自动恢复。
- **回归与证据范围**：以下 RED/GREEN 是提交前的组件对照，固定 D6 全量验证单列于上表。同一阶段 suite 在 D5 实现上 3 项中 2 项失败、D6 全部通过，其中 1 项为既有回归。journal 的最终 3 项测试对照未改动的 D5 `snapshot_sqlite.py`，不是对完整 D5 固定树重跑全套；旧实现记录 `failures=6`（含 subtest），修复后 3 项通过；不能把它写成 6 个独立失败用例。journal 测试显式用哨兵阻断 header/SQLite 连接边界，检验异常条目能否更早被拒绝，并检查源字节、条目状态与输出目录；旧实现也不会对异常 journal 运行实际 SQLite。**这些测试证明提前拒绝，不是实际锁失效或跨数据库影响的重演证据。** 对照来源和日志见 [regression-summary.json](evidence/2026-09-21-a2-review5/regression-summary.json)。文件系统正向分类仍为 LOGIC_ONLY。

软件版本仍为 `0.2.0a1`；A1 wire/schema、A2 协议字段、五份批准规范及 R/A/scope 均未改变。GX10、可靠本地文件系统安装态正向、真实 NAS 和 T06/T08 实际部署仍 NOT_RUN，A2 完整验收保持 PARTIAL。

### 3.7 D7 第六轮复审与配额错误分类（历史）

固定实现 `4af226210586f61bfb4e4c604b23edd6194fef59`，tree `1123371884fbd6507bcb67e495e1bd9fa31db8c9`，父提交为 D6 证据提交 `073057c2d1a72e12c669f1c4ee5fe3d0930e701a`。本轮仅修改 `recovery.py` 的本地链接错误分类和 `test_snapshot_recovery.py`，五份批准规范保持原字节。固定源码构建、测试和日志见 [本轮证据](evidence/2026-09-21-a2-review6/README.md)、[summary.json](evidence/2026-09-21-a2-review6/summary.json)。

本地 hardlink 因 `EDQUOT`（磁盘配额耗尽）失败时，D6 漏将它归入确定没有创建公开对象的错误集合，导致 create/restore 返回 `PUBLICATION_UNKNOWN/unknown`。D7 按既有接口保留 `IO_ERROR/publish/not_published`；CLI 仍为退出码 9。该判断限定为已识别的本地路径，NAS 错误仍不能据 errno 推断未发布。语义依据为 [Linux link 错误与 NFS 限制](https://man7.org/linux/man-pages/man2/link.2.html)和 [POSIX link 失败语义](https://man7.org/linux/man-pages/man3/link.3p.html)。

扩展已有 3 项测试、新增 1 项 API 实际调用的 CLI 回归：验证本地 create/restore 无公开 marker/恢复文件、源字节保持，API/CLI 的代码、阶段与状态一致；另在模拟远端先实际创建链接再抛 EDQUOT，确认仍为 unknown、目标保留且可只读核验。相同最终测试文件选择 4 项，在修复前出现 4 个失败记录（含 CLI 两个 subtest），修复后 4 项通过；不能称作 4 个独立失败用例。原始与公开日志摘要、源码及测试摘要见 [regression-summary.json](evidence/2026-09-21-a2-review6/regression-summary.json)和 [log-index.json](evidence/2026-09-21-a2-review6/log-index.json)。这是 errno 注入的 LOGIC_ONLY 证据，没有配置或耗尽真实文件系统配额。

| 验证 | 发现 | 通过 | 跳过 | suite 耗时 |
|---|---:|---:|---:|---:|
| Python 3.11.16 源码 | 430 | 410 | 20 | 59.818s |
| Python 3.12.14 源码 | 430 | 410 | 20 | 62.337s |
| 独立安装态 A2 | 264 | 253 | 11 | 见日志 |

12 条构建、安装、测试、编译和环境命令均 exit 0；两版 compileall、A1 安装态文档闭环和实际 overlay 的生产 API/CLI 拒绝通过。源码中的 20 项资源专项（A1 9、A2 11）本轮全部未重跑，安装态 11 项为同一批 A2 专项；保留 D2 的 A1、D6 的 A2 历史证据，不将它们记作 D7 本轮通过。运行代码只增加一个 errno 分类，资源预算、SQL、复制、挂载与同步代码均未修改。不同套件数量不相加。

wheel `infra_artifact_ledger-0.2.0a1-py3-none-any.whl` 为 **67,832 bytes**，SHA-256 **`a54bd1b75238a3a75184001c33e6b46dd021c50e0f0a00b8e0118b455e73a181`**。构建前后 195 个固定源文件核验一致，17 个运行模块在源码、wheel、新独立安装环境中一致，见 [source-integrity.json](evidence/2026-09-21-a2-review6/source-integrity.json)。证据文档在构建验证完成后单独提交，不冒充构建源提交。

本轮重看五入口的状态优先级、journal 检查前后边界、CLI 错误输出、远端发布歧义及固定源码到安装包链路；在这些检查中确认上述一项错误分类遗漏，未另确认新缺陷。这不是穷尽所有时序或真实设备的证明。受支持本地文件系统正向、真实配额耗尽、GX10、真实 NAS 与挂载丢失/断网仍 NOT_RUN，A2 整体 PARTIAL，PR 保持 Draft。

### 3.8 D8 第七轮复审与验收子进程清理

固定实现 `c9e4cb450c853578b1b4c15eab4a9a6458693d8e`，tree `5eacace3f61b81909c9b14646742d5102cd5fa00`，父提交为 D7 证据提交 `0de4e57345f6211cbd2f22e01994ab073c9f7344`。只修改 `tools/acceptance/a2_nas_exercise.py` 并增加 `tests/test_snapshot_process_cleanup.py`；产品运行模块、接口、预算和五份批准规范均未改变。

本轮确认 NAS 验收工具的子进程收尾缺陷：`child_cli` 和 `concurrent_directory_probe` 在原操作已失败后，wait/管道关闭等二次错误会覆盖首错、中断后续管道/其他子进程清理；`child_cli` 的命令记录也可能因此缺失。D8 统一逐项尝试终止、回收和关闭，保留首错及二次失败提示；没有首错时仍报告清理失败。处理 poll/kill 之间进程已退出的情况，不重试已经关闭的管道，并在清理出错时仍保存该次命令记录。此变更不保证操作系统本身无法完成 kill/wait/close 时资源一定释放，也不新增 OS 阻塞 I/O 硬超时承诺。

新增 6 项实际合成子进程回归：异常响应叠加 stdout/stderr/wait 失败；正常响应叠加清理失败；调用者已有已处理异常；第二个子进程启动失败；两个子进程中第一个清理失败；读取失败叠加 poll/kill 退出竞态。核对原错误、进程退出、管道关闭、不重复关闭及命令记录。相同最终测试文件在 D7 工具上产生 10 个 failure 记录（含 subtest，不是 10 个独立测试方法），D8 的 6 项全部通过。故障主要在实际回收/关闭后注入，没有执行 NAS 操作或触及业务数据。 [修复前后记录](evidence/2026-09-21-a2-review7/regression-summary.json)保存最终测试/工具摘要、命令与原始日志映射。

| 验证 | 发现 | 通过 | 跳过 |
|---|---:|---:|---:|
| Python 3.11.16 源码 | 436 | 416 | 20 |
| Python 3.12.14 源码 | 436 | 416 | 20 |
| 独立安装态 A2 | 270 | 259 | 11 |

12 条固定源码构建、安装、测试、编译和环境命令全部 exit 0。两版本 compileall、A1 安装态文档闭环、生产 API/CLI 的实际 overlay 拒绝均通过。20 项资源专项本轮未重跑：A1 的 9 项保留 D2 历史，A2 的 11 项保留 D6 历史；安装态 11 项跳过是同一批 A2 专项。历史结果不算作 D8 本轮执行，不相加不同版本和安装态的重复用例。 逐命令结果见 [summary.json](evidence/2026-09-21-a2-review7/summary.json)。

本轮 wheel `infra_artifact_ledger-0.2.0a1-py3-none-any.whl` 为 67,832 bytes，SHA-256 `c88541e1c29e9f19eb78127baee03d19f45ef4b5397b0011f302e970de583531`。构建前后 215 个固定源文件核验一致，17 个运行模块在源码、wheel、新独立 Python 3.11 runtime venv 中一致。17 个运行模块与 D7 完全相同；修复位于仓库内的验收工具，不在 wheel 中，复跑必须同时取得固定 D8 的工具文件。 见 [source-integrity.json](evidence/2026-09-21-a2-review7/source-integrity.json)。14 份公开日志的原始/脱敏摘要分开记录于 [log-index.json](evidence/2026-09-21-a2-review7/log-index.json)。

本轮重看格式预算、源只读视图、发布状态、核验/清理、恢复完整状态和制品来源，未另确认新缺陷；不宣称穷尽时序。受支持本地文件系统正向、GX10、真实 NAS 与实际挂载丢失/断网仍 NOT_RUN，A2 整体 PARTIAL。

## 4. 关键边界与失败链路

- 源头隔离预检在任何暂存创建之前完成；WAL、异常 journal 条目、hot journal、非 UTF-8、坏头、异常子进程和绑定变化都有拒绝/无副作用检查。D6 的 journal 检查只用 nofollow stat，非普通文件或 link count 不为 1 时在 SQLite 打开前拒绝；普通单链接 DELETE journal 仍可进入后续检查，hot journal 不因此获准自动恢复。调用进程原有 A1 未提交事务保持锁；其真实未提交 Artifact 不混入快照。T0 握手检查后续写入不混入固定读视图，backup 后释放源锁，再校验私有副本。
- 两个独立解释器同时争同一 snapshot_id 或恢复目标；精确一个成功，另一为 `TARGET_EXISTS/not_applicable`。恢复竞争使用内容不同的合法快照，最终完整状态属于获胜者，旧代不变。
- create/publish/restore 在真实写入部分字节后注入 EIO/ENOSPC，检查 `not_published`、没有完成 marker/公开恢复文件和原数据不变。marker/link 已尝试后的同步、清理或挂载身份失败保留现场并返回 `PUBLICATION_UNKNOWN/unknown`。D2 补充 T06 的四个 LOGIC_ONLY 用例：create marker 发布后，分别对 DB/manifest 回读注入 EIO 或摘要变化。四者都返回 unknown 并保留已发布代；故障解除后的只读 verify 接受完整代、拒绝被改写代，不修改现场。
- D2 补充 T08 的五个 LOGIC_ONLY 用例：独立 create 子进程丢失全部响应后，仅在调用者能确认专属代归属、原始 snapshot_id/source_commit 和完整自洽性时记录当前核对结果；不完整代、source_commit 不匹配、归属未确认、manifest 超限均不接纳、不覆盖重试。新算出的摘要只证明当前专属代自洽，不冒充事前独立摘要或过去 create 成功的证明。四项回读和五项丢响应见 `test_snapshot_publication_review`；实际部署 T06/T08 仍未执行。
- 独立子进程在恢复 hardlink 后 `os._exit(73)`，实际留下 nlink=2 和私有暂存。`check_restore` 只读核对成功，目录成员、字节、inode/link 数保持不变；它不宣称清理、过去同步或业务启用已完成。
- 原始路径先逐层 nofollow，再规范化，覆盖合法 `..` 以及 `symlink/../`。scratch 不得位于只读快照/恢复输入内部；配置归档根遵循同一规则。配置原生类型、16 KiB 预算、固定后调用方修改不改变目标都有检查。
- D2 修复 fd 关闭失败时的归属转移与重复关闭风险，保留首要异常并释放其余自有 fd；绑定、路径检查和 fsync 失败按实际阶段分类。CLI 显式 `null` 配置不能退回本地模式，异常诊断先限制内容规模。NAS 验收工具的报告写入或最后 close 失败保留原操作错误；已有报告位置和 `evidence_saved` 状态随错误保留，相关三项 close 回归包含在 D2。D3/D4 补齐的复合失败与外层异常场景见 §3.3–3.4；D5 输出通道与探针目录绑定修复见 §3.5，D6 最终回读阶段与源 journal 预检补齐见 §3.6。
- SQL 五表逐行、所有 Blob、版本分支与多父合并、内容复用、Manifest、来源关系、导入 Receipt 和幂等结果对账；重放不增记录，在恢复测试库的原 Artifact 上追加新版本及 payload，并核对父引用、字节和精确计数增量。

这些是已执行的具体失败模型，不是所有系统调用时序或真实断电耐久性的穷尽证明。

## 5. 实际资源边界

本节资源执行结果最近固定于 D6；D7/D8 均未重跑，不将历史结果改写为后续版本执行记录。

合法业务 payload **402,653,185 bytes**（384 MiB+1），SQLite 文件 **403,103,744 bytes**。A1 `export_bundle()` 按自身预算返回 `RESOURCE_LIMIT/not_applicable`；A2 create→verify→restore→check_restore、逐 Blob 对账、幂等重放成功。

另用 SQLite 自身分配并释放临时 padding 表，恢复精确 A1 schema，保留合法 freelist 页，真实构造封存文件边界；未伪造 header 或以稀疏扩展冒充合法库：

| 文件字节数 | 4096-byte 页数 | 结果 |
|---:|---:|---|
| 1,073,737,728 | 262,143 | 完整快照/校验/恢复/核对成功 |
| 1,073,741,824 | 262,144 | 同链路成功，达到 1 GiB 上限 |
| 1,073,750,016 | 262,146 | SQLite integrity/FK/A1 语义校验成功；A2 超限拒绝且输出根为空 |

第 262,145 页覆盖 SQLite 保留 lock-byte；本 SQL 分配构造的首个超限点为 +8192，而非 +4096。依据 [SQLite 文件格式 §1.4](https://www.sqlite.org/fileformat.html#the_lock_byte_page)。文件字节 limit−1/= /+1 另在 stat 预检层检查，不能把这些稀疏 fixture 写成合法 SQLite 成功证据。

metadata **16 MiB**、refs **32 MiB**、全部 TEXT **64 MiB**、metadata **50,000 行**、refs **250,000 行**的 SQL 预检层边界仍保留（含 UTF-8 中文、ASCII）。这些预检 fixture 有意不保证完整业务语义，只证明计量与超限拒绝，不能代替合法业务恢复链。

D2 另以 A1 公共 API 构造四组完整合法库，共 **12 个 limit−1/= /+1 点**：metadata 字节、metadata 行数、refs 行数、refs 字节。每个点先通过 SQLite integrity/FK 和 A1 语义核验；限内/等限执行 create→verify→restore→check_restore 并核对完整表摘要，超限点拒绝且源数据不变、输出根为空。D6 重跑结果见 [D6 a2-semantic-resources](evidence/2026-09-21-a2-review5/a2-semantic-resources.log.txt)，构造与范围见 [A2_RESOURCE_BOUNDARIES](A2_RESOURCE_BOUNDARIES.md)。这补齐 T09 可达合法 metadata/refs 边界的 LOGIC_ONLY 恢复链。

总 TEXT **64 MiB** 在其他预算同时满足时不存在完整合法等限库：[同文证明](A2_RESOURCE_BOUNDARIES.md#4-为什么完整合法库不能独立达到总-text-64-mib) 给出 `T ≤ 64 MiB + 18 − 29N`；有 metadata 时 `N ≥ 1`，故严格小于上限；空库仅 18 bytes。此结论不允许删除总 TEXT 检查，损坏输入的 limit−1/= /+1 仍由预检器覆盖。JSON 限制另有格式层用例，畸形字段不会因刚好等于解析上限而变成合法对象。

D6 逐用例耗时/RSS 见 [D6 A2 资源日志](evidence/2026-09-21-a2-review5/a2-resources.log.txt)及上述 D6 语义资源日志；[D5](evidence/2026-09-21-a2-review4/a2-resources.log.txt)、[D3](evidence/2026-09-20-a2-review2/a2-resources.log.txt)、[D2](evidence/2026-09-20-a2-review1/a2-resources.log.txt) 与 [D1](evidence/2026-09-20-a2-cloud/a2-resources.log.txt) 资源日志保留为历史。RSS 为测试进程累计峰值 KiB，非单个调用峰值或部署内存保证。文件预算 1 GiB 不是 RSS 上限。不同 suite 并行运行，耗时不应作为独占机器性能基准。

## 6. T01–T15 证据映射

以下“逻辑通过”只指列出的 Cloud 用例，不表示整行实机验收全部完成；规范中的全部正反例仍需逐项对照。

| 项目 | 当前证据 | 状态/剩余边界 |
|---|---|---|
| T01 | `test_snapshot_sqlite` + `test_snapshot_recovery`：T0/未提交/完整状态 | 逻辑通过 |
| T02 | 隔离头预检、DELETE 头部残留 WAL/SHM 拒绝、异常 journal 的打开前拒绝、UTF-16、锁与期限 | SQLite/预检逻辑通过；journal 反例未进入 SQLite；环境限定本表版本 |
| T03 | `test_snapshot_format/storage/recovery`：成员、链接、JSON、ID | 逻辑通过 |
| T04 | 物理/外键/业务/幂等/Blob/summary 损坏反例 | 逻辑通过 |
| T05 | mountinfo、fd mount_id、端点/root/子挂载与路径替换模型 | 模型通过；真实 NAS 挂载变化 NOT_RUN |
| T06 | 部分复制、marker 发布、同步失败、DB/manifest 发布后 EIO/摘要变化及最终回读 verify 阶段 | LOGIC_ONLY 通过；真实部署/NAS/设备耐久性 NOT_RUN |
| T07 | `test_snapshot_concurrency` 两独立进程争同 ID；工具竞争探针固定目录 fd 并检查替换后状态 | 本地系统调用逻辑通过；真实 NAS 竞争 NOT_RUN |
| T08 | stdout/丢响应核对、真实子进程通道故障；未完成发布的 unknown 与已完成发布后的 report 失败保留 published | LOGIC_ONLY 通过；真实部署丢响应核对 NOT_RUN |
| T09 | 文件/SQL/JSON 边界、1 GiB 合法封存、403 MB payload、四组合法库 12 点及 TEXT 上界证明 | 已列资源链 LOGIC_ONLY 通过；不形成部署资源/耐久性认证 |
| T10 | 同 fd 复制、字节/hash/路径/挂载变化检查 | 逻辑通过 |
| T11 | 既有目标拒覆盖，内容不同的双进程恢复竞争 | 逻辑通过 |
| T12 | 复制/链接/清理/同步/最终回读失败，真实进程终止后 nlink=2 只读核对 | 逻辑通过；真实 NAS 失败模型未验 |
| T13 | 全表/全 Blob、多父合并与内容复用、旧 key 重放、恢复后追加版本及 payload | 合成数据逻辑通过 |
| T14 | 五入口、CLI/错误/配置、真实进程输出失败退出码、独立 wheel、辅助子进程、A1 回归 | PARTIAL；受支持本地 FS 安装态正向与 GX10 NOT_RUN |
| T15 | NAS 丢失恢复工具及归属清单、wheel、错误保留、竞争探针目录绑定和追加版本回归 | 工具已交付并有逻辑回归；真实演练 NOT_RUN |

## 7. 实机移交与完成条件

本会话没有可调用的 GX10 shell 或已接入的 A2 Local Hand 执行通道；现有 Local Hand 信息不能自动等价为本任务已获机器访问。A2 授权保持有效，本记录不要求重复开工批准，也不新增或修改 Local Hand 准入配置。

下一步按 [A2_RUNBOOK](A2_RUNBOOK.md) 固定 **D8 `c9e4cb450c853578b1b4c15eab4a9a6458693d8e`**，在 GX10 创建项目独立 build/runtime 环境，执行源码/资源/安装态验收；再提供部署侧核实的 NAS 配置，在专属合成范围运行 [a2_nas_exercise.py](../tools/acceptance/a2_nas_exercise.py)。工具先构造并关闭合成库和快照，在 NAS 操作之前冻结精确全树、inode/挂载、常规文件 nlink 与内容摘要清单；NAS 能力预检、发布和独立校验成功后，再次逐项核对，才移除归属可证明的本地可恢复副本。期间新增文件、替换或改写均阻止删除。新进程仅从 NAS 恢复并逐项核对，不能依靠已删除的本地源库或快照回退；这些仍是待实机执行的步骤。

工具对同一打开 fd 计算 wheel 摘要并核对安装包字节，但 `--source-commit` 是调用者声明，不能凭 wheel 字节匹配证明 Git 来源；必须另存固定 checkout、构建日志、wheel 摘要、安装日志和实际模块路径的构建链。报告 `evidence_saved=false` 表示未确认报告已可靠记录，不表示操作没有副作用，也不证明报告路径不存在。完整说明见运行手册。

工具不卸载共享盘、不关闭设备、不配置 NAS 服务，也不删除已有项目/业务库。真实挂载丢失/断网须在隔离测试范围留独立证据；工具的 `synthetic_nas_roundtrip` PASS 不代替这些项目。受支持本地文件系统闭环、GX10、真实 NAS、T06/T08 实际部署证据未齐前，不宣称 S1–S5 全部验收完成。

没有 GitHub Actions 运行结果、硬件断电、设备缓存、生产切换、冗余备份、A3/A4 或供应商普遍兼容性声明。将来合并时保留 C→D1→`f13d2fb`→D2→`f564174`→D3→`14f68c7`→D4→D5→`689b2d0`→D6→`073057c`→D7→`0de4e57`→D8→本轮证据提交的祖先链，不 squash 掉独立 CLOSED 记录；本轮仅提交 PR，不自动合并。
