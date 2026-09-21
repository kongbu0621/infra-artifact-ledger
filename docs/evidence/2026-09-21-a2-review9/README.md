# PR6 第九轮复核证据

固定输入 **`6bd6acfbe5c35d581891eb87275e1173e17848fc`**，tree `6dd4da925cb97c22eca78a0a3f2165310e048db9`；运行修订仍为 D9 `928b3e0338e7460b9916bf27fc832ce5697b1b7a`。本目录随后单独归档，不作为本轮构建输入。

本轮未确认新缺陷，产品代码、测试、验收工具和构建配置均未修改；运行修订仍为 D9，不新增实现修订号。重新审查严格 JSON、源只读视图与 sidecar、路径/挂载绑定、拒覆盖发布、错误优先级、清理、恢复完整状态及证据来源。没有运行此前被阻断的锁失效重演。 [review-map.json](review-map.json) 记录审查角度、验收编号与回归对应关系；人工复核不冒称穷尽或新增自动化测试。

## 实际执行

| 验证 | 发现 | 通过 | 跳过 |
|---|---:|---:|---:|
| Python 3.11.16 源码 | 442 | 422 | 20 |
| Python 3.12.14 源码 | 442 | 422 | 20 |
| 独立安装态 A2 | 276 | 265 | 11 |
| A2 实际资源边界专项 | 7 | 7 | 0 |
| A2 合法大库语义边界专项 | 4 | 4 | 0 |

14 条固定源码构建、安装、测试、编译和环境命令全部 exit 0。两版本 compileall、A1 安装态闭环与生产 API/CLI 对实际 overlay 的拒绝通过。常规源码的 20 项跳过由 A1 9 项与 A2 11 项组成；本轮另以原始常量显式执行并通过 A2 全部 11 项资源专项，A1 9 项仍保留 D2 历史。安装态 11 项为同一批 A2 专项，专项在源码态执行，不冒称安装态专项。跨版本/安装态结果不相加。 见 [summary.json](summary.json)。

合法 1,073,737,728 和 1,073,741,824 bytes SQLite 均完成 create→verify→restore→check_restore；1,073,750,016 bytes 合法超限库拒绝且不发布。累计 payload 402,653,185 bytes、数据库 403,103,744 bytes 的闭环通过。四组合法 metadata/refs 边界共 12 个 limit−1/= /+1 点通过，限内完整恢复、超限拒绝且检查原状态。JSON/SQL 预检层边界与完整合法库边界分别记录，不把畸形等限输入写成完整流程成功。 详见 [a2-resources.log.txt](a2-resources.log.txt)和 [a2-semantic-resources.log.txt](a2-semantic-resources.log.txt)。资源日志记录每组观测尺寸、耗时与进程累计 RSS；各 suite 有并行执行，耗时不是独占机器性能基准，1 GiB 文件预算也不是内存保证。

固定提交 `6bd6acfbe5c35d581891eb87275e1173e17848fc`（tree `6dd4da925cb97c22eca78a0a3f2165310e048db9`）的 254 个文件在构建前后核验一致；17 个运行模块在源码、wheel、新独立 Python 3.11 runtime venv 中字节一致。wheel 为 67,832 bytes，SHA-256 `8a76cdb3163796327858ba3c54680ee83c095500135c3d159373b06567c46e99`。本轮源码提交包含 D9 及其第八轮证据；后续复核文档提交不是本轮 wheel 构建来源。 见 [source-integrity.json](source-integrity.json)。构建复用已有 build venv；安装态从源码目录外以 -I 执行，库来自新 runtime 的 site-packages，测试/工具来自固定输入；验收工具不在 wheel 内。本地由 Git blob 核验，并非 Git checkout。

[log-index.json](log-index.json) 绑定 14 份日志原始/公开摘要和字节数；summary receipt 使用原始摘要。`<WORKSPACE>`、`<PRIMARY_PYTHON>`、`<PIP_CACHE>` 为公开脱敏标记。

文件系统正向分类仍为 LOGIC_ONLY，实际 overlay / fsync=volatile 仍不受支持。受支持本地文件系统安装态正向、GX10、真实配额耗尽、真实 NAS、实际挂载丢失/断网仍 NOT_RUN；A2 整体 PARTIAL，PR 保持 Draft。当前检查不构成所有时序的穷尽证明。 五份批准规范、Gate、Owner 决定和旧证据保持原字节。
