# A2 固定源码 Cloud 执行证据

结果和限制见 [A2_VALIDATION](../../A2_VALIDATION.md)。本目录是实际执行日志的公开副本，不是 GX10/NAS 实机证据。

- 固定实现源码：`ca17ca8b8a1a7b4e2186bb1d24d5ee662faa3c87`。
- 逐命令参数、退出码、耗时和 wheel 摘要：[summary.json](summary.json)。源码命令显式设置 PYTHONPATH；安装态命令使用独立 venv、`-I` 并清除 PYTHONPATH/PYTHONHOME。
- [log-index.json](log-index.json) 分别记录原始与公开副本 SHA-256、字节数。公开副本仅将云端工作目录替换为 `<WORKSPACE>`；原始与公开日志摘要不能互换。
- SQLite、复制、链接、同步及测试进程实际执行；测试显式模拟挂载分类的结果仅为 LOGIC_ONLY。实际 Cloud overlay 被生产 API/CLI 拒绝。
- 16 个资源项在普通 discovery 中跳过，随后以 8+1+7 三个独立 suite 全部执行。不得把 skipped 写成已通过。
- 本目录不包含数据库、payload、真实 NAS 配置、凭据或私人聊天；没有真实 NAS 或 GX10 A2 通过声明。

本轮记录可用于复核这一个源码/wheel，不证明后来提交、所有文件系统、硬件断电或所有失败时序。
