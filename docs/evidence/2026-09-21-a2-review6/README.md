# PR6 第六轮复核与修复证据

固定实现 D7 **`4af226210586f61bfb4e4c604b23edd6194fef59`**，tree `1123371884fbd6507bcb67e495e1bd9fa31db8c9`，父提交为 D6 证据提交 `073057c2d1a72e12c669f1c4ee5fe3d0930e701a`。本目录随后以独立文档提交归档；运行代码、测试、工具与构建配置保持固定 D7。

本轮确认并修复本地 hardlink 配额耗尽 `EDQUOT` 的错误分类遗漏：create/restore 返回 `IO_ERROR/publish/not_published`，CLI 退出码 9。只增加本地 errno 集合一项，未改变远端规则。依据 [Linux link](https://man7.org/linux/man-pages/man2/link.2.html)与 [POSIX link](https://man7.org/linux/man-pages/man3/link.3p.html)；NAS 仍存在服务端创建后客户端报错的歧义。

## 回归证据

扩展 3 项已有测试、新增 1 项 CLI 回归，选择这 4 项做相同最终测试文件的修复前后对照：修复前 4 个 failure 记录，涉及 3 个测试方法（CLI 含 create/restore 两个 subtest）；修复后 4 项全过。API 检查错误代码、阶段、状态、公开对象缺失和源字节不变。CLI 调用实际 API，核对单行响应与退出码。远端反例先实际创建链接再抛 EDQUOT，仍保留 unknown 和完整目标，之后只读 verify 成功。

[regression-summary.json](regression-summary.json) 记录命令、工作目录、PYTHONPATH、源码/测试摘要与 RED/GREEN 日志映射。注入未配置真实配额，存储分类模拟均为 LOGIC_ONLY。没有真实 NAS 或真实配额耗尽证据。

## 固定源码验证

| 验证 | 发现 | 通过 | 跳过 |
|---|---:|---:|---:|
| Python 3.11.16 源码 | 430 | 410 | 20 |
| Python 3.12.14 源码 | 430 | 410 | 20 |
| 独立安装态 A2 | 264 | 253 | 11 |

12 条命令全部 exit 0，详见 [summary.json](summary.json)。两版 compileall、A1 安装态闭环、构建/安装及实际 overlay 的生产 API/CLI 拒绝均通过。20 项资源专项本轮未重跑：A1 9 项保留 D2 历史，A2 11 项保留 [D6 历史](../2026-09-21-a2-review5/summary.json)；安装态跳过的 11 项是同一批 A2 专项。不同版本与安装态有重叠，不相加为独立用例数。

构建前按远端 tree 核对 195 个文件的 Git blob，构建后复核 SHA-256；17 个运行模块在源码、wheel 和新独立 Python 3.11 runtime venv 中逐字节一致，见 [source-integrity.json](source-integrity.json)。复用已有 build venv，补充 Python 3.12 复用此前 venv。安装态从源码目录外以 `-I` 执行；库来自 site-packages，测试/工具来自固定源文件集。本地源文件集经过哈希核验，并非本地 Git checkout。

- wheel：`infra_artifact_ledger-0.2.0a1-py3-none-any.whl`
- 大小：67,832 bytes
- SHA-256：`a54bd1b75238a3a75184001c33e6b46dd021c50e0f0a00b8e0118b455e73a181`

[log-index.json](log-index.json) 绑定 14 份原始/公开日志各自的摘要与字节数；receipt 指向原始摘要，不冒充脱敏后摘要。`<WORKSPACE>`、`<PRIMARY_PYTHON>`、`<PIP_CACHE>` 为脱敏标记。

五入口状态、journal 前置检查、CLI 失败输出和来源链复核未另确认新缺陷；不宣称穷尽验证。受支持本地文件系统正向、真实配额耗尽、GX10、真实 NAS、挂载丢失/断网仍 NOT_RUN，A2 整体 PARTIAL，PR 保持 Draft。五份批准规范、Gate、Owner 决定及旧证据保持原字节。
