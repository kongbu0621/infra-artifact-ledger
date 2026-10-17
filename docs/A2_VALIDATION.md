# A2 实现与验证记录

状态：**实现已提交；所列 Cloud、本地安装态、GX10 + CIFS 的真实 NAS 合成恢复与发布故障/冲突场景通过；A2 完整验收仍为 PARTIAL**。2026-10-16 的本地闭环见 §8–§9。2026-10-17 的本地 CLI 响应丢失见 §10，真实 NAS 恢复见 §11，九种发布故障/冲突见 §12，十八个短写、空间/权限/回读及进程中断场景见 §13；NAS 输入恢复端的 15 个恢复与 7 个核对场景见 §14。当前接续按累计矩阵核对 T09 部署 RSS/耗时与 T11 目标冲突的证据缺口。真实容量/配额/ACL 故障、断网、设备断电和资源峰值未由本轮验证。§1–§5、§7 保留此前 Cloud 复核与移交记录，§6 是更新至本次的累计证据映射。D9 运行代码见 §3.9，第九轮固定输入见 §3.10。历史记录中的环境与 NOT_RUN 描述属于相应轮次，后续结果不追授给旧提交。

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
| 第七轮修复 D8（历史） | `c9e4cb450c853578b1b4c15eab4a9a6458693d8e` |
| D8 tree | `5eacace3f61b81909c9b14646742d5102cd5fa00` |
| D8 证据提交 | `a7f7b278160d5a9951dbaa2944421b99464e7d87` |
| 最近修复 D9 | `928b3e0338e7460b9916bf27fc832ce5697b1b7a` |
| D9 tree | `e77d8b1e2f99f2bb05473503a63ef19a6df9716c` |
| D9 第八轮证据 / 本轮固定输入 | `6bd6acfbe5c35d581891eb87275e1173e17848fc` |
| 本轮固定输入 tree | `6dd4da925cb97c22eca78a0a3f2165310e048db9` |

Owner 授权转录见 [A2_OPENING_DECISION](decisions/A2_OPENING_DECISION.md)。C 只记录 CLOSED，D1 直接以 C 为父提交。五份批准的 A2 需求/架构/计划/接口/验收文档字节保持不变；本文补充实际证据，不将其历史 Candidate/NOT_RUN 原文当成新的权限判断。

祖先链为 C → D1 → `f13d2fb` → D2 → `f564174` → D3 → `14f68c7` → D4 → D5 → `689b2d0` → D6 → `073057c` → D7 → `0de4e57` → D8 → `a7f7b27` → D9 → `6bd6acf` → 本轮复核文档提交。各固定源码的执行结果及 wheel 摘要分别列于下文；不把后续修复后的结果套给旧版本，也不能把后续证据文档提交 SHA 冒充 wheel 的构建来源。软件为 `0.2.0a1` alpha；A1 wire/schema 标识不变。未发布 PyPI 包，未配置 NAS 服务，未启用业务库。

## 2. 实际环境与隔离

本轮执行环境为 ChatGPT Work 的 Cloud 容器，Linux 6.18.44 / x86_64 / glibc 2.39。必验 Python **3.11.16**、补充 Python **3.12.14**，两者 SQLite **3.53.1**。构建复用 D5 已记录的 build venv，使用 `system-site-packages` 继承本环境已有的构建工具，实测为 pip 25.0.1、setuptools 84.0.0、wheel 0.48.0、packaging 26.3；它不是完全隔离的依赖解析环境。Python 3.11 安装态 runtime venv 本轮独立新建；补充 Python 3.12 源码与编译验证复用此前 runtime venv。运行包只依赖标准库。实际版本见 [build-environment](evidence/2026-09-21-a2-review9/build-environment.log.txt) 与 [runtime](evidence/2026-09-21-a2-review9/runtime.log.txt)。

本轮使用固定提交 `6bd6acfbe5c35d581891eb87275e1173e17848fc` 的源文件集（D9 与其第八轮证据），构建前逐文件计算 Git blob 并与远端 tree `6dd4da925cb97c22eca78a0a3f2165310e048db9` 对照，构建后复核同一文件集的 SHA-256；**不是本地 Git checkout**。本轮核验 254 个文件，并核对 17 个运行模块在源码、wheel 与已安装包中的字节一致；见 [source-integrity.json](evidence/2026-09-21-a2-review9/source-integrity.json)。产品代码、测试、工具、构建配置未改；后续复核文档提交不是本轮构建来源。

从固定源码以 `--no-index --no-build-isolation --no-deps` 构建 wheel，再以 `--no-index --no-deps` 安装到独立 Python 3.11 runtime venv。安装态命令在源码目录之外执行，显式 `-I` 并清除 PYTHONPATH/PYTHONHOME；被测 `infra_artifact_ledger` 来自 runtime venv 的 site-packages。测试模块和 NAS 验收工具脚本来自 D9 源文件集，**并非 wheel 自带验收脚本**；`-I` 不改变这一点。源预检辅助进程由同一解释器 `-I` 启动安装包内文件。[D5 runtime](evidence/2026-09-21-a2-review4/runtime.log.txt)、[D3 runtime](evidence/2026-09-20-a2-review2/runtime.log.txt)、[D2 runtime](evidence/2026-09-20-a2-review1/runtime.log.txt) 与 [D1 runtime](evidence/2026-09-20-a2-cloud/runtime.log.txt) 保留为历史。

[D9 第九轮实际文件系统记录](evidence/2026-09-21-a2-review9/storage-profile.log.txt) 显示 Cloud 根文件系统是 **overlay，包含 fsync=volatile**，不属于产品支持的 ext4/xfs/btrfs 本地 profile。生产 API 和安装的 console entry 在该文件系统都返回 `UNSUPPORTED_STORAGE/not_published`，退出码 3；测试确认未创建快照、源字节/成员不变。此项是**安装态拒绝行为 PASS**，不是正向部署 PASS，见 [installed-unsupported-storage](evidence/2026-09-21-a2-review9/installed-unsupported-storage.log.txt)。此前 [D9 第八轮](evidence/2026-09-21-a2-review8/storage-profile.log.txt)、[D8](evidence/2026-09-21-a2-review7/storage-profile.log.txt)、[D7](evidence/2026-09-21-a2-review6/storage-profile.log.txt)、[D6](evidence/2026-09-21-a2-review5/storage-profile.log.txt)、[D5](evidence/2026-09-21-a2-review4/storage-profile.log.txt)、[D3](evidence/2026-09-20-a2-review2/storage-profile.log.txt) 与 [D1](evidence/2026-09-20-a2-cloud/storage-profile.log.txt) 文件系统记录保留为历史。

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

### 3.8 D8 第七轮复审与验收子进程清理（历史）

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

### 3.9 D9 第八轮复审与子进程 JSON 证据

固定修订 `928b3e0338e7460b9916bf27fc832ce5697b1b7a`，tree `e77d8b1e2f99f2bb05473503a63ef19a6df9716c`，父提交 `a7f7b278160d5a9951dbaa2944421b99464e7d87`。变更限于 `tools/acceptance/a2_nas_exercise.py` 与新增 `tests/test_snapshot_child_protocol.py`。

NAS 验收工具原先用普通 json.loads 解析子进程响应，会接受 BOM、UTF-16、重复字段、非有限数、孤立 surrogate 及超过 A2 深度/节点预算的 JSON；深层嵌套又可能在 finally 记录阶段再次触发 RecursionError，覆盖原始命令失败并丢掉记录。D9 复用既有 A2 严格解析器，成功或拒绝均缓存，不重复解析失败内容；异常输出保留原始摘要和命令记录，合法错误 envelope 仍可记录。解析失败转成工具自身的 ExerciseError，不携带可能误导的 not_published 判断。产品 JSON 规则和预算没有改变。

新增 6 项真实合成子进程回归，覆盖异常编码/值、重复键、深度与节点超限、深层 JSON、非零退出叠加坏输出、合法错误 envelope，以及解析失败叠加清理失败。修复前同一最终测试文件记录 failures=8、errors=3（含 subtest，不是 11 个独立方法），修复后 6 项通过。深度 8、1,024 个节点及含 LF 共 65,536 bytes 三个 JSON/外层 envelope 层边界正例通过；其中合成 data 只用于解析预算验证，不作为完整业务响应的语义证据。 修复前后命令、最终测试与工具摘要见 [regression-summary.json](evidence/2026-09-21-a2-review8/regression-summary.json)。子进程退出和管道关闭仍有核对，D8 的清理与首错回归继续通过。没有执行真实 NAS。

| 验证 | 发现 | 通过 | 跳过 |
|---|---:|---:|---:|
| Python 3.11.16 源码 | 442 | 422 | 20 |
| Python 3.12.14 源码 | 442 | 422 | 20 |
| 独立安装态 A2 | 276 | 265 | 11 |

12 条固定源码构建、安装、测试、编译和环境命令全部 exit 0。两版本 compileall、A1 安装态闭环、生产 API/CLI 对实际 overlay 的拒绝均通过。20 项资源专项本轮未重跑：A1 9 项保留 D2 历史，A2 11 项保留 D6 历史；安装态跳过的是同一批 A2 专项。历史结果不记作 D9 执行，不相加不同版本/安装态的重复用例。 详见 [summary.json](evidence/2026-09-21-a2-review8/summary.json)。

本轮 wheel 为 67,832 bytes，SHA-256 `def4036d928d8e941d38d2205bccb118be6a87af0a4246b6d415211533957e7b`。构建前后 235 个固定源文件核验一致，17 个运行模块在源码、wheel、新独立 Python 3.11 runtime venv 中一致。17 个产品运行模块与 D8 相同；修复的验收工具不在 wheel 内，复跑需同时取得固定 D9 工具。 来源核验见 [source-integrity.json](evidence/2026-09-21-a2-review8/source-integrity.json)，14 份日志的原始/脱敏摘要见 [log-index.json](evidence/2026-09-21-a2-review8/log-index.json)。

当前检查中未另确认新缺陷；不构成所有时序的穷尽证明。五份批准规范、产品接口和授权范围原字节保留；受支持本地文件系统正向、GX10、真实 NAS 与实际挂载丢失/断网仍 NOT_RUN，A2 整体 PARTIAL。

### 3.10 D9 第九轮复核与资源边界重验

固定输入 `6bd6acfbe5c35d581891eb87275e1173e17848fc`，tree `6dd4da925cb97c22eca78a0a3f2165310e048db9`；运行代码仍为 D9 `928b3e0338e7460b9916bf27fc832ce5697b1b7a`，本轮归档提交只更新文档与证据。

本轮未确认新缺陷，产品代码、测试、验收工具和构建配置均未修改；运行修订仍为 D9，不新增实现修订号。重新审查严格 JSON、源只读视图与 sidecar、路径/挂载绑定、拒覆盖发布、错误优先级、清理、恢复完整状态及证据来源。没有运行此前被阻断的锁失效重演。 审查角度与实际回归映射见 [review-map.json](evidence/2026-09-21-a2-review9/review-map.json)。

| 验证 | 发现 | 通过 | 跳过 |
|---|---:|---:|---:|
| Python 3.11.16 源码 | 442 | 422 | 20 |
| Python 3.12.14 源码 | 442 | 422 | 20 |
| 独立安装态 A2 | 276 | 265 | 11 |
| A2 实际资源边界专项 | 7 | 7 | 0 |
| A2 合法大库语义边界专项 | 4 | 4 | 0 |

14 条固定源码构建、安装、测试、编译和环境命令全部 exit 0。两版本 compileall、A1 安装态闭环与生产 API/CLI 对实际 overlay 的拒绝通过。常规源码的 20 项跳过由 A1 9 项与 A2 11 项组成；本轮另以原始常量显式执行并通过 A2 全部 11 项资源专项，A1 9 项仍保留 D2 历史。安装态 11 项为同一批 A2 专项，专项在源码态执行，不冒称安装态专项。跨版本/安装态结果不相加。 逐命令、退出码与耗时见 [summary.json](evidence/2026-09-21-a2-review9/summary.json)。

合法 1,073,737,728 和 1,073,741,824 bytes SQLite 均完成 create→verify→restore→check_restore；1,073,750,016 bytes 合法超限库拒绝且不发布。累计 payload 402,653,185 bytes、数据库 403,103,744 bytes 的闭环通过。四组合法 metadata/refs 边界共 12 个 limit−1/= /+1 点通过，限内完整恢复、超限拒绝且检查原状态。JSON/SQL 预检层边界与完整合法库边界分别记录，不把畸形等限输入写成完整流程成功。 新资源证据见 [A2 资源日志](evidence/2026-09-21-a2-review9/a2-resources.log.txt) 和 [合法大库边界日志](evidence/2026-09-21-a2-review9/a2-semantic-resources.log.txt)；这次是当前固定输入上的实际执行。

固定提交 `6bd6acfbe5c35d581891eb87275e1173e17848fc`（tree `6dd4da925cb97c22eca78a0a3f2165310e048db9`）的 254 个文件在构建前后核验一致；17 个运行模块在源码、wheel、新独立 Python 3.11 runtime venv 中字节一致。wheel 为 67,832 bytes，SHA-256 `8a76cdb3163796327858ba3c54680ee83c095500135c3d159373b06567c46e99`。本轮源码提交包含 D9 及其第八轮证据；后续复核文档提交不是本轮 wheel 构建来源。 核验见 [source-integrity.json](evidence/2026-09-21-a2-review9/source-integrity.json)，14 份日志的原始/公开摘要见 [log-index.json](evidence/2026-09-21-a2-review9/log-index.json)。验收工具仍不在 wheel 内，安装态测试与工具来自固定输入。

文件系统正向分类仍为 LOGIC_ONLY，实际 overlay / fsync=volatile 仍不受支持。受支持本地文件系统安装态正向、GX10、真实配额耗尽、真实 NAS、实际挂载丢失/断网仍 NOT_RUN；A2 整体 PARTIAL，PR 保持 Draft。当前检查不构成所有时序的穷尽证明。

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

第九轮已在固定输入 `6bd6acfbe5c35d581891eb87275e1173e17848fc`（D9 运行代码）重跑 11 项 A2 资源专项，全部通过。第六至第八轮是否执行的原记录保留；不将本轮结果追授给历史提交。当前日志见 [A2 资源](evidence/2026-09-21-a2-review9/a2-resources.log.txt) 与 [合法大库边界](evidence/2026-09-21-a2-review9/a2-semantic-resources.log.txt)。

合法业务 payload **402,653,185 bytes**（384 MiB+1），SQLite 文件 **403,103,744 bytes**。A1 `export_bundle()` 按自身预算返回 `RESOURCE_LIMIT/not_applicable`；A2 create→verify→restore→check_restore、逐 Blob 对账、幂等重放成功。

另用 SQLite 自身分配并释放临时 padding 表，恢复精确 A1 schema，保留合法 freelist 页，真实构造封存文件边界；未伪造 header 或以稀疏扩展冒充合法库：

| 文件字节数 | 4096-byte 页数 | 结果 |
|---:|---:|---|
| 1,073,737,728 | 262,143 | 完整快照/校验/恢复/核对成功 |
| 1,073,741,824 | 262,144 | 同链路成功，达到 1 GiB 上限 |
| 1,073,750,016 | 262,146 | SQLite integrity/FK/A1 语义校验成功；A2 超限拒绝且输出根为空 |

第 262,145 页覆盖 SQLite 保留 lock-byte；本 SQL 分配构造的首个超限点为 +8192，而非 +4096。依据 [SQLite 文件格式 §1.4](https://www.sqlite.org/fileformat.html#the_lock_byte_page)。文件字节 limit−1/= /+1 另在 stat 预检层检查，不能把这些稀疏 fixture 写成合法 SQLite 成功证据。

metadata **16 MiB**、refs **32 MiB**、全部 TEXT **64 MiB**、metadata **50,000 行**、refs **250,000 行**的 SQL 预检层边界仍保留（含 UTF-8 中文、ASCII）。这些预检 fixture 有意不保证完整业务语义，只证明计量与超限拒绝，不能代替合法业务恢复链。

D2 另以 A1 公共 API 构造四组完整合法库，共 **12 个 limit−1/= /+1 点**：metadata 字节、metadata 行数、refs 行数、refs 字节。每个点先通过 SQLite integrity/FK 和 A1 语义核验；限内/等限执行 create→verify→restore→check_restore 并核对完整表摘要，超限点拒绝且源数据不变、输出根为空。最新第九轮结果见 [D9 a2-semantic-resources](evidence/2026-09-21-a2-review9/a2-semantic-resources.log.txt)，此前 D6 结果见 [D6 a2-semantic-resources](evidence/2026-09-21-a2-review5/a2-semantic-resources.log.txt)，构造与范围见 [A2_RESOURCE_BOUNDARIES](A2_RESOURCE_BOUNDARIES.md)。这补齐 T09 可达合法 metadata/refs 边界的 LOGIC_ONLY 恢复链。

总 TEXT **64 MiB** 在其他预算同时满足时不存在完整合法等限库：[同文证明](A2_RESOURCE_BOUNDARIES.md#4-为什么完整合法库不能独立达到总-text-64-mib) 给出 `T ≤ 64 MiB + 18 − 29N`；有 metadata 时 `N ≥ 1`，故严格小于上限；空库仅 18 bytes。此结论不允许删除总 TEXT 检查，损坏输入的 limit−1/= /+1 仍由预检器覆盖。JSON 限制另有格式层用例，畸形字段不会因刚好等于解析上限而变成合法对象。

本轮逐用例耗时/RSS 见 [第九轮 A2 资源日志](evidence/2026-09-21-a2-review9/a2-resources.log.txt)和 [合法大库边界日志](evidence/2026-09-21-a2-review9/a2-semantic-resources.log.txt)。D6 历史逐用例耗时/RSS 见 [D6 A2 资源日志](evidence/2026-09-21-a2-review5/a2-resources.log.txt)及上述 D6 语义资源日志；[D5](evidence/2026-09-21-a2-review4/a2-resources.log.txt)、[D3](evidence/2026-09-20-a2-review2/a2-resources.log.txt)、[D2](evidence/2026-09-20-a2-review1/a2-resources.log.txt) 与 [D1](evidence/2026-09-20-a2-cloud/a2-resources.log.txt) 资源日志保留为历史。RSS 为测试进程累计峰值 KiB，非单个调用峰值或部署内存保证。文件预算 1 GiB 不是 RSS 上限。不同 suite 并行运行，耗时不应作为独占机器性能基准。

## 6. T01–T15 累计证据映射（更新至 2026-10-17）

以下“逻辑通过”只指列出的 Cloud 用例，不表示整行实机验收全部完成；规范中的全部正反例仍需逐项对照。

| 项目 | 当前证据 | 状态/剩余边界 |
|---|---|---|
| T01 | `test_snapshot_sqlite` + `test_snapshot_recovery`：T0/未提交/完整状态 | 逻辑通过 |
| T02 | 隔离头预检、DELETE 头部残留 WAL/SHM 拒绝、异常 journal 的打开前拒绝、UTF-16、锁与期限 | SQLite/预检逻辑通过；journal 反例未进入 SQLite；环境限定本表版本 |
| T03 | `test_snapshot_format/storage/recovery`：成员、链接、JSON、ID | 逻辑通过 |
| T04 | 物理/外键/业务/幂等/Blob/summary 损坏反例 | 逻辑通过 |
| T05 | mountinfo、fd mount_id、端点/root/子挂载与路径替换模型；§9 实际 namespace 解析，§11 CIFS 绑定，§12 private namespace 实际卸载/同名 ext4 拒绝 | 所列回归与实机子项通过；其他挂载变化未由本轮部署验证 |
| T06 | 原逻辑反例；§12 部分写入、同步/回执故障；§13 短写/零写入、ENOSPC/EDQUOT/EACCES、回读、真实损坏及 marker 前后 SIGKILL | 所列真实 CIFS 上的注入与进程中断场景通过；不等价于 NAS 实际容量/ACL、所有时序或设备耐久性认证 |
| T07 | `test_snapshot_concurrency`；§11 NAS mkdir 原语竞争；§12 同 ID 不同内容的两进程完整 NAS 发布及既有不完整目录拒覆盖 | 所列逻辑和此 CIFS 配置的完整发布冲突通过；不推广至其他配置 |
| T08 | stdout/丢响应核对；§10 本地、§12 NAS；§13 区分发布原对象完整/不完整/损坏；§14 NAS 输入恢复丢响应及 unknown 后沿原目标核对 | 所列实机子项通过；不伪造被杀进程响应，不把 unknown 统一改报成功；未证明真实断网或历史同步 |
| T09 | 文件/SQL/JSON 边界、1 GiB 合法封存、403 MB payload、四组合法库 12 点及 TEXT 上界证明 | 已列资源链 LOGIC_ONLY 通过；不形成部署资源/耐久性认证 |
| T10 | 同 fd 复制、字节/hash/路径/挂载变化检查 | 逻辑通过 |
| T11 | 既有目标拒覆盖，内容不同的双进程恢复竞争 | 逻辑通过 |
| T12 | 既有 restore 故障逻辑；§14 NAS 输入下读/复制/校验/链接/清理/同步失败、实际 SIGKILL、丢响应、sidecar 含断链及清理先于最终同步 | 所列 GX10 安装态场景通过；双硬链接与临时文件保留，只读 OK 不追认原同步/清理；不声明所有时序或其他配置 |
| T13 | Cloud 原逻辑及 §8–§9 本地 fixture；§11 真 NAS 恢复的全表/Blob、多父合并、内容复用、旧 key 重放与原制品追加版本 | 本次丰富合成数据的正向延续 PASS；不把未跑的实机冲突反例算入 |
| T14 | 五入口既有逻辑与本地安装；§11 NAS 正向；§12–§14 安装态 NAS API/CLI 故障返回和独立 CLI 核对 | 所列正向与故障子项通过；全套 API/CLI 反例未由本轮覆盖，整项仍 PARTIAL |
| T15 | §11 真 CIFS 保存、独立回读、按归属清单移除本轮本地可恢复副本、NAS-only 恢复与完整状态核对 | 该 GX10 / CIFS / SMB 3.1.1 合成配置 PASS；不推及其他 NAS 或硬件断电 |

## 7. 第九轮实机移交与完成条件（历史）

以下保留 2026-09-21 第九轮的移交原记录。当前已完成项和下一接续点见 §8–§14；不因这段历史流程重复构建或重跑已有适用结果。

本会话没有可调用的 GX10 shell 或已接入的 A2 Local Hand 执行通道；现有 Local Hand 信息不能自动等价为本任务已获机器访问。A2 授权保持有效，本记录不要求重复开工批准，也不新增或修改 Local Hand 准入配置。

下一步按 [A2_RUNBOOK](A2_RUNBOOK.md) 固定本轮已验证输入 **`6bd6acfbe5c35d581891eb87275e1173e17848fc`（D9）**，在 GX10 创建项目独立 build/runtime 环境，执行源码/资源/安装态验收；再提供部署侧核实的 NAS 配置，在专属合成范围运行 [a2_nas_exercise.py](../tools/acceptance/a2_nas_exercise.py)。工具先构造并关闭合成库和快照，在 NAS 操作之前冻结精确全树、inode/挂载、常规文件 nlink 与内容摘要清单；NAS 能力预检、发布和独立校验成功后，再次逐项核对，才移除归属可证明的本地可恢复副本。期间新增文件、替换或改写均阻止删除。新进程仅从 NAS 恢复并逐项核对，不能依靠已删除的本地源库或快照回退；这些仍是待实机执行的步骤。

工具对同一打开 fd 计算 wheel 摘要并核对安装包字节，但 `--source-commit` 是调用者声明，不能凭 wheel 字节匹配证明 Git 来源；必须另存固定 checkout、构建日志、wheel 摘要、安装日志和实际模块路径的构建链。报告 `evidence_saved=false` 表示未确认报告已可靠记录，不表示操作没有副作用，也不证明报告路径不存在。完整说明见运行手册。

工具不卸载共享盘、不关闭设备、不配置 NAS 服务，也不删除已有项目/业务库。真实挂载丢失/断网须在隔离测试范围留独立证据；工具的 `synthetic_nas_roundtrip` PASS 不代替这些项目。受支持本地文件系统闭环、GX10、真实 NAS、T06/T08 实际部署证据未齐前，不宣称 S1–S5 全部验收完成。

没有 GitHub Actions 运行结果、硬件断电、设备缓存、生产切换、冗余备份、A3/A4 或供应商普遍兼容性声明。将来合并时保留 C→D1→`f13d2fb`→D2→`f564174`→D3→`14f68c7`→D4→D5→`689b2d0`→D6→`073057c`→D7→`0de4e57`→D8→`a7f7b27`→D9→`6bd6acf`→本轮复核证据提交的祖先链，不 squash 掉独立 CLOSED 记录；本轮仅提交 PR，不自动合并。

## 8. 2026-10-16：真实本地安装态闭环

本次登记复用已经完成并保留的两个独立合成数据运行，没有为补文档重放业务。Linux x86_64、kernel 6.8.0-124-generic、glibc 2.35、真实 ext4；Python **3.11.15** 和 **3.12.14**，两者 SQLite **3.37.2**。这不是 GX10。两个 runtime 分别新建，从已有 wheel 离线安装，执行时只读；业务脚本从源码目录之外以 `-I -B` 启动，CLI 子进程使用同一安装解释器和 console entry。未替换文件系统分类、SQLite 或恢复 API。

| 绑定 | 本次值 |
|---|---|
| 固定 Ledger 输入 | `6707a1b521c9c4718674620e6c584656bd434e4c` |
| wheel | `infra_artifact_ledger-0.2.0a1-py3-none-any.whl` |
| wheel SHA-256 | `d1924a346effe83a100860a5f00f4c3eafa12fa51b63529f0a788cef94b4dc8a` |
| 固定脚本 Git blob | `347e07b6c32042ddf10bebb0ddfab45c703c0c14` |
| 执行包装器 | 独立安装的 Local Hand `d04fcf51c51df5942d8a2c00c71f12d0c9214b12`；其源码随后通过 PR #44 合并，包装器不构成 Ledger 运行依赖 |
| 文档 PR 基线 | `37ed2eaa1c7f12504723de23b856b892d419d8a3`；所核对的 17 个模块和脚本与固定输入一致 |

已有 wheel 本次没有重新构建。17 个模块逐一核对固定 Git blob、源码、wheel 和两份安装包字节；脚本绑定单列，因为它来自 checkout，不在 wheel 内。`--source-commit` 仍是调用者声明，独立字节核对不改写原报告的 `CALLER_ASSERTED_NOT_GIT_ATTESTED` 和 wheel 证据限定。逐文件记录见 [source-integrity.json](evidence/2026-10-16-a2-local/source-integrity.json)。

| 运行 | API / CLI 操作 | 实际结果 |
|---|---|---|
| Python 3.11.15 | 各执行 create → verify → restore → check_restore | 八项 PASS；恢复 Blob、原幂等记录、summary 一致，原合成库不变 |
| Python 3.12.14 | 同上，使用另一独立 runtime 与新合成目录 | 八项 PASS；同样的完整性检查通过 |

两轮实际 runtime 探针、匿名临时文件检查、业务进程均退出 0，stderr 为空，双流采集无截断或未排空。固定程序要求完整八项报告，包装器再次校验报告与输入字节；不是仅依据零退出码判成功。运行前后完整 runtime 清单、所选源码和 wheel 保持不变。首次登记以只读 immutable SQLite 连接比较保留的源库、两份快照及 API/CLI 恢复库的全部 SQL 表行；当时读取前后摘要仅保存了源库和两份恢复库。

PR #8 复核另补[只读核对记录](evidence/2026-10-16-a2-local/readonly-recheck.json)：两轮各五个数据库均完成读取前后摘要、完整性、外键、结构及全部表行比较；一致性比较在每轮内部进行，两轮记录的生成时间不同。四份快照的 marker → manifest → 数据库摘要绑定一致；运行目录内条目的集合、文件内容及已核对的 mode、inode、size、mtime 未变，不对其他文件元数据作不变声明。该核对只证明当前保留内容一致，不追认历史发布同步或断电耐久性。17 个模块和固定脚本在两版解释器中各进行一次内存编译，未执行或重放业务。

每轮实际数据为 1 个 Artifact、1 个 Version、1 个 ContentRoot、1 个 39 字节 Blob 和 2 条成功幂等记录；没有 Manifest、ProvenanceLink、ImportReceipt、多父版本或恢复后新增版本。各数据库为 49,152 字节。全表比较仅针对该 fixture，不能把缺席的记录类型或 T13 全部场景解释为已经通过。

公开[证据说明](evidence/2026-10-16-a2-local/README.md)、[汇总](evidence/2026-10-16-a2-local/summary.json)和两份执行记录保留版本、结果、退出和原始摘要；机器路径已替换为占位符，未上传原始配置或合成数据库。pip 安装日志中的不可写缓存警告保留于私有原件，安装成功；业务 stderr 为空。没有把安装日志说成全程无警告。

本轮只补所列本地安装态正向缺口，未重跑已有 Cloud 全量/资源专项，未测量资源峰值，也没有补齐所有 T01–T15 反例。GX10、真实 NAS 能力/发布/仅从 NAS 恢复、实际挂载丢失/断网、T06/T08 部署故障仍未完成；整体 A2 保持 PARTIAL。systemd 限额/隔离、Local Hand 常驻服务端到端及硬件断电耐久性均未验证，但不属于本轮 Ledger A2 完成的新增前置；A2 原验收范围仍以固定[验收矩阵](a2/ACCEPTANCE.md)为准。

下一步复用上述本地结果和已核实安装，核对部署侧真实 NAS 挂载与专属合成目录，再按运行手册执行剩余现场项。NAS 条件未具备时保留 NOT_RUN；不以本地替身或 Local Hand 功能完成替代 NAS 验收。


## 9. 2026-10-16：GX10 挂载表修复与安装态本地闭环

§8 保留此前 x86_64 的原版本和结果。本次 GX10 首次使用同一 `6707a1b` wheel，在创建合成目录前因合法 nsfs root 被误判而失败，退出 1、合成目录为零。原件保留。修复只允许 nsfs 的 namespace 名称/inode root 语法，保留最深挂载及 fd/mount_id 检查，nsfs 本身仍拒绝作为存储。没有跳过不受支持挂载而回退到父挂载。

固定修复源码为 **`d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b`**，tree `f1e18c92e05888441c19ee463bfb1f804c4b1d16`；新构建 wheel SHA-256 **`b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293`**。280 个固定源文件在构建及 GX10 执行前后核对，17 个运行模块与 wheel、安装一致；GX10 编译 17 个模块与 2 个验收脚本通过。软件仍为 `0.2.0a1`，来源靠提交与摘要区分。

GX10 实际为 Linux 6.17.0-1031-nvidia / aarch64 / glibc 2.39、Python 3.12.3 / SQLite 3.45.1、ext4。在新的独立 runtime 与合成目录执行 API/CLI 各四项本地操作，八项 PASS、进程退出 0、业务 stderr 为空。保留的原 A1 与首次失败现场共 27 个登记文件、适用元数据及旧 Git head/status 不变。runtime 前后核对覆盖 17 个模块、解释器、venv 配置和 console script，不是完整 runtime inode 清单。

后续只读核对取回的 24 个文件，以及源库、两份快照和两份恢复库的完整性、结构、外键、五表全部行、独立已知 Blob、身份引用与两条幂等指纹；两份封存的 marker/manifest/database 摘要链一致，恢复库字节与快照相同，原业务未重放。数据规模仍为单版本、39 字节 Blob、两条幂等记录，不代表完整 T13。

旧解析器复现两项新测试错误；另将原有源改写测试的时间戳依赖改成显式注入可观察 mtime 变化。修复后 Python 3.11/3.12 的存储套件各 25 项通过；Python 3.11 快照套件 279 项发现、267 项通过、12 项跳过（11 项资源专项与 1 项当前 ext4 不适用的非支持文件系统测试）。GX10 的原始挂载表 53 行（含 3 行 nsfs）用修复解析器读通。没有重跑全部历史资源测试，也不把源码回归算成 GX10 全套测试。

完整来源、首次失败、日志摘要及复核见[GX10 公开证据](evidence/2026-10-16-a2-gx10/README.md)。真实 NAS 能力/保存/仅从 NAS 恢复、挂载丢失/断网和 T06/T08 部署故障仍未执行，A2 保持 PARTIAL。接续复用本次已核实 runtime 与原合成结果，在实际文件访问与挂载绑定就绪后执行剩余 NAS 项；不重放本地闭环，不把设备在线视为 NAS 验收通过。

## 10. 2026-10-17：GX10 安装态 CLI 响应丢失

复用 §9 的固定源码 `d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b` 与同一 wheel，重新只读核对
19 个相关源码文件、17 个安装模块、解释器和环境绑定后，在全新合成目录执行。
本轮核对的主线 `50c400481af415b5030298061407765f48a321b2` 中，`src/`、`tools/acceptance/`
及 `pyproject.toml` 与该运行来源一致；没有重新安装、改产品实现或重放此前验收。

`create` 与 `restore` 各启动一次，在子进程启动前关闭 stdout 管道读端，真实安装态 CLI
均退出 9，并返回响应通道失败诊断。随后分别使用 `verify` 与 `check-restore` 核对原路径，
两次只读命令均退出 0；没有通过重新创建或再次恢复来获取成功响应。

create 的 manifest 摘要是在响应丢失后按原 ID 和源码身份读取、再经 verify 核对所得，
只能证明当前目录自洽，不能表述为匹配事先独立摘要、认证精确 T0 或追证历史调用成功。
该引用在 restore 启动前单独保存，恢复核对使用其中的原数据库摘要。

独立取回 26 个文件并核对大小和摘要。源库、快照库与恢复库的全部 SQL 表和 Blob 字节一致，
含 16 条记录、21 条引用、7 条幂等记录、3 个 Blob（共 103 字节），覆盖分支、多父合并、
内容复用、Manifest、来源及已有导入；核对前后保留文件一致。详细结果、执行检查器及局限见
[本轮公开证据](evidence/2026-10-17-a2-gx10-response-loss/README.md)。

本次只补 T08 的本地安装态响应通道故障子项。没有运行 NAS publish、挂载丢失、真实断网、
NAS-only 恢复或 T06 部署中断，也不证明硬件耐久性；T08 整项及 A2 整体仍为 PARTIAL。

## 11. 2026-10-17：GX10 真实 NAS 合成恢复

复用 §9 的固定 `d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b`、已有 wheel 与独立 runtime，
在 GX10 / aarch64 / Python 3.12.3 / SQLite 3.45.1 / ext4 和真实 CIFS（SMB 3.1.1）挂载上，
完成一次 `synthetic_nas_roundtrip`，脚本退出 0、结果 PASS。
wheel SHA-256 为 `b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293`；
执行前核对 wheel 与验收脚本摘要，工具再次核对安装模块字节。

真实挂载与目录绑定、存储能力、两进程目录原语竞争通过。新合成快照发布后由独立进程回读，
核对预先保管的身份与摘要，再按冻结归属清单移除本轮源库、导入源、本地快照和暂存。
另一个全新进程仅从 NAS 恢复至新本地目录；写入前 check_restore、全部 SQL 行与所有 Blob
核对通过，原请求重放不增记录，随后在原 Artifact 上追加一个新版本及 29 bytes 内容。

初始为 2 个 Artifact、4 个 Version、3 个 Blob / 103 bytes、7 个幂等记录及 1 个导入回执。
新增版本后分别为 2、5、4 / 132 bytes、8、1；独立只读对账确认全部旧行、关系、操作与内容保留，
仅增加预期的版本、内容根、Blob、四条关系和一次操作。18 个原始证据文件在成功恢复后取回，
大小与摘要全部一致并持久保留；现场确认 disposable 已不存在，原 NAS 挂载仍匹配。

详见[公开证据](evidence/2026-10-17-a2-gx10-nas/README.md)、
[结构化摘要](evidence/2026-10-17-a2-gx10-nas/summary.json)和
[独立核对程序](evidence/2026-10-17-a2-gx10-nas/audit.py)。实际端点、配置、密码输入过程的无密码记录、
原始报告与合成数据库保存在私有部署侧；没有把它们发布到仓库。

本次仅证明这个配置下的真实合成恢复，耐久性范围为 `filesystem_acknowledged`。
T06、T08 的 NAS 发布丢响应/挂载丢失等剩余故障、同 generation 的完整 NAS 发布竞争、
真实断网和设备断电仍未执行，未测量资源峰值。A2 继续保持 PARTIAL；接续复用已有结果与挂载绑定，
不重复已完成的恢复。挂载为临时配置，没有新增开机自动挂载或保存密码。

## 12. 2026-10-17：真实 NAS 发布故障与冲突

§11 的未测项描述正向恢复完成时的状态。本轮复用同一 `d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b`
安装与真实 GX10 / CIFS / SMB 3.1.1 挂载，新建专属合成目录，完成九种场景。
安装模块前后与已有 wheel 一致；产品代码没有改动。

1. NAS publish 的 stdout 读端在启动前关闭；CLI 退出 9。按发布前已保存的 ID 与摘要只读核对原代成功，没有重新发布。
2. 两个同 ID、不同内容的完整候选，两个独立进程会合后争用独占目录；一个成功，一个 `TARGET_EXISTS`，最终字节和全部行只匹配胜者 B。
3. 已有不完整 generation 的发布被 `TARGET_EXISTS / not_applicable` 拒绝，原 sentinel 未变。
4. 数据库真实写入并同步 128 bytes 后注入 EIO，返回 `IO_ERROR / not_published`；保留原前缀，无 marker，verify 拒绝。
5. manifest 同样真实部分写入后注入 EIO，返回及不完整快照核对符合预期。
6. 真实 marker link 后，generation 最后 fsync 注入 EIO；返回 `PUBLICATION_UNKNOWN / unknown`，只读核对当前完整内容成功。
7. 真实 NAS marker link 成功后注入 EIO 模拟回执丢失；同样返回 unknown，原代只读核对成功。
8. 普通 UID 1000 发布进程在真实 marker link 后暂停，控制器在 private 挂载命名空间实际 lazy unmount 继承的 CIFS，再绑定新的空 ext4 影子目录；继续原进程后返回 unknown。从主机原挂载只读核对原代成功，保留一个未清理的 marker 临时目录。
9. 另一 private 命名空间内，调用前已以同名空 ext4 目录替代 CIFS；安装态 CLI 返回 `UNSUPPORTED_STORAGE / not_published`，影子目录没有新增文件。

挂载操作前检查 namespace 与宿主不同、传播为 private；产品进程仍以普通用户运行。
未全局卸载、改 NAS 服务或真实断网。故障注入仅作用于验收进程的明确 I/O 边界，
不替换文件系统分类、SQLite 或实际 NAS 文件操作。

首次挂载丢失的产品进程已退出并保存 unknown，但验收控制器的系统 Python 为检查退出码映射
导入 Ledger 失败，原控制器报告为 FAIL。修复将该核对改为标准库操作；保留初版、失败和
原产品返回，在确认退出与原挂载正常后，仅执行尚未调用的第 9 项和最终只读核对。
没有重新执行第 8 项发布，没有修改历史失败为成功。

独立取回 138 个文件 / 738,283 bytes，全部大小和摘要一致。五个完整 NAS 代与预先保存的
两个候选逐文件、全表、实际 Blob 字节比较；部分写入核对精确前缀。此前 T15 成功归档
与其独立保管摘要一致，原挂载不变。只读 verify 不追认失败调用最后同步或残留清理成功。
对四份副本篡改内容、unknown 状态、挂载类型和 namespace，更新传输摘要后仍全部被拒绝；原件不变。

详见[公开证据](evidence/2026-10-17-a2-gx10-nas-faults/README.md)、
[结构化摘要](evidence/2026-10-17-a2-gx10-nas-faults/summary.json)与
[独立核对器](evidence/2026-10-17-a2-gx10-nas-faults/audit.py)。
本轮覆盖 T07 所列 NAS 冲突及 T05/T06/T08 上述子项，不表示全部 A2 通过。
其他中断位置、短写恢复、空间/权限/回读失败尚缺本轮部署证据；真实断网、设备断电和
资源峰值未执行或测量。接续复用本轮结果，仅补剩余缺口，A2 继续保持 PARTIAL。

## 13. 2026-10-17：NAS 发布 I/O 与实际进程中断

§12 当时列出的短写、空间、权限与回读缺口，本轮在同一固定 `d4ba0c6` wheel/runtime、
GX10 / CIFS / SMB 3.1.1 配置上，以全新专属目录补证。没有重新安装、改产品代码或重放旧发布。
先核对 T15 与上一轮 NAS 故障原件的独立摘要，再构造新的丰富合成库、创建本地快照，
在全部发布前保存 snapshot ID 与 manifest/database 摘要。

十八个场景逐一独立发布并用新 CLI 进程 verify：短写续写、零写入，四个 ENOSPC 位置、
一个 EDQUOT，两个 EACCES 位置，marker 前两项与之后三项真实回读后的 EIO，
marker 后两项实际字节损坏，以及实际 marker link 前后各一次 SIGKILL。
短写调用真实 os.write，每次最多 97 bytes，产品 _write_all 未替换；DB 507 次、manifest 8 次、
marker 2 次写入后完整。错误只在选定 syscall 边界注入，NAS 容量、配额、ACL、服务和挂载未更改。

发布返回为一项 published、九项 not_published、六项 unknown；两项 SIGKILL 实际退出 -9，
没有响应，未为它们编造产品 envelope。独立 verify 为五项 OK、十一项不完整、两项完整性失败。
六个 unknown 的当前对象为三个完整、一个缺 marker、两个已损坏：保留这些差异，
不把 unknown 等同于成功或自动重新发布。两个被杀进程留下的 NAS marker 临时目录及
本地 scratch 数据库各两个，全部保留；marker ENOSPC 的产品自建临时目录按原实现清理。

取回 278 个文件 / 1,532,951 bytes，逐文件大小、摘要匹配。独立核对器逐项核对精确文件成员、
空文件和前缀、短写次数、损坏偏移/值、errno、原始进程输出与退出、crash 检查点、
五个完整 NAS 代全部 SQL 行与 Blob 字节、旧归档及挂载。verify 前后原文件一致；
六种篡改副本即使更新传输摘要与相关报告仍被拒绝。执行器、支持代码、核对器编译通过。
当前运行与核对没有出现新的产品或验收脚本失败，历史失败仍保留。

详见[公开证据](evidence/2026-10-17-a2-gx10-nas-io/README.md)、
[摘要](evidence/2026-10-17-a2-gx10-nas-io/summary.json)和
[离线核对器](evidence/2026-10-17-a2-gx10-nas-io/audit.py)。
本轮补足此前明确列出的发布 I/O 代表场景，仍不宣称全部 T01–T15、所有时序或 NAS 实际容量/ACL
策略通过；真实断网、断电和资源峰值未验证。接续优先 NAS 输入下的恢复端 T12 故障，并对账
完整矩阵剩余部署证据，复用上述结果；A2 整体保持 PARTIAL。


## 14. 2026-10-17：NAS 输入恢复端故障与只读核对

复用同一固定源码、wheel 和 GX10 runtime，以 §11 已保留的独立摘要绑定原 NAS 快照，
只读输入、全新 ext4 合成目标，完成[15 个恢复与 7 个核对场景](evidence/2026-10-17-a2-gx10-nas-restore/README.md)。
没有重新发布 NAS 或重跑旧恢复，产品代码、NAS 原对象、安装和挂载均未改变。

- 正常恢复观察到本次暂存已清理，随后最终目标 fsync；published 且独立核对完整。
- NAS 实读后 EIO、目标部分复制 ENOSPC、暂存真实损坏、暂存 fsync EIO、本地 link 的 ENOSPC/EACCES 均得到对应错误/not_published。NAS 读失败未建目标，其余清理暂存后保留空目标。
- link 前/后不确定 EIO 与 link 后清理/目标同步/父目录同步失败共五项 unknown；当前四个完整、一个无公开 DB。
- link 前/后两个真实 SIGKILL 均退出 -9、无产品响应；安装态 CLI stdout 断开退出 9。后续沿原目标核对，不重复恢复。
- 六种 SQLite sidecar（journal/wal/shm 各普通文件与断链）全部拒绝；无关断链允许且保留。它们是七个独立合成 check_restore fixture，不计作七次恢复。

15 次恢复之后的独立 CLI 核对为七项 OK、八项 NOT_FOUND。三个目标暂存目录、两个 scratch
数据库保留；其中清理失败与 link 后 SIGKILL 的目标各为同 inode 双硬链接、nlink=2。
只读核对保持原字节、成员、inode、链接数及 mtime/ctime 不变，不清理、不启用业务。

219 个普通文件（1,227,519 bytes）和四个断链已取回；离线标准库核对器验证原始响应、调用参数、
故障边界、进程身份、精确残留、原输入摘要与全部 SQL 表行/Blob。六份同步改动传输摘要及相关报告的
篡改副本均拒绝；执行器、支持代码、核对器与传输脚本编译通过。原始配置和数据在私有部署记录中。

本轮对应 T08/T12/T14 的所列部署子项 PASS，整体 A2 保持 PARTIAL。注入 errno 不等价于真实
设备满盘、权限策略或断电；只读核对不追认历史同步/清理。下一步逐项核对 §6，优先明确 T09
部署 RSS/耗时与 T11 目标冲突的证据缺口；复用适用历史逻辑和已完成现场结果，不要求无限追加故障场景。
