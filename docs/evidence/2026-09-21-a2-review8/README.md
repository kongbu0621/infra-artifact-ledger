# PR6 第八轮复核与修复证据

固定 D9 **`928b3e0338e7460b9916bf27fc832ce5697b1b7a`**，tree `e77d8b1e2f99f2bb05473503a63ef19a6df9716c`，父提交 `a7f7b278160d5a9951dbaa2944421b99464e7d87`。本目录随后以独立文档提交归档，代码、测试、工具与构建配置仍为固定 D9。

NAS 验收工具原先用普通 json.loads 解析子进程响应，会接受 BOM、UTF-16、重复字段、非有限数、孤立 surrogate 及超过 A2 深度/节点预算的 JSON；深层嵌套又可能在 finally 记录阶段再次触发 RecursionError，覆盖原始命令失败并丢掉记录。D9 复用既有 A2 严格解析器，成功或拒绝均缓存，不重复解析失败内容；异常输出保留原始摘要和命令记录，合法错误 envelope 仍可记录。解析失败转成工具自身的 ExerciseError，不携带可能误导的 not_published 判断。产品 JSON 规则和预算没有改变。

新增 6 项真实合成子进程回归，覆盖异常编码/值、重复键、深度与节点超限、深层 JSON、非零退出叠加坏输出、合法错误 envelope，以及解析失败叠加清理失败。修复前同一最终测试文件记录 failures=8、errors=3（含 subtest，不是 11 个独立方法），修复后 6 项通过。深度 8、1,024 个节点及含 LF 共 65,536 bytes 三个 JSON/外层 envelope 层边界正例通过；其中合成 data 只用于解析预算验证，不作为完整业务响应的语义证据。 [regression-summary.json](regression-summary.json) 绑定命令、工作目录、PYTHONPATH、源码/测试及日志摘要。所有新增用例使用本地合成子进程，不执行真实 NAS 操作。

## 固定源码验证

| 验证 | 发现 | 通过 | 跳过 |
|---|---:|---:|---:|
| Python 3.11.16 源码 | 442 | 422 | 20 |
| Python 3.12.14 源码 | 442 | 422 | 20 |
| 独立安装态 A2 | 276 | 265 | 11 |

12 条固定源码构建、安装、测试、编译和环境命令全部 exit 0。两版本 compileall、A1 安装态闭环、生产 API/CLI 对实际 overlay 的拒绝均通过。20 项资源专项本轮未重跑：A1 9 项保留 D2 历史，A2 11 项保留 D6 历史；安装态跳过的是同一批 A2 专项。历史结果不记作 D9 执行，不相加不同版本/安装态的重复用例。 逐命令记录见 [summary.json](summary.json)。

本轮 wheel 为 67,832 bytes，SHA-256 `def4036d928d8e941d38d2205bccb118be6a87af0a4246b6d415211533957e7b`。构建前后 235 个固定源文件核验一致，17 个运行模块在源码、wheel、新独立 Python 3.11 runtime venv 中一致。17 个产品运行模块与 D8 相同；修复的验收工具不在 wheel 内，复跑需同时取得固定 D9 工具。 详见 [source-integrity.json](source-integrity.json)。构建复用已有 build venv；安装态从源码目录外以 -I 执行，库来自 site-packages，测试与工具来自固定 D9。本地经 Git blob 核验，并非 Git checkout。

[log-index.json](log-index.json) 保存 14 份日志的原始/公开摘要和字节数，receipt 使用原始日志摘要。`<WORKSPACE>`、`<PRIMARY_PYTHON>`、`<PIP_CACHE>` 为脱敏标记。

本轮未另确认新缺陷，不宣称穷尽验证。GX10、受支持本地文件系统正向、真实配额耗尽、真实 NAS、挂载丢失/断网仍 NOT_RUN；资源专项保留 D2/D6 历史，A2 整体 PARTIAL，PR 保持 Draft。五份批准规范、Gate、Owner 决定和旧证据保持原字节。
