# GX10 / CIFS：发布故障与冲突证据

2026-10-17，在已有 GX10 安装与真实 CIFS / SMB 3.1.1 挂载上，新建专属合成范围，完成下表九种场景。
**这些场景 PASS，A2 整体仍为 PARTIAL。** 本轮没有修改 Ledger 产品代码、重新安装或重放此前 NAS 恢复。
固定源码 `d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b`，wheel SHA-256
`b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293`。
GX10 / aarch64 / Linux 6.17.0-1031-nvidia / Python 3.12.3 / SQLite 3.45.1，本地 ext4。
运行前后安装包字节与同一 wheel 匹配；实际路径和配置保存在私有接续记录。

## 实际覆盖

| 场景 | 操作与结果 | 失败后的实际对象 |
|---|---|---|
| NAS publish 的 stdout 丢失 | 安装态 CLI 启动前关闭管道读端；退出 9，响应通道失败 | 按发布前已保管的 ID、manifest/database 摘要，只读 verify 原 NAS 对象成功；未重新发布 |
| 两进程争用同一代、内容不同 | 两个候选库产生同 ID、不同摘要的完整本地快照；两独立进程都到达目标独占 mkdir 前才放行；一个成功、一个 `TARGET_EXISTS` | 胜者为 B；NAS 三个文件及全部 SQL 行、Blob 字节只与 B 一致，无混合 |
| 既有不完整目录 | 预置本轮独占目录和 sentinel；CLI 返回 `TARGET_EXISTS / not_applicable`，退出 4 | 原 sentinel 和成员保持不变，没有续传或自动清理 |
| 数据库写入中断 | 真实 NAS 写入并同步 128 bytes 后注入 EIO；`IO_ERROR / not_published`，退出 9 | 保留数据库前缀，无 marker；独立 CLI verify 返回 `INCOMPLETE_SNAPSHOT` |
| manifest 写入中断 | 真实 NAS 写入并同步 128 bytes 后注入 EIO；同上 | 数据库已写入，manifest 为原内容前缀，无 marker；verify 拒绝不完整快照 |
| marker 后最后目录同步失败 | 真实 marker link 完成，generation 的最后 fsync 注入 EIO | `PUBLICATION_UNKNOWN / unknown`，退出 9；只读核对当前完整内容成功，不追认发布者最后同步 |
| marker link 回执丢失 | 真实 NAS link 成功后，在调用边界注入 EIO | 同样返回 unknown；原对象完整，只读核对成功；这是受控回执故障，不是真实网络断开 |
| marker 后挂载消失 | 普通用户发布进程到达真实 link 后暂停；私有挂载命名空间实际 lazy unmount，再绑定空 ext4 影子目录，随后继续原进程 | 原进程返回 unknown；主机原 CIFS 挂载未变，经原挂载只读核对原代成功；保留一个未清理的 marker 临时目录 |
| 调用前挂载缺失 | 另一个私有命名空间中，以同名空 ext4 目录替代 CIFS；普通用户 CLI 调用 | `UNSUPPORTED_STORAGE / not_published`，退出 3；本地影子目录无新增文件，没有回退写入 |

故障注入仅在验收进程中作用于明确的 I/O 边界；文件系统分类、挂载身份、SQLite 和真实 NAS
文件操作均未替换。竞争只在独占 mkdir 前设置会合点，不替换 mkdir。挂载命名空间递归设为
private 后才卸载继承副本，工作进程 UID 为 1000；没有全局卸载、修改 NAS 服务或断开网络。

两份候选库的所有记录、关系、操作和 Blob 实际内容都参与比较。五个完整 NAS 代与对应候选
逐文件、逐表核对；两份不完整文件另核对精确前缀。此前 T15 成功代的三文件保持不变，
并与此前独立保管的 manifest/database 摘要一致。挂载丢失留下的临时 marker 原件保留；
当前 verify 成功不表示该次发布完成了清理或最后同步。

## 验收控制器失败与接续

首次挂载丢失的产品进程已经退出 9 并返回 `PUBLICATION_UNKNOWN`；随后控制器使用系统 Python
检查返回时，因为仅为查询退出码映射而导入 Ledger，触发 `ModuleNotFoundError`。这是验收代码
的依赖错误，原失败报告保持 FAIL，没有改写成成功。

修复将返回检查改为只依赖标准库。确认原工作进程已退出、结果已保存、主机挂载正常后，
新命名空间只执行此前尚未调用的“调用前挂载缺失”检查；原 marker 发布没有再次执行。
最终从主机原挂载只读核对原代。初版与修复版脚本、计划、失败与接续记录全部私有保留，
其摘要见 [summary.json](summary.json)。未向系统 Python 安装 Ledger，也未更改产品运行环境。

## 独立核对与边界

取回 138 个文件，共 738,283 bytes；每个文件大小、SHA-256 与远端只读清单一致。
[audit.py](audit.py) 使用标准库离线读取私有副本，核对进程原始输出、冲突胜者、部分文件、
数据库全行与实际 Blob、命名空间与挂载、失败原件和此前成功代。输入目录结构为
`run/`、`nas/`、`preserved/` 和 `readback-index.json`；它不执行产品业务。
结果见 [audit-result.json](audit-result.json)。

对四份独立副本分别改坏 NAS 内容、把 unknown 伪改成成功、改掉挂载类型、伪改命名空间，
并同步更新传输摘要；核对器仍分别拒绝，证明它不只检查传输哈希。
原证据保持不变，详见 [validation-receipt.json](validation-receipt.json)。

本轮覆盖 T07 的所列 NAS 冲突、T05/T06/T08 的上述子项。其他中断位置、短写恢复、空间不足、
权限与回读失败尚无本轮部署补证；不宣称所有 T01–T15 或所有 NAS 组合通过。
未执行真实断网、设备断电或资源峰值测量；只读核对证明当前字节，不证明历史 fsync 完成或
设备缓存耐久性。原始数据库、payload、端点、挂载路径和凭据不发布到仓库。
