# PR6 第五轮复核与修复证据

当前实现 **D6 `894cab8b1d55840bbc0cf130c22d4f57d67875d3`**，tree `5f8bfb606ce1d9e1ef84b9f4d96c46192045f81e`；父提交为 D5 证据提交 `689b2d0e82f2d15b4e2bb49c91a0cf7047e6f296`。本目录随后以独立文档提交归档，不改变 D6 运行源码、测试、工具和构建配置。

## 修复与回归

1. **最终核验阶段**：create/publish 共用的封存路径，以及 restore 在最终目录同步后进入 `verify`。最终数据库读取 EIO 保持 `PUBLICATION_UNKNOWN/unknown`，准确报告核验阶段；保留公开对象，故障解除后可只读核对。新增 create/restore 两项回归，同一最终测试文件修复前 3 项中 2 项失败，修复后 3 项全过。
2. **源 journal 入口保护**：预检前后和源 SQLite 打开前，仅用 nofollow stat 检查已有 `-journal`；非普通文件或多链接项提前 `INTEGRITY_FAILURE`。缺失和普通单链接 journal 保持允许，不冻结正常 DELETE 事务的 journal 身份、大小或时间。新增 3 项回归，覆盖 5 种异常形态、缓存预检后的再次检查和普通 journal 正例。RED 日志的 6 个 failure 包含 subtest，不能当作 6 个独立用例。

journal 回归在修复前后都显式阻断源 SQLite 连接边界，验证提前拒绝及文件不变；没有运行 SQLite 读取链接 journal 的锁失效重演。该保护与 [SQLite 的 POSIX 锁注意事项](https://www.sqlite.org/howtocorrupt.html#posix_advisory_locks_canceled_by_a_separate_thread_doing_close_)一致；仍依赖已冻结规范中的管理性稳定前置，不声明消除 stat 到 SQLite open 之间任意管理性替换的竞态。

两组 RED/GREEN 是组件修复前后的实际实验，源码及最终测试摘要、命令、工作目录、解释器、PYTHONPATH 和日志文件映射见 [regression-summary.json](regression-summary.json)。D6 固定源码的完整回归独立记录于 [summary.json](summary.json)。

## 固定源码验证

| 验证 | 发现 | 通过 | 跳过 |
|---|---:|---:|---:|
| Python 3.11.16 源码 | 429 | 409 | 20 |
| Python 3.12.14 源码 | 429 | 409 | 20 |
| A2 资源专项 | 7 | 7 | 0 |
| A2 合法 metadata/refs 边界 | 4 | 4 | 0 |
| 独立安装态 A2 | 263 | 252 | 11 |

14 条构建、安装、测试、编译和环境命令均 exit 0。两版本 compileall、A1 安装态闭环和生产 API/CLI 的实际 overlay 拒绝验证通过。A2 的 11 项资源测试本轮单独执行；A1 的 9 项大容量/响应专项本轮未重跑，保留 D2 历史证据。不同版本、安装态和定向回归有重叠，数量不相加。

实测仍包含 1 GiB 合法 SQLite、402,653,185 bytes payload，以及 metadata bytes/rows、refs bytes/rows 的十二个合法边界点。文件系统分类模拟的正向生命周期为 **LOGIC_ONLY**。

本地 D5 文件集加四文件变更形成 D6，先创建固定 Git 提交对象，再核对远端 tree。构建前后 **172 文件**内容摘要一致；**17 个运行模块**在源码、wheel、独立安装环境中字节一致。来源和制品摘要见 [source-integrity.json](source-integrity.json)。本次构建复用已记录的 build venv，新建独立 Python 3.11 runtime venv；安装态从源码目录外以 `-I` 执行，库和源预检辅助入口来自 site-packages。

- wheel：`infra_artifact_ledger-0.2.0a1-py3-none-any.whl`
- 大小：67,823 bytes
- SHA-256：`3d65460945ae24c91ea86ebe5c79e7e779acae6d083dbe89cc634b7c0a163217`

## 日志与边界

[log-index.json](log-index.json) 为 18 份公开日志保存原始与脱敏版本各自的 SHA-256/字节数及路径映射。receipt 摘要对应原始日志，不冒充脱敏文件摘要。`<WORKSPACE>`、`<PRIMARY_PYTHON>`、`<PIP_CACHE>` 是脱敏位置标记。

历史 review4 的 NAS receipt 保留原始日志名；其中 `probe-binding-before.log` 对应历史目录的 `nas-binding-before.log.txt`，`probe-binding-after.log` 对应 `nas-binding-after.log.txt`。从对应历史固定源码的仓库根复跑该 receipt 的模块命令时，应设置 `PYTHONPATH=src:tests`；原 receipt 未记录当时 cwd/PYTHONPATH，不将此复跑说明补写为已核实的历史执行事实。历史原始记录保持原字节；本轮 NAS/存储定点复核与全套回归有重叠，不另计新增用例。

受支持本地文件系统安装态正向、GX10、真实 NAS、真实挂载丢失/断网均 **NOT_RUN**；A2 整体验收仍为 **PARTIAL**。PR 保持 Draft。五份批准规范、Gate 和 Owner 决定未修改。
