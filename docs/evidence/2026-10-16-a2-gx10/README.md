# GX10 安装态 A2 本地闭环（2026-10-16）

固定修复源码 `d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b` 在 GX10 的真实 ext4、Linux aarch64、Python 3.12.3 / SQLite 3.45.1 上完成 API/CLI 各四项 create → verify → restore → check_restore，八项 PASS。整个 A2 仍为 **PARTIAL**；真实 NAS 保存、丢失恢复与 T06/T08 部署故障未执行。

首次尝试使用原 `6707a1b` 及保留 wheel，在创建合成目录前因 mountinfo 解析失败退出 1，未产生合成数据库。GX10 的正常挂载表包含 namespace 文件系统的非绝对 root；旧解析器因此拒绝整张表，连同无关的 ext4 目标一起失败。与现场内核主版本一致的上游 Linux v6.17 [`nsfs_show_path` 实现](https://raw.githubusercontent.com/torvalds/linux/v6.17/fs/nsfs.c)采用 namespace 名称与 inode 的形式输出 root；固定版本避免后续 `master` 变化改写本次依据，也不把上游标签误称为发行版内核的精确源码。修复仅在 fs_type 为 nsfs 时接受该语法，保留记录供最深挂载和 fd/mount_id 核对；nsfs 仍不能作为 Ledger 存储，其他类型的相对 root 仍拒绝。

本次从固定 Git 提交导出 280 个文件，在独立本地构建环境生成新 wheel，随后在 GX10 新建独立 runtime 离线安装。原失败目录、旧 A1 安装及数据保留。17 个模块的固定源码、wheel 与安装字节一致；GX10 的 Python 对 17 个模块与两个验收脚本进行内存编译。业务从源码目录外以 `-I -B` 运行，使用真实生产 API、CLI、SQLite 和存储检查。runtime 普通文件与目录已移除写权限；前后核对的是模块、解释器、venv 配置和 console script 绑定，并非完整 runtime inode 清单。

执行退出 0、业务 stderr 为空、完整八项报告通过核对。原 A1 与首次失败现场共 27 个已登记文件保持一致，适用元数据及旧 Git head/status 一并比较。构建日志中的 pip 缓存权限警告保留于私有原件，不影响成功构建；不宣称所有日志无警告。

随后取回 24 个文件并核对远端/本地大小与摘要。只读核对源库、两份快照及两份恢复库：SQLite 完整性、外键、结构、五张表全部行、预期身份与引用、39 字节 Blob 及两条请求指纹均通过。恢复库字节等于快照，源库的物理字节单独记录；不要求 SQLite backup 后与源库逐字节相同。两份快照的 marker → manifest → 数据库摘要链一致。复核前后保留文件摘要不变，没有重放业务或写数据库。

新增三项回归覆盖 namespace root 与 ext4/NFS 共存、保留不受支持挂载防止错误回退、畸形 root 与错误类型拒绝。旧解析器复现两项新测试错误；同时发现旧的同尺寸源改写测试依赖文件系统时间戳跳变，已改为显式注入可观察 mtime 变化。后者只验证可观察变化的检测，不声称能发现所有同尺寸并发改写。修复后存储套件在 Python 3.11/3.12 各 25 项通过；Python 3.11 快照套件发现 279 项、通过 267 项、跳过 12 项（11 项按开关启用的资源专项及 1 项不适用于当前 ext4 的非支持文件系统测试）。保留的真实挂载表 53 行均可解析，其中 3 行 nsfs 保留。两组测试含重复覆盖，不相加。

| 记录 | 内容 |
|---|---|
| [summary.json](summary.json) | 固定版本、首次失败、当前结果、测试计数与未测范围 |
| [python312.json](python312.json) | 实际八项报告、退出与原文件保护；仅报告中的两处机器路径替换为占位符 |
| [source-integrity.json](source-integrity.json) | Git/tree/archive、构建工具、wheel、17 个安装模块和脚本绑定 |
| [readonly-recheck.json](readonly-recheck.json) | 取回文件摘要、五库表行、Blob、身份、引用和幂等指纹复核 |
| [snapshot-seals-recheck.json](snapshot-seals-recheck.json) | 两份封存摘要链、五库结构与外键复核 |
| [log-index.json](log-index.json) | 私有原始日志及检查器的大小与 SHA-256 |

原报告的 `CALLER_ASSERTED_NOT_GIT_ATTESTED` 等限定保持原样，Git 来源由单列的构建绑定补充。公开记录不包含实际主机地址、配置、凭据、目录或原始数据库。每轮只有单版本/单 Blob 和两条成功幂等记录，没有多父版本、Manifest、ProvenanceLink、ImportReceipt 或恢复后追加版本，不能代表完整 T13。未测资源峰值、NAS 故障或硬件耐久性；现有 x86_64 结果仍属于原源码与 wheel，不追授为本次新包的安装态结果。
