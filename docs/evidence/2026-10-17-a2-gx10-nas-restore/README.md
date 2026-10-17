# GX10：NAS 输入下的恢复故障与只读核对

2026-10-17，复用已核实的 GX10 独立安装，从此前成功的真实 CIFS / SMB 3.1.1 快照只读恢复到
新的本地 ext4 合成目标，完成 **15 个恢复场景和 7 个核对场景**。所列场景 PASS；A2 整体仍为 PARTIAL。
固定源码 `d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b`，wheel SHA-256
`b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293`。
GX10 / aarch64 / Linux 6.17.0-1031-nvidia / Python 3.12.3 / SQLite 3.45.1；安装字节前后匹配同一 wheel。
产品代码未改，原 NAS 快照、挂载和既有业务未改写，所有新目标属于本轮专属合成目录。

## 恢复链路与结果

每次 restore 使用此前独立保存的 manifest 和数据库摘要，NAS 只作为输入。
ENOSPC、EACCES、EIO 在本轮普通用户子进程的指定调用边界注入；真实 NAS 读取、本地复制、SQLite
校验和未选中的系统调用继续执行，没有替换文件系统分类或模拟整个数据库。
它们是错误处理验收，不是实际满盘、服务端 ACL、设备断电或网络断开认证。

| 数量 | 场景 | 原调用 / 后续原目标核对 |
|---|---|---|
| 1 | 正常恢复并观察最终目录同步 | published，退出 0；最终 target fsync 时只有 `ledger.sqlite`，本次暂存已移除；check_restore OK |
| 1 | 实际从 NAS 读到 DB 后注入 EIO | IO_ERROR / not_published；目标尚未创建，check_restore NOT_FOUND |
| 3 | 目标暂存实写 128 bytes 后 ENOSPC；校验前实际改坏暂存文件头；暂存 DB fsync EIO | 对应 IO_ERROR / INTEGRITY_FAILURE / IO_ERROR，均 not_published；产品清理自建暂存，保留空目标，核对 NOT_FOUND |
| 2 | 本地公开硬链接调用返回 ENOSPC / EACCES，未执行实际 link | IO_ERROR / not_published；目标为空，核对 NOT_FOUND |
| 2 | 本地 link 前返回 EIO / 实际 link 完成后返回 EIO | 均 PUBLICATION_UNKNOWN / unknown；当前原目标分别无公开 DB / DB 完整，核对 NOT_FOUND / OK |
| 3 | link 后自建暂存清理 EACCES、最终目标目录 fsync EIO、父目录 fsync EIO | 均 unknown；当前 DB 完整，核对 OK；清理失败保留两个名字指向同一 inode、nlink=2 |
| 2 | 在实际 link 前 / 后暂停，再由父进程 SIGKILL 自建恢复进程 | 均退出 -9，没有产品响应；前者仅留暂存 DB，后者公开 DB 与暂存 DB 为双硬链接；核对 NOT_FOUND / OK |
| 1 | 安装态 CLI restore 的 stdout 读端在启动前关闭 | 实际退出 9、诊断 response channel failed；沿同一目标核对 OK，没有重复恢复 |

15 个恢复场景中，一项返回 published、六项 not_published、五项 unknown、两项被杀无响应、
一项响应通道失败。独立安装态 CLI check_restore 得到七项 OK、八项 NOT_FOUND。
**五个 unknown 中四个当前完整，一个没有公开 DB。** 本地 link 的已知无效果错误与不确定 EIO
采用不同分类；不要把远端 publish 的保守分类机械套到本地 restore。

三个目标暂存目录和两个 scratch 数据库保留。其中两个目标各有两个名字指向同一个真实 inode，
nlink=2；只读核对允许这种中断残留且保持其成员、字节、inode 和链接数不变。
回读 OK 证明当前数据库内容，不能追认原调用最终同步、清理完成或业务启用。

## Sidecar 与边界核对

另外七个新合成目录由本轮已核实数据库字节构造，不计作七次 restore：

- `ledger.sqlite-journal`、`-wal`、`-shm` 各有普通文件和断链两种：六项全部返回 INTEGRITY_FAILURE / not_applicable，退出 6。
- 一个无关断链：check_restore OK；无关条目保留。

各场景前后的成员、字节和所记录的文件元数据均一致。
本轮不声称所有目标竞争时序、其他文件系统或整个 T12/T14 已完全覆盖。

## 独立证据与接续

取回 219 个普通文件，共 1,227,519 bytes，另保留四个断链的目标文本；文件大小与 SHA-256 全部一致。
[audit.py](audit.py) 只用标准库离线核对原 stdout/stderr、退出码、调用参数、故障位置、进程检查点、
精确残留、源摘要、全部 SQL 表行与实际 Blob 字节。原硬链接身份使用远端 lstat 和取回元数据核对，
不从已复制的本地文件推断原 nlink。

输入为私有归档的 `run/`、`snapshot/` 与 `readback-index.json`；结果见
[audit-result.json](audit-result.json)，汇总见 [summary.json](summary.json)。
六份篡改副本分别修改 unknown 分类、硬链接数、清理/同步顺序、sidecar 返回、被杀进程退出码和
NAS 输入字节；同时更新传输摘要及相关报告后仍全部被拒绝，见 [validation-receipt.json](validation-receipt.json)。
执行器、支持代码、核对器和传输脚本编译通过。

原始配置、数据和执行脚本保存在私有接续记录，公开仓库只保存脱敏说明与离线核对器。
下一步按[累计矩阵](../../A2_VALIDATION.md#6-t01t15-累计证据映射更新至-2026-10-17)核对尚缺的部署证据，
优先明确 T09 的部署 RSS/耗时记录和 T11 的目标冲突覆盖；已有适用逻辑与实机结果继续复用。
不重复此前发布、恢复或故障业务，也不把真实断电、其他 NAS 配置变成新增统一前置。
