# PR #6 复审后的固定源码执行证据

结果和适用范围见 [A2_VALIDATION](../../A2_VALIDATION.md)。此目录只对应修订源码 `47b525ebb0add3a6aebc1cb13119f4046eea42b8`，原 D1 的证据保留在 [原目录](../2026-09-20-a2-cloud/README.md)。

- [summary.json](summary.json) 记录固定源码/tree、wheel SHA-256、构建/安装/执行命令、退出码和时间。公开日志只将 Cloud 工作根替换成 `<WORKSPACE>`。
- [log-index.json](log-index.json) 同时保存原始日志与公开副本的摘要及字节数；两者不可混用。
- 两个 Python 版本全量源码回归分别统计；普通 discovery 跳过的 20 个资源测试在 8+1+7+4 四个专门 suite 实际执行。未把 skipped 计作通过。
- 新的四项合法资源测试包含十二个边界场景；构造、完整数据对账与总 TEXT 不可达证明见 [A2_RESOURCE_BOUNDARIES](../../A2_RESOURCE_BOUNDARIES.md)。
- 新 wheel 安装到新的独立 runtime venv。安装态测试使用 `-I`，从固定 checkout 读取测试/验收脚本；被测试的包来自 venv 的 site-packages，不以 PYTHONPATH 指向源码。安装包不包含这些验收脚本。
- 正向快照逻辑测试明确模拟文件系统分类，SQLite、复制、硬链接、fsync、独立进程和失败状态核对实际执行，结果为 **LOGIC_ONLY**。生产 API 和已安装 CLI 对实际 overlay 的拒绝另外执行。
- 真实 GX10、受支持本地文件系统安装态正向流程、真实 NAS 演练仍 **NOT_RUN**；本目录不是设备持久性、断网或断电证明。

目录不包含数据库、payload、真实 NAS 配置或凭据。本轮 wheel 未发布到 PyPI；相同 alpha 版本号不能代替完整源码及 wheel 摘要。
