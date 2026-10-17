# GX10 / CIFS：短写、空间、权限、回读与进程中断

2026-10-17，复用已有 GX10 独立安装与真实 CIFS / SMB 3.1.1 挂载，在新专属合成目录完成
十八个发布场景及各自的独立 CLI verify。**所列场景 PASS，A2 整体仍为 PARTIAL。**
源码仍为 `d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b`，wheel SHA-256
`b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293`。
GX10 / aarch64 / Linux 6.17.0-1031-nvidia / Python 3.12.3 / SQLite 3.45.1，本地 ext4。
运行前后安装字节匹配同一 wheel；没有修改产品代码或重新执行此前发布、恢复与故障场景。

## 故障模型与结果

空间、配额、权限和读错误在普通用户验收进程的指定 syscall 边界注入。
真实 NAS 文件创建、未被选中的读写/同步/链接以及产品的续写、状态分类和校验逻辑继续执行。
没有替换文件系统分类或 SQLite，也没有耗尽 NAS、调整服务端配额或 ACL。
这些结果证明此配置下的错误处理，不能称为服务端真实磁盘耗尽或权限策略认证。

| 场景数 | 场景 | 发布返回 / 当前原对象核对 |
|---|---|---|
| 1 | 每次真实 write 最多写 97 bytes | published，退出 0；DB 507 次、manifest 8 次、marker 2 次写入后内容完整，verify OK |
| 1 | DB 实写 128 bytes 后 write 返回 0 | `IO_ERROR / not_published`；保留 128-byte 原前缀，verify 拒绝不完整快照 |
| 4 | DB 首次写 ENOSPC、manifest 写 128 bytes 后 ENOSPC、临时 marker 写 32 bytes 后 ENOSPC、DB 写完后 fsync ENOSPC | 均 not_published；分别保留空 DB、部分 manifest、无公开 marker 的 DB/manifest、完整 DB；verify 全部拒绝不完整快照 |
| 1 | DB 首次写 EDQUOT | not_published；空 DB 保留，verify 拒绝 |
| 1 | DB 独占创建返回 EACCES | not_published；独占 generation 为空，verify 拒绝 |
| 1 | 进入远端 marker link 调用边界返回 EACCES | `PUBLICATION_UNKNOWN / unknown`；当前回读缺 marker。远端调用的保守 unknown 不改写为成功，也不自动重发 |
| 2 | marker 前 DB / manifest 实际回读后注入 EIO | not_published；DB/manifest 完整但无 marker，verify 拒绝 |
| 3 | marker 后 DB / manifest / marker 实际回读后注入 EIO | unknown；随后独立 verify 确认当前三文件完整，不追认原调用最后同步 |
| 2 | marker 后实际改坏新合成 DB 最后一字节 / manifest 第一字节 | unknown；独立 verify 均返回 `INTEGRITY_FAILURE`，精确损坏原件保留，没有修补成成功 |
| 2 | 自建发布进程在实际 marker link 前 / 后暂停，再由父进程 SIGKILL | 均实际退出 -9、没有产品响应；前者缺 marker、后者当前内容完整。分别 verify 为不完整 / OK，不伪造被杀进程的成功或 unknown envelope |

共一项 published、九项 not_published、六项 unknown、两项被杀后无响应。
独立 verify 得到五项 OK、十一项 `INCOMPLETE_SNAPSHOT`、两项 `INTEGRITY_FAILURE`。
**六个 unknown 中仅三个当前完整；另一个不完整、两个已损坏。**
因此，unknown 后必须核对原 ID 和发布前保存的摘要，不能直接视为成功或自动重新发布。

每个场景只发布一次。前后核对全部 NAS 文件和成员，verify 没有改动原对象。
临时 marker 的 ENOSPC 场景由产品清理其自建临时目录；32 bytes 的写入量来自实际 syscall
返回记录，清理后没有保留该前缀文件。两个 SIGKILL 场景各留下一个 NAS marker 临时目录和
一个本地 scratch 数据库，全部保留为中断原件，不冒充清理完成。

## 独立证据核对

取回 278 个文件，共 1,532,951 bytes，大小和 SHA-256 全部匹配。
此前 T15 成功归档及上一轮 NAS 故障原件与其独立保存的摘要一致，主机原挂载和安装未变。

[audit.py](audit.py) 只用标准库离线读取私有副本，不连接 NAS 或执行产品业务。它核对：

- 原 stdout/stderr、退出码、故障 errno、marker 阶段、两个被杀进程的 PID 与检查点。
- 三类文件的精确短写次数、每个场景的完整成员、空文件、前缀、损坏偏移和残留。
- 五个完整 NAS 代的全部 SQL 表行和 Blob 实际字节与源快照一致。
- 原独立摘要、此前归档、实际 CIFS 绑定及逐场景 verify 前后文件一致。

输入结构为 `run/`、`nas/`、`preserved-t15/`、`preserved-faults/` 和 `readback-index.json`。
结果见 [audit-result.json](audit-result.json)，汇总见 [summary.json](summary.json)。

另在六份副本中分别伪改短写次数、远端 link 状态、损坏原件、被杀进程退出码、errno 和旧归档；
即使同步修改传输摘要及相关报告，核对器仍逐项拒绝。原证据不变，见
[validation-receipt.json](validation-receipt.json)。执行器、复用支持代码与核对器均通过编译检查。

本轮补齐此前明确列出的发布 I/O 场景；不表示穷尽所有时序、所有文件系统或全部 A2 验收。
真实设备断电、断网、NAS 实际容量/配额/ACL 故障及资源峰值未由本轮验证；只读回读不能追证
历史最终同步。接续核对 T01–T15 剩余部署证据，优先处理 NAS 输入下的恢复端 T12 故障。
原始数据库、payload、配置、端点及执行脚本保存在私有部署记录，公开仓库仅保留脱敏证据与离线核对器。
