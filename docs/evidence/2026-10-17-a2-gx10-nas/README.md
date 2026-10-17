# GX10 → NAS → GX10 合成恢复证据

2026-10-17，固定实现 `d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b` 在已有 GX10 独立
runtime 中完成一次真实 NAS 合成保存与丢失恢复，结果 **PASS**。这补齐本次配置下的
T15 正向链路；A2 整体仍为 **PARTIAL**，剩余故障项见下文。

## 来源与环境

复用 [GX10 本地验证](../2026-10-16-a2-gx10/README.md) 的固定源码、wheel 和安装。
wheel SHA-256 为 `b53461203a4b10c0e5df887af71185ae5798ddc9abcfaa37082ca2a9805ce293`。
执行前复核 wheel 及固定验收脚本摘要，验收工具再次核对安装包与 wheel 字节一致。
工具自身的 `source_commit` 为调用者声明；来源证明结合已有构建链和本次执行前核对，
不能只凭参数或同名版本号认定来源。

| 项目 | 本次实际范围 |
|---|---|
| 软件 | `0.2.0a1`，运行包来自独立 venv 的 site-packages |
| 执行主机 | GX10 / Linux 6.17.0-1031-nvidia / aarch64 / glibc 2.39 |
| Python / SQLite | 3.12.3 / 3.45.1 |
| 本地数据库、快照及恢复目标 | ext4；均为新合成范围 |
| NAS | 真实 CIFS 挂载，SMB 3.1.1；使用现有共享下新建的专属目录 |
| 存储 profile | `mounted-posix-v1`；实际 source、root、mount point 与目录绑定经核对 |
| 同步结论 | `filesystem_acknowledged`；不代表 NAS 断电耐久性 |

本轮未修改产品源码或 NAS 服务配置，没有创建开机自动挂载；临时挂载与归档位置保存在私有
接续记录中。密码通过原生工具的隐藏终端输入，没有保存；旧认证失败及随后成功分别保留。

## 实际链路

使用固定源码的 [a2_nas_exercise.py](https://github.com/kongbu0621/infra-artifact-ledger/blob/d4ba0c6bd40fe06fa31d473e6f1f37d96d6bf23b/tools/acceptance/a2_nas_exercise.py)，
以已安装 runtime 的 `-I` 模式执行。脚本不属于 wheel，运行库来自 wheel 安装。

1. 构造包含版本分支、多父合并、内容复用、Manifest、来源、已有导入和成功幂等记录的合成库。
2. 创建本地快照，保存独立的 snapshot ID、manifest / 数据库摘要和不可直接还原数据库的预期清单。
3. 在专属 NAS 子树完成能力预检，包括独占目录、O_EXCL、拒覆盖硬链接、文件/目录同步与重开回读；
   两个独立进程争用同一目录，实际为一个创建成功、一个已存在。
4. 发布到 NAS；独立 CLI 进程 verify 退出 0，核对事先保管的身份、摘要和 summary。
5. 按冻结的路径、inode、挂载与内容清单，只移除本轮 disposable 下的源库、导入源、本地快照与暂存。
6. 全新 CLI 进程仅从 NAS 恢复，退出 0；写入前 `check_restore` 与全部 SQL 行、Blob 内容核对通过。
7. 原请求重放没有增加记录；随后在原 Artifact 上追加新版本和 29 bytes 新内容，父引用与精确增量匹配。

整个脚本退出 0，记录耗时约 1.59 秒；这是该小型 fixture 的一次执行观测，不是性能基准。
快照中的 7 个幂等记录、1 个导入回执保持；追加新版本后只有幂等记录增至 8，导入回执仍为 1。

| 保留对象 | NAS 快照 / 写入前恢复 | 追加一个新版本后 |
|---|---:|---:|
| Artifact / Version | 2 / 4 | 2 / 5 |
| records / refs / operations SQL 行 | 16 / 21 / 7 | 19 / 25 / 8 |
| Blob 数量 / 实际字节 | 3 / 103 | 4 / 132 |

## 独立复核与证据

[summary.json](summary.json)记录安装身份、操作结果、摘要、范围和未测项。
在恢复和新增版本完成后，另经只读通道取回 18 个证据文件，逐项核对大小与 SHA-256；
现场复核 disposable 已不存在，原 NAS 挂载绑定保持。取回发生在恢复完成之后，不是恢复输入。

[audit.py](audit.py)不导入产品库，仅用 Python 标准库读取私有证据副本：核对每个文件摘要、
manifest/marker 与独立摘要、SQLite 完整性/外键、所有原始 SQL 行和每个 Blob，
并确认恢复库保留每一条原记录和实际内容，只增加预期版本、关系、操作和 payload。
其实际输出见 [audit-result.json](audit-result.json)。

```sh
python3 audit.py /path/to/private/evidence-archive
```

公开仓库不包含该私有输入目录。实际挂载配置、机器路径、终端原始记录、合成数据库及原始报告
保存在操作者持久目录，由私有 continuation 入口定位；公开摘要保留原报告和取回清单摘要。
独立核对只读取完成后的证据，不重放 create、publish、restore 或原业务。

## 未覆盖事项

- T06 的真实部署/NAS 发布中断、空间、权限和回读故障仍未执行。
- T08 本地 create/restore 丢响应见[单独证据](../2026-10-17-a2-gx10-response-loss/README.md)；
  NAS publish 丢响应、挂载丢失和真实断网仍未执行。
- 两进程 mkdir 探针只证明这次目录原语竞争；不能替代两个 publish 争同一 generation 的完整实机验收。
- 未测量本次资源峰值；未执行 NAS 断电、硬件故障、其他 NAS/NFS/CIFS 配置或 Local Hand 常驻端到端验收。

该结果允许接续剩余故障项，不代表所有 A2 正反例、所有 NAS 配置或生产业务资格已经通过。
