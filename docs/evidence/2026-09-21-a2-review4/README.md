# D5 固定源码验证与修复回归

源码 `8506574083a39339cb63d472c89703b4affaf4e8`，tree `76728c8d5cdd8d5777cab01bc654652f2a9e9118`，父提交 D4 `8a11030fd03f4d0bfb291786d1c557ba8fc94f89`。本目录是随后提交的证据，不是 wheel 的源码提交。

## 证据读取

- [summary.json](summary.json)：14 条固定源码验证命令、参数、环境开关、退出码和耗时，全部 exit 0；源码/安装态/资源的发现、通过、跳过分别记录。
- [log-index.json](log-index.json)：18 份日志的原始字节及公开字节摘要。公开日志只替换工作目录、解释器设施路径和 pip cache 根路径；普通测试内容及结果保持。
- [source-integrity.json](source-integrity.json)：148 个输入文件的 Git blob 与固定远端 tree 一致；17 个包模块在源文件、wheel、独立安装后逐字节相同。
- [regression-summary.json](regression-summary.json)：本轮 CLI/NAS 针对性 RED→GREEN 实验的源码和测试范围。修复前日志含预期失败，不应计为修复后套件失败；subtest 失败记录不等于独立用例数。

源码通过固定 GitHub 提交的文件集及逐 Git blob 校验落地，没有声称建立本地 Git checkout，也没有虚构 git diff 检查。build venv 继承已有构建工具，准确版本见 [build-environment.log.txt](build-environment.log.txt)；两个 runtime venv 独立。安装态用 `-I` 从源码目录外运行；被测库位于 site-packages，测试和 NAS 验收工具来自固定源码文件集。

## 范围

本轮新增 11 个输出通道测试和 3 个 NAS 探针绑定测试。输出通道使用真实子进程、pipe、`/dev/full` 和缺失 fd；标准 CLI 自己产生的响应/诊断不滞留在失败缓冲中。不关闭或替换调用者流；不承诺修复嵌入调用者原先已有的损坏缓冲。

NAS 探针回归使用真实两个子进程和固定目录 fd，但文件系统分类被模拟；路径替换仅发生在专属合成目录中。正向恢复和资源测试仍为 LOGIC_ONLY，不构成真实 NAS 认证。

实际 Cloud overlay（fsync=volatile）被生产 API/CLI 拒绝。受支持本地文件系统正向、GX10、真实 NAS 及实际挂载丢失/断网均 NOT_RUN；整体 A2 PARTIAL。A1 的 9 个大容量/响应专项本轮未重跑；A2 的 11 个资源专项已实际执行通过。不同解释器、源码/安装态和定向实验存在重复，不合计为独立用例数。
