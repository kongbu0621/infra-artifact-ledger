# PR #6 第二轮复审固定执行证据

当前修订 D3：`478ce4b39bc82df2bb1f6543de208e54ac100d8f`，tree `b0987df359b23a949e854764824b09acc6eb39be`。本目录对应这一提交；此前 [D2](../2026-09-20-a2-review1/README.md) 与 [D1](../2026-09-20-a2-cloud/README.md) 证据保持原样。

- [summary.json](summary.json) 保存 15 条命令、退出码、时间、源码/tree、wheel 摘要。全部 exit 0。
- [log-index.json](log-index.json) 保存原始与公开日志的 SHA-256 及字节数。公开副本只替换 Cloud 工作根为 `<WORKSPACE>`。
- 两版本源码各发现 388 项，368 项普通测试通过、20 项资源跳过。A2 的 7+4 项资源测试另行实际通过；A1 的 8+1 项大容量专项本轮未重跑，保留 D2 历史证据，不算 D3 本轮通过。
- 安装态 A2 222 项发现、211 项通过、11 项资源跳过；使用新 runtime venv、`-I` 和 checkout 外工作目录。测试及 NAS 工具脚本来自固定 checkout，受测库来自安装 wheel。
- 新增 39 项定向回归，覆盖源 WAL/SHM 保持、复合关闭失败、报告阶段的已知 published 状态、路径/挂载文本解析、演练目录组件绑定与嵌套异常。详见 [A2_VALIDATION](../../A2_VALIDATION.md)。
- 正向存储测试模拟文件系统分类，标为 LOGIC_ONLY。SQLite/文件/进程/故障后的状态均实际执行；实际 overlay 的生产 API/CLI 拒绝另外执行。
- GX10、受支持本地文件系统安装态正向、真实 NAS、断网/断电仍 NOT_RUN，A2 整体验收 PARTIAL。

目录不含业务数据、数据库、真实 NAS 配置或凭据；未发布 PyPI。测试计数存在跨解释器与安装态重复，不相加为独立用例数。
