# A1 兼容与运行边界

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


## A2 0.2.0a1 补充状态

以上是 A1 0.1.0a1 历史证据；A2 当前固定修订为 D8 `c9e4cb450c853578b1b4c15eab4a9a6458693d8e`，tree `5eacace3f61b81909c9b14646742d5102cd5fa00`。D1–D7 历史、D8 构建与运行证据见 [A2_VALIDATION](A2_VALIDATION.md)，不能跨提交挪用结果。

本轮环境为 Linux x86_64 / Python 3.11.16、3.12.14 / SQLite 3.53.1；215 个源文件按固定 D8 tree 核验，17 个运行模块的源码、wheel、安装字节一致。复用已有 build venv，采用 system-site-packages 的构建工具；新建独立 Python 3.11 runtime venv，Python 3.12 复用此前环境。本地是经 Git blob 核验的文件集，并非 Git checkout；安装态从源码目录外以 -I 运行，测试与验收工具来自固定源码。

12 条固定源码构建、安装、测试、编译和环境命令全部 exit 0。两版本 compileall、A1 安装态文档闭环、生产 API/CLI 的实际 overlay 拒绝均通过。20 项资源专项本轮未重跑：A1 的 9 项保留 D2 历史，A2 的 11 项保留 D6 历史；安装态 11 项跳过是同一批 A2 专项。历史结果不算作 D8 本轮执行，不相加不同版本和安装态的重复用例。

D8 两版本源码各 436 项发现、416 项通过、20 项跳过；安装态 A2 270 项发现、259 项通过、11 项跳过。新增 6 项子进程清理回归，覆盖首错、二次清理失败、两个子进程与退出竞态，见 [本轮证据](evidence/2026-09-21-a2-review7/README.md)。产品运行模块与 D7 相同，工具文件不在 wheel 中，复跑须同时取得 D8 工具。已有配额分类、源 journal、最终核验阶段、真实输出通道与目录绑定回归继续通过；管理性稳定前置、已有损坏缓冲等边界不变，未运行被阻断的锁失效重演。

A2 本地候选文件系统为 ext4/xfs/btrfs，NAS profile 为显式核实的 nfs/nfs4/cifs；类型名称不等于兼容认证。实际 Cloud overlay（fsync=volatile）仍被生产 API/CLI 拒绝，正向模拟仅 LOGIC_ONLY。

GX10、受支持本地文件系统安装态正向、真实 NAS 及 T06/T08 实际部署演练仍 NOT_RUN，整体 PARTIAL。资源可达性证明见 [A2_RESOURCE_BOUNDARIES](A2_RESOURCE_BOUNDARIES.md)，不替代部署资源或耐久性认证。上文 Windows/macOS/其他未验组合边界保留。

A2 封存文件上限 1 GiB，其他预算与合作式 300 秒期限见 [A2 接口](a2/INTERFACE_PROFILE.md)。读回核对不能证明过去发布同步或设备断电耐久性。构建/部署见 [A2_RUNBOOK](A2_RUNBOOK.md)。
