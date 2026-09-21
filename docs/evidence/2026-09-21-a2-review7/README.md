# PR6 第七轮复核与修复证据

固定实现 D8 **`c9e4cb450c853578b1b4c15eab4a9a6458693d8e`**，tree `5eacace3f61b81909c9b14646742d5102cd5fa00`，父提交 `0de4e57345f6211cbd2f22e01994ab073c9f7344`。随后独立文档提交归档本目录，运行源码、测试、工具和构建配置保持固定 D8。

本轮确认 NAS 验收工具的子进程收尾缺陷：`child_cli` 和 `concurrent_directory_probe` 在原操作已失败后，wait/管道关闭等二次错误会覆盖首错、中断后续管道/其他子进程清理；`child_cli` 的命令记录也可能因此缺失。D8 统一逐项尝试终止、回收和关闭，保留首错及二次失败提示；没有首错时仍报告清理失败。处理 poll/kill 之间进程已退出的情况，不重试已经关闭的管道，并在清理出错时仍保存该次命令记录。此变更不保证操作系统本身无法完成 kill/wait/close 时资源一定释放，也不新增 OS 阻塞 I/O 硬超时承诺。

新增 6 项实际合成子进程回归：异常响应叠加 stdout/stderr/wait 失败；正常响应叠加清理失败；调用者已有已处理异常；第二个子进程启动失败；两个子进程中第一个清理失败；读取失败叠加 poll/kill 退出竞态。核对原错误、进程退出、管道关闭、不重复关闭及命令记录。相同最终测试文件在 D7 工具上产生 10 个 failure 记录（含 subtest，不是 10 个独立测试方法），D8 的 6 项全部通过。故障主要在实际回收/关闭后注入，没有执行 NAS 操作或触及业务数据。 命令、工作目录、PYTHONPATH 和源码/测试/日志摘要见 [regression-summary.json](regression-summary.json)。

## 固定源码验证

| 验证 | 发现 | 通过 | 跳过 |
|---|---:|---:|---:|
| Python 3.11.16 源码 | 436 | 416 | 20 |
| Python 3.12.14 源码 | 436 | 416 | 20 |
| 独立安装态 A2 | 270 | 259 | 11 |

12 条固定源码构建、安装、测试、编译和环境命令全部 exit 0。两版本 compileall、A1 安装态文档闭环、生产 API/CLI 的实际 overlay 拒绝均通过。20 项资源专项本轮未重跑：A1 的 9 项保留 D2 历史，A2 的 11 项保留 D6 历史；安装态 11 项跳过是同一批 A2 专项。历史结果不算作 D8 本轮执行，不相加不同版本和安装态的重复用例。 见 [summary.json](summary.json)。

本轮 wheel `infra_artifact_ledger-0.2.0a1-py3-none-any.whl` 为 67,832 bytes，SHA-256 `c88541e1c29e9f19eb78127baee03d19f45ef4b5397b0011f302e970de583531`。构建前后 215 个固定源文件核验一致，17 个运行模块在源码、wheel、新独立 Python 3.11 runtime venv 中一致。17 个运行模块与 D7 完全相同；修复位于仓库内的验收工具，不在 wheel 中，复跑必须同时取得固定 D8 的工具文件。 逐文件 Git blob 和 SHA-256 见 [source-integrity.json](source-integrity.json)。本地为核验后的源文件集，并非 Git checkout；构建复用已有 build venv，安装态从源码目录外以 -I 运行，库来自 site-packages、测试与工具来自 D8 源码。

[log-index.json](log-index.json) 保存 14 份日志的原始和公开版本各自摘要/字节数，receipt 对应原始日志。`<WORKSPACE>`、`<PRIMARY_PYTHON>`、`<PIP_CACHE>` 为脱敏标记。

本轮未另确认新缺陷，不宣称穷尽验证。资源专项保留 D2/D6 历史；受支持本地文件系统正向、真实配额耗尽、GX10、真实 NAS、挂载丢失/断网仍 NOT_RUN，A2 整体 PARTIAL，PR 保持 Draft。五份批准规范、Gate、Owner 决定及旧证据原字节保留。
