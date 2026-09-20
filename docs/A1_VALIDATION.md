# A1 执行验证记录

范围：`A1-local-ledger-v0.1` 的 P1–P5；软件 `0.1.0a1`；日期 2026-09-20。只使用合成数据。

本记录将由后续证据提交填写已验证实现的完整 source SHA、wheel 摘要和 Codex Cloud 结果；不能把这份说明自身当作未执行项目的通过证据。

## 1. 授权链

- 规则 R：`91ac1ad72a423079785725fafd4eb139f5cd7943`。
- 文档 A：`a74c8f1650699b987f4f299ff95eb5006ab97130`。
- Owner B：[会话授权的明确转录记录](https://github.com/kongbu0621/infra-artifact-ledger/pull/1#issuecomment-5750709424)。GitHub 事件时间是执行者转录时间，不冒充 Owner 在 GitHub 直接发言。
- 独立 CLOSED C：`6846b7d65d34d9cc21d46473e340109a481c595e`，仅关闭记录及文档状态，不含运行实现，见 [PR #2](https://github.com/kongbu0621/infra-artifact-ledger/pull/2)。
- 实现以 C 为祖先；本轮不自动合并、发布软件或扩大到 A2–A4。

## 2. 环境与复核命令

本地工作环境 Linux-6.18.44-x86_64-with-glibc2.39，Python 3.11.16，SQLite 3.53.1。构建环境 pip 24.0、setuptools 84.0.0、wheel 0.48.0、packaging 26.3。版本和平台边界见 [COMPATIBILITY](COMPATIBILITY.md)。在固定实现 checkout 根目录运行：

```sh
python3.11 -m venv /tmp/ledger-build
/tmp/ledger-build/bin/python -m pip install pip==24.0 setuptools==84.0.0 wheel==0.48.0 packaging==26.3
/tmp/ledger-build/bin/python -m compileall -q src tests
PYTHONPATH=src /tmp/ledger-build/bin/python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src /tmp/ledger-build/bin/python tests/test_resources.py -v
/tmp/ledger-build/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/ledger-wheels .
python3.11 -m venv /tmp/ledger-consumer
/tmp/ledger-consumer/bin/python -m pip install --no-index --no-deps /tmp/ledger-wheels/infra_artifact_ledger-0.1.0a1-py3-none-any.whl
/tmp/ledger-consumer/bin/python -I tests/installed_walkthrough.py --repo "$PWD"
```

以上临时目录必须使用新的路径。构建工具准备可以联网，产品运行与 consumer 安装不需要联网。实际命令使用等价的独立绝对路径，测试脚本不注入源码路径到 consumer；脚本会确认导入位置位于 consumer 的 site-packages 且不在源码树中。

## 3. 已执行验证

| 层次 | 本地实测 |
|---|---|
| Python 3.11 编译 | `compileall -q src tests`，退出 0 |
| 普通测试 | 105 项通过，8 项资源测试按设计跳过；unittest 输出 `Ran 113 tests in 18.686s / OK (skipped=8)` |
| 真实资源 | 8 项单独执行通过：第一批 6 项 61.712s，新增 decoded payload 总量边界 1 项 22.495s，完整读取响应边界 1 项 13.260s；没有调低或 monkeypatch 上限 |
| Python 3.12 附加验证 | 3.12.14 / SQLite 3.53.1，104 项普通测试通过，18.602s；当次跳过的 7 项资源测试由 3.11 单独执行，新响应资源专项随后补测 |
| 独立安装 | 初版 wheel 离线安装成功；最终源码对应 wheel 将在后续记录固定摘要并复跑 |
| 文档闭环 | 独立安装包运行 25 次 CLI（正常退出 0、冲突反例退出 4）和实际 USAGE Python 代码块，全部通过；最终 wheel 会再次核对 |
| 独立审读 | 公共 portable 规则和事务/重放分别审读；事务 reviewer 实跑 20 项 service 与 4 项提交状态测试，无未解决阻塞问题 |

故障与语义测试分别覆盖：真正多进程同请求/冲突/跨 key 身份竞争；真实 SQLite BEGIN 和 COMMIT 锁等待；写入中、COMMIT 前、COMMIT 后的实际进程终止；SQL/COMMIT/ROLLBACK/响应阶段注入异常；已提交状态的结果身份保留；关闭与输出发布失败；CLI 真子进程 stdout 断管后按原 key 恢复并重放不重复；同一 handle 先成功后失败；七类 ID 碰撞；分支、多父、完整历史冲突；导入失败无部分结果；目标新增版本后原导入仍重放原 Receipt；严格存储 JSON 与索引损坏；元数据查询和内容校验的不同结论。

资源输入覆盖 Blob 64 MiB±1、decoded payload 总量 256 MiB±1、复用 Blob 的 256 MiB 内容闭包、package 384 MiB±1、descriptor 4096±1 字节、request 8 MiB±1、公共 get_history 完整响应含换行 8 MiB±1、深度 15/16/17、节点 499999/500000/500001。最大进程 RSS 为 1,552,416 KiB（Linux `ru_maxrss`，约 1.48 GiB）；此值是观测结果，不把 package 上限等同于内存上限。超限导入同时核对数据库无部分写入。

文档场景从源库写入报告 v1/v2，逐字节读回 20+20=40 字节；源库有 1 Artifact、2 Version、2 ContentRoot、2 Blob、1 ProvenanceLink、3 成功操作。新库导入后原记录/来源/内容/历史操作保持一致，增加 1 ImportReceipt 和 1 导入成功操作。原请求重放无新增；同 key 改输入退出 4。Python 入口在另一个新库运行，结果与 CLI 场景相同。

## 4. 证明范围

所有故障都记录具体边界，不把测试数量替代正确性说明。实际 SIGKILL 不等于真实设备掉电；没有 NAS snapshot/restore、两个真实消费者或独立第二 backend 的证明。没有新增托管 CI，也没有发布 PyPI 或 tag。Alpha 仅供已声明环境下的受限试用。

Codex Cloud 将在确切实现提交上执行只读编译/测试，结果单独补充；此前 A0 文档 Cloud 任务不能替代本轮源码验证。维护私有 companion 的可读性与公共软件独立运行是两个不同检查，不把缺失的私有访问写成通过。
