# 兼容与运行边界

当前软件为 `0.2.0a1` alpha，首次安装见[安装指南](INSTALL.md)。A2 已验证的组合、源码和限制见下方[本地安装态结果](#a2-真实本地安装态补证2026-10-16)、[GX10 安装态结果](#a2-gx10-修复版安装态补证2026-10-16)、[真实 NAS 合成恢复](#a2-gx10-真实-nas-合成恢复2026-10-17)、[故障补证](#a2-gx10-真实-nas-故障补证2026-10-17)与[发布 I/O 补证](#a2-gx10-nas-发布-io-补证2026-10-17)。所列配置的正向恢复及发布故障/冲突检查通过，其他部署缺口尚未完成，A2 整体为 PARTIAL。

本页按验证轮次保留各自的版本和结果。以下 A1 `0.1.0a1` 部分是历史记录；其中的“当前”和“本轮”指该轮验证，不能据此选择当前软件版本，也不能把旧结果自动归给新的源码或安装包。

## A1 兼容与运行边界

软件版本：`0.1.0a1`，Alpha。验证日期：2026-09-20 UTC；本次 GX10 现场日期为北京时间 2026-09-21。完整来源、执行证据见 [A1_VALIDATION](A1_VALIDATION.md)。未发布 PyPI 包；按 [USAGE](USAGE.md) 从固定源码构建并安装 wheel。

| 项目 | 本轮结果 / 边界 |
|---|---|
| 必验环境 | Linux x86_64，kernel 6.18.44，glibc 2.39；Python 3.11.16；SQLite 3.53.1 |
| 历史 D 的 Cloud 验收 | Python 3.11.15 / SQLite 3.45.1；105 普通测试、8 资源测试与编译通过；wheel 构建、隔离安装和文档闭环通过，具体记录见 A1_VALIDATION §5 |
| 历史 F 的 Cloud 验收 | Python 3.11.15 / SQLite 3.45.1；126 普通测试、响应节点/字节两个真实资源专项、编译、wheel 隔离安装及文档闭环通过，见 A1_VALIDATION §6 |
| 历史 H 的验收 | 本地及 Cloud 的139普通测试、两个响应资源专项、编译、wheel隔离安装及实际文档示例通过，见 A1_VALIDATION §7 |
| 历史 J 的验收 | 本地及 Cloud 的154普通测试、两个响应资源专项、编译、wheel隔离安装及实际文档示例通过，见 A1_VALIDATION §8 |
| 当前 L 的本地验收 | 必验环境下157普通测试、编译、wheel隔离安装、实际文档示例及安装态3项路径回归通过，见 A1_VALIDATION §9；该轮未重跑资源专项 |
| 当前 L 的 Cloud 验收 | Python 3.11.15 / SQLite 3.45.1；157普通测试、编译、wheel隔离安装、实际文档示例及安装态3项路径回归通过，见 A1_VALIDATION §9；该轮未重跑资源专项，私有规则读取单列 BLOCKED |
| 合并版本的 GX10 实机验收 | `a00926dff89ae5ddc498105472899a48381b1927`；Linux 6.17.0-1031-nvidia / aarch64 / glibc 2.39，Python 3.12.3 / SQLite 3.45.1，本机 ext4；157普通测试 + 8容量测试 + 1响应节点测试、编译、独立wheel安装及实际文档闭环通过。原始日志复核及来源限制见 A1_VALIDATION §10 与[公开证据](evidence/2026-09-20-gx10/README.md)；不代表所有 ARM 平台 |
| 附加环境 | Python 3.12.14 的结果见验证记录；不能替代 3.11 验收 |
| 运行依赖 | Python 标准库；无需模型、云账号、私有 companion 或第三方运行包 |
| 构建环境 | pip 24.0、setuptools 84.0.0、wheel 0.48.0、packaging 26.3 |
| Python 版本声明 | `>=3.11` 是安装最低版本；未测的后续版本不代表已经验证 |
| 公共版本 | contract 0.1.0；transport 0.1.0；profile `bounded-local-v0.1` |
| 固定 schema 标识 | `urn:code-driver-theory:artifact-ledger:v0.1:candidate`；候选后缀保持批准的合同原值 |
| SQLite 格式 | version 1；DELETE journal，synchronous FULL，锁等待 5 秒；无自动 migration |
| 部署范围 | 本地可靠文件系统、单一受信数据域、一个逻辑写入者；每个进程独立连接，handle 由创建线程使用 |
| CLI 文件 | 输入为普通文件；输出使用同目录临时文件、fsync 和硬链接拒覆盖发布，需要文件系统支持相应行为；提前拒绝输出到当前数据库同名 -journal/-wal/-shm 路径 |
| 数据库初始化 | Python initialize / CLI init 在同目录私有临时目录构建后，使用硬链接拒覆盖发布并 fsync 父目录；发布后失败可能留下完整目标，须 open 核对 |
| 未验平台 | Windows、macOS、表中未列出的 ARM / Python / SQLite / 文件系统组合、网络文件系统及真实 NAS；不宣称兼容 |
| 许可 | 仓库根目录 MIT [LICENSE](../LICENSE) |

硬上限沿用 [接口 §3](INTERFACE_PROFILE.md#3-资源限额)：普通 JSON 8 MiB、Blob 64 MiB、一次调用或一个 Version 内容闭包 256 MiB、package 384 MiB、descriptor 4096 字节。Ledger 的历史累计量不受单调用 256 MiB 限额约束；整库超过可移植包上限时导出会拒绝，不自动拆包。

每个 JSON 文档另受 500,000 节点和 16 层容器深度限制，完整读取响应也适用。各次写入分别合法，不代表累计历史必定能装进一次响应；超限查询返回 RESOURCE_LIMIT，不截断历史或改变既有记录。

历史资源测试观测的进程峰值约 1.48 GiB；本次 GX10 的容量 suite 最大记录为 1,551,072 KiB（约 1.479 GiB），响应节点专项最大记录为 199,064 KiB。它们明显区别于 serialized 文件大小，是各自环境及测试进程的观测值，不是通用内存上限、全机峰值或最低配置保证。完整测量见验证记录 §3 与 §10。

CLI 正常操作和错误使用单行 JSON；`--help` 是面向人的文本辅助入口。没有有效响应时使用原请求身份核对结果。export 的 package 与 descriptor 分别拒覆盖发布，不构成两个输出文件的原子提交；任一输出失败应按错误语义检查已有输出。

本轮进程终止和故障注入证明的是已测试失败模型下的结果；没有真实断电、设备缓存或硬件故障耐久性证据。A2 一致快照/恢复、A3 两类真实消费者、A4 独立第二实现分别需要后续验证。


## A2 0.2.0a1 Cloud 第九轮记录（2026-09-21）

以上是 A1 0.1.0a1 历史证据；A2 运行修订仍为 D9 `928b3e0338e7460b9916bf27fc832ce5697b1b7a`。最新第九轮固定输入 `6bd6acfbe5c35d581891eb87275e1173e17848fc`，tree `6dd4da925cb97c22eca78a0a3f2165310e048db9`，包含 D9 与第八轮证据；本轮未修改产品代码、测试、工具和构建配置，未确认新缺陷。历史与当前来源见 [A2_VALIDATION](A2_VALIDATION.md)。

本轮环境 Linux x86_64 / Python 3.11.16、3.12.14 / SQLite 3.53.1；254 个输入文件在构建前后按固定 tree 核验，17 个运行模块的源码、wheel、安装字节一致。复用已有 build venv 及 system-site-packages 构建工具；Python 3.11 runtime venv 独立新建，3.12 复用此前环境。安装态从源码目录外以 -I 运行，库来自 site-packages，测试/验收工具来自固定输入；工具不在 wheel 内。本地是核验后的文件集，并非 Git checkout。

两版本源码各 442 项发现、422 项通过、20 项跳过；安装态 A2 276 项发现、265 项通过、11 项跳过；A2 两组资源专项 7+4 项全部通过。14 条固定源码构建、安装、测试、编译和环境命令全部 exit 0。两版本 compileall、A1 安装态闭环与生产 API/CLI 对实际 overlay 的拒绝通过。常规源码的 20 项跳过由 A1 9 项与 A2 11 项组成；本轮另以原始常量显式执行并通过 A2 全部 11 项资源专项，A1 9 项仍保留 D2 历史。安装态 11 项为同一批 A2 专项，专项在源码态执行，不冒称安装态专项。跨版本/安装态结果不相加。

本轮复核严格 JSON、源保护、发布错误状态、固定读取与恢复完整状态、目录/挂载绑定和验收清理；D9 子进程解析与记录回归继续通过。资源专项按原常量重跑，合法 1 GiB 数据库与超过 portable 上限的累计 payload 完成恢复链，metadata/refs 四组合法边界通过。见 [本轮证据](evidence/2026-09-21-a2-review9/README.md)。未执行被阻断的锁失效重演；管理性稳定前置等边界不变。

A2 本地候选文件系统为 ext4/xfs/btrfs，NAS profile 为显式核实的 nfs/nfs4/cifs；类型名称不等于兼容认证。实际 Cloud overlay（fsync=volatile）仍被生产 API/CLI 拒绝，正向模拟仅 LOGIC_ONLY。

在该轮记录时，GX10、受支持本地文件系统安装态正向、真实 NAS 及 T06/T08 实际部署演练均为 NOT_RUN，整体 PARTIAL。2026-10-16 的本地正向补证见下节。资源可达性证明见 [A2_RESOURCE_BOUNDARIES](A2_RESOURCE_BOUNDARIES.md)，不替代部署资源或耐久性认证。上文 Windows/macOS/其他未验组合边界保留。

A2 封存文件上限 1 GiB，其他预算与合作式 300 秒期限见 [A2 接口](a2/INTERFACE_PROFILE.md)。读回核对不能证明过去发布同步或设备断电耐久性。构建/部署见 [A2_RUNBOOK](A2_RUNBOOK.md)。

## A2 真实本地安装态补证（2026-10-16）

固定输入 `6707a1b521c9c4718674620e6c584656bd434e4c`，保留 wheel SHA-256 为 `d1924a346effe83a100860a5f00f4c3eafa12fa51b63529f0a788cef94b4dc8a`。17 个运行模块与固定源码、wheel、两份独立安装字节一致；本轮未重建 wheel。完整来源与限定见 [A2_VALIDATION §8](A2_VALIDATION.md#8-2026-10-16真实本地安装态闭环)和[公开证据](evidence/2026-10-16-a2-local/README.md)。

| 环境 | 验证范围 | 结果 |
|---|---|---|
| Linux x86_64 / kernel 6.8.0-124-generic / glibc 2.35 / ext4 / Python 3.11.15 / SQLite 3.37.2 | 独立安装后的 API/CLI 各四项本地快照、校验、恢复及恢复检查；Blob、幂等记录、源保护 | PASS |
| 同一主机与文件系统 / Python 3.12.14 / SQLite 3.37.2 | 另一独立安装与新合成目录中的相同步骤 | PASS |
| GX10 的 A2 | 未覆盖该机器与架构组合 | NOT_RUN |
| 真实 NAS 能力、保存及丢失恢复 | 未使用 NAS 配置或网络文件系统替身 | NOT_RUN |
| T06/T08 部署故障、挂载丢失/断网 | A2 剩余现场项，本轮正向闭环不覆盖 | NOT_RUN |
| 硬件断电耐久性、systemd 隔离、Local Hand 常驻服务全链 | 未验证，且不新增为 Ledger A2 完成前置 | NOT_RUN |

这两份结果证明所列环境下的安装态本地正向流程，整体 A2 仍为 PARTIAL；不能推广到所有 Linux、ext4、Python 3.11/3.12 或 NAS 配置。每轮只有一个版本、一个 39 字节 Blob 和两条成功幂等记录；完整数据规模及后续只读核对见公开证据，不宣称完整 T13 实机通过。没有为补录证据重跑业务或历史资源专项，也没有新增 Ledger 对 Local Hand 的运行依赖。


## A2 GX10 修复版安装态补证（2026-10-16）

上节 GX10 的 NOT_RUN 属于当时两次 x86_64 补证范围。本次修复合法 nsfs root 导致整张 mountinfo 被拒绝的问题，固定源码 `d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b`，新 wheel SHA-256 `b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293`。实际构建、17 模块字节绑定、首次失败与后续独立运行见[GX10 证据](evidence/2026-10-16-a2-gx10/README.md)和 [A2_VALIDATION §9](A2_VALIDATION.md#9-2026-10-16gx10-挂载表修复与安装态本地闭环)。

| 环境或范围 | 本次结果 |
|---|---|
| GX10 / Linux 6.17.0-1031-nvidia / aarch64 / glibc 2.39 / Python 3.12.3 / SQLite 3.45.1 / ext4 | PASS：独立离线安装，API/CLI 各四项本地操作；编译、完整性、Blob、幂等记录及保留内容核对 |
| 真实 NAS 能力、保存、丢失恢复与 T06/T08 部署故障 | NOT_RUN |
| 其他 ARM、文件系统、Python/SQLite 组合 | 本次不新增兼容性声明 |

整个 A2 仍为 PARTIAL。原 A1、原失败现场与旧 x86_64 结果保留各自版本，不追授给本次新 wheel；nsfs 仍不是可用存储。此次小型 fixture 不代表完整 T13、资源上限或硬件耐久性验收。

## A2 GX10 本地响应丢失补证（2026-10-17）

复用上节相同源码、wheel、GX10 / aarch64 / Python 3.12.3 / SQLite 3.45.1 / ext4 环境，
补做新合成目录中的安装态 CLI 通道故障：create/restore 各一次，响应丢失后沿原对象
verify/check-restore，当前内容与完整记录核对通过，没有重执行业务。
来源绑定、四次命令、摘要来源区别和局限见[本轮证据](evidence/2026-10-17-a2-gx10-response-loss/README.md)。
这仅增加 T08 的本地子项证据；上节带日期的 NOT_RUN 保留原意，后续真实 NAS 结果见下节。
它不扩大平台支持范围或宣称 A2 全部通过。

## A2 GX10 真实 NAS 合成恢复（2026-10-17）

复用 `d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b` 的同一 wheel、GX10 runtime 与本地 ext4，
新增真实 CIFS / SMB 3.1.1 配置的合成保存与丢失恢复证据，完整结果见
[A2_VALIDATION §11](A2_VALIDATION.md#11-2026-10-17gx10-真实-nas-合成恢复)和[公开证据](evidence/2026-10-17-a2-gx10-nas/README.md)。

| 项目 | 本次结果 / 边界 |
|---|---|
| 环境 | GX10 / aarch64 / Linux 6.17.0-1031-nvidia / Python 3.12.3 / SQLite 3.45.1；本地 ext4 |
| NAS profile | 实际 CIFS / SMB 3.1.1；核对 source、root、挂载点与目录绑定，能力预检通过 |
| NAS 保存、独立回读、移除本轮本地副本后仅从 NAS 恢复 | PASS；运行库来自已有独立 wheel 安装 |
| 全部记录与实际内容、旧请求重放、恢复后追加版本 | PASS；独立取回 18 个证据文件核对，原行与内容完整保留 |
| NAS 目录原语竞争 | 两个进程中恰有一个 mkdir 成功；不是完整的同 generation 发布竞争验收 |
| T06、T08 剩余部署故障及硬件耐久性 | 未执行；本次仅得到 filesystem_acknowledged 结论 |
| 其他 NAS、NFS、CIFS 或机器组合 | 未由本次结果验证 |

实际机器路径、共享和挂载配置仅保存在私有部署记录。本次未修改 NAS 服务，也没有创建开机自动挂载；
后续操作前仍应核对挂载是否有效。该单配置结果不代表所有 NAS 或 A2 全部通过。

## A2 GX10 真实 NAS 故障补证（2026-10-17）

上节记录正向恢复当时的边界；随后以同一固定源码、wheel、runtime 和 CIFS 挂载，完成
[九种故障与冲突场景](evidence/2026-10-17-a2-gx10-nas-faults/README.md)。

| 项目 | 本轮结果 / 边界 |
|---|---|
| NAS publish 响应丢失 | 安装态 CLI 退出 9；按发布前摘要只读核对原代成功，没有重新发布 |
| 同代竞争与拒覆盖 | 不同内容的两进程完整发布恰有一个成功；已有不完整目录保持原样 |
| 部分 DB / manifest 写入 | 真实写入 128 bytes 后受控 EIO；not_published，原件保留，verify 拒绝 |
| marker 后 fsync / 回执失败 | 实际 NAS marker 已写入，受控 EIO 返回 unknown；当前内容核对成功，不追认最终同步 |
| 挂载丢失 / 调用前缺失 | private namespace 中实际卸载继承副本；分别返回 unknown / UNSUPPORTED_STORAGE，本地空目录无写入 |
| 独立证据核对 | 138 个文件摘要一致；五个完整 NAS 代、全部 SQL 行和 Blob 与候选一致；原成功代和全局挂载未变 |
| 其他 T06 故障、真实断网、设备断电、资源峰值 | 本轮未覆盖；A2 整体保持 PARTIAL |

初次控制器在产品进程退出后发生系统 Python 导入错误；另存修复版，只接续未执行项，原失败保留。
挂载丢失后遗留一个 marker 临时目录，未自动清理。该结果只覆盖本配置和所列注入点，
不扩大其他机器、文件系统或 NAS 的兼容声明。

## A2 GX10 NAS 发布 I/O 补证（2026-10-17）

沿用同一源码、wheel、runtime 和 CIFS 配置，又完成[十八个发布 I/O 与进程中断场景](evidence/2026-10-17-a2-gx10-nas-io/README.md)。上节未测项只描述该轮边界。

| 项目 | 本轮结果 / 边界 |
|---|---|
| 短写续写及零写入 | 每次真实写入最多 97 bytes 仍能完整发布；写入 128 bytes 后返回 0 被拒绝，原前缀保留 |
| ENOSPC / EDQUOT / EACCES | 在写入、同步、独占创建、marker link 边界受控注入，状态与残留符合预期；不是 NAS 实际满盘、配额或 ACL 认证 |
| marker 前后回读错误及实际损坏 | 三种后置读错误可只读核对完整原代；两种真正改坏字节的原代被 verify 拒绝，损坏原件保留 |
| marker link 前后实际 SIGKILL | 两个普通用户自建进程退出 -9、无响应；原对象分别不完整 / 当前完整，保留 NAS 与本地临时副本 |
| 独立核对 | 278 个文件，五个完整 NAS 代全表与 Blob、十八种精确文件状态通过；六种篡改证据被拒绝 |
| 当前接续 | 优先 NAS 输入下的恢复端 T12 故障与完整矩阵剩余部署证据；已完成发布场景复用 |

本轮六个 unknown 的当前对象有三种结果：三个完整、一个不完整、两个损坏，不能统一视为成功。
原归档、全局挂载、产品安装保持不变。只读核对不追认历史同步，其他配置、真实断网/断电、
NAS 服务端容量/权限策略和资源峰值未由本轮验证；A2 继续为 PARTIAL。
