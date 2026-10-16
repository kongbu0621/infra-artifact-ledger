# 2026-10-16 安装态本地 A2 实测

结果：所列 Linux x86_64 / ext4 / Python 3.11、3.12 环境的本地合成闭环 **PASS**，整个 A2 **PARTIAL**。本目录从已保留的两次真实执行整理而来，补录没有重新执行业务。不是 GX10、NAS 或 Local Hand 常驻服务验收。

| 文件 | 内容 |
|---|---|
| [summary.json](summary.json) | 两版实际环境、结果与未执行项 |
| [python311.json](python311.json) | Python 3.11.15 的原报告投影、三阶段实际退出与采集结果 |
| [python312.json](python312.json) | Python 3.12.14 的相同记录；使用独立 runtime 和新合成目录 |
| [source-integrity.json](source-integrity.json) | 固定提交、wheel 大小/摘要、17 个模块及 checkout 脚本的 Git blob 和 SHA-256 |
| [log-index.json](log-index.json) | 私有原始报告、stdout、安装日志的摘要，公开投影的摘要及转换说明 |
| [readonly-recheck.json](readonly-recheck.json) | PR #8 复核追加的十个数据库摘要、快照绑定、实际数据规模和双解释器编译结果 |

两轮均使用 `infra-artifact-ledger 0.2.0a1`，SQLite 3.37.2。执行前 `findmnt` 确认真实 ext4，登记时再次只读确认。固定脚本使用产品原有挂载识别和恢复 API，没有替换分类函数或使用存储 mock。

## 来源与实际调用

复用保留的 wheel，不重新构建；对应固定 Ledger 输入为 `6707a1b521c9c4718674620e6c584656bd434e4c`。本次逐模块比较 Git blob、源码、wheel 与两份安装，所核对的 18 个文件在文档基线 `37ed2ea` 中字节相同。验收脚本来自该固定 checkout，不在 wheel 内。脚本自身的来源声明和 wheel 证据限制原样保留，独立字节核对不冒充可复现构建证明。

两份 runtime 均以 `venv --without-pip` 新建，使用本地 wheel 的 `pip --python <RUNTIME>/bin/python install --no-index --no-deps --no-compile` 安装。安装成功；两份私有安装日志均含不可写 pip 缓存警告，未隐藏或改写。执行时采用普通用户、umask 0077，runtime 文件/目录去除写权限。包装器来自独立安装的 Local Hand `d04fcf5`，调用其真实输入校验、runtime 探针、匿名临时文件检查、进程采集和严格报告解析；没有提交常驻服务任务。

业务调用的路径已作占位替换，其余参数保持实际值：

```text
<RUNTIME>/bin/python -I -B <SOURCE>/tests/installed_snapshot_walkthrough.py \
  --scratch-parent <TASK_WORK>/local-parent \
  --source-commit 6707a1b521c9c4718674620e6c584656bd434e4c
```

固定脚本的 CLI 子进程使用同一 `<RUNTIME>/bin/python -I <RUNTIME>/bin/artifact-ledger snapshot ...`，没有继承源码导入路径。API 和 CLI 分别执行 create、verify、restore、check_restore；总计每轮八项。程序还检查恢复 Blob、原幂等请求重放、summary 和源合成库原始字节不变。

## 保留结果与核对

两轮 runtime 探针、临时目录检查和业务进程均退出 0，stderr 为空，采集未截断、双流完整。业务报告必须恰含八项 PASS，零退出码不能单独建立成功。运行后完整 runtime 清单、所选源码和 wheel 与运行前一致。首次登记以只读 immutable SQLite 连接比较原合成库、各两份快照及两份恢复库的全部 SQL 表行；当时的读取前后摘要记录覆盖源库和恢复库，未包括快照数据库。

PR #8 复核追加 readonly-recheck：每轮五个数据库都记录读取前后 SHA-256，检查完整性、外键、结构与全表行；四份快照均核对 marker、manifest 和数据库摘要，保留文件内容及元数据未变。快照按目录名排序编号，`snapshot_1/2` 不推定对应 API/CLI 顺序。这是当前内容的只读核对，不是重跑原操作，也不能证明历史 fsync 或断电耐久性。两个原始执行投影和 log-index 的原摘要保持不变。另将 17 个模块及固定脚本在 Python 3.11.15、3.12.14 中各编译一次，只调用内存 `compile()`，不导入、执行或写入字节码。

每轮 fixture 为 1 个 Artifact、1 个 Version、1 个 ContentRoot、1 个 Blob（39 字节）和 2 条成功幂等记录；数据库均为 49,152 字节。没有 Manifest、ProvenanceLink、ImportReceipt、多父版本或恢复后新增版本。全表比较仅覆盖实际存在的数据，不能据此宣称缺席的记录类型、完整 T13 实机范围或资源边界已经验证。

公开 JSON 是明确标注的投影：原报告的 `module_path` 替换为 `<RUNTIME>` 相对位置，`work_dir` 替换为 `<RUN>`，临时检查阶段名去掉由私有路径派生的标识。它们不是原始 stdout 的逐字副本。原始 JSON、日志、运行程序和合成数据在私有目录保留，摘要映射见 log-index；没有上传机器绝对路径、凭据、内部服务配置或数据库。

这次小型正向数据不代替已有资源边界专项；未测量 RSS，也未通过 systemd 强制计划中的资源预算。先前 Cloud 测试、故障与大容量结果保留其原环境归属。GX10、真实 NAS 能力与恢复、T06/T08 部署故障、挂载丢失/断网仍未执行。summary 的 `not_run` 列表记录这些 A2 剩余现场项，不是完整验收矩阵的替代；`outside_ledger_a2_acceptance` 单列未验的硬件掉电、systemd 隔离及常驻服务 A2 全链，不把它们新增为 Ledger A2 的完成前置。
