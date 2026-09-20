# A1 执行验证记录

范围：`A1-local-ledger-v0.1` 的 P1–P5；软件 `0.1.0a1`；日期 2026-09-20。只使用合成数据。

已验证实现 source D：`cbfa42bb8ded86ce81e003853847bab1d190a65f`；tree：`bfccf7cf071f53c6316afc960f8fe54319f1cfdb`。本记录的后续证据提交只更新文档，不改变已验源码/测试/构建配置。

最终本地 wheel：`infra_artifact_ledger-0.1.0a1-py3-none-any.whl`，33,756 字节；SHA-256：`7c9275665e242f1820cae6a88d5d6a244e60660fd5048bfff61f0b651f977129`。该摘要标识本次构建产物；未声明不同时刻或工具环境构建的 ZIP 必然字节相同。wheel 内所有 Python 源文件与 D 逐字节相等，含原 MIT LICENSE，无 Requires-Dist 运行依赖。

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
| 独立安装 | 固定 D 最终 wheel 构建、全新 consumer venv 离线安装均退出 0；包实际来自该 venv 的 site-packages |
| 文档闭环 | 固定 D 最终 wheel 运行 25 次 CLI（正常退出 0、冲突反例退出 4）和实际 USAGE Python 代码块，全部通过，`installed_walkthrough.py` 退出 0 |
| 独立审读 | 公共 portable 规则和事务/重放分别审读；事务 reviewer 实跑 20 项 service 与 4 项提交状态测试，无未解决阻塞问题 |

故障与语义测试分别覆盖：真正多进程同请求/冲突/跨 key 身份竞争；真实 SQLite BEGIN 和 COMMIT 锁等待；写入中、COMMIT 前、COMMIT 后的实际进程终止；SQL/COMMIT/ROLLBACK/响应阶段注入异常；已提交状态的结果身份保留；关闭与输出发布失败；CLI 真子进程 stdout 断管后按原 key 恢复并重放不重复；同一 handle 先成功后失败；七类 ID 碰撞；分支、多父、完整历史冲突；导入失败无部分结果；目标新增版本后原导入仍重放原 Receipt；严格存储 JSON 与索引损坏；元数据查询和内容校验的不同结论。

资源输入覆盖 Blob 64 MiB±1、decoded payload 总量 256 MiB±1、复用 Blob 的 256 MiB 内容闭包、package 384 MiB±1、descriptor 4096±1 字节、request 8 MiB±1、公共 get_history 完整响应含换行 8 MiB±1、深度 15/16/17、节点 499999/500000/500001。最大进程 RSS 为 1,552,416 KiB（Linux `ru_maxrss`，约 1.48 GiB）；此值是观测结果，不把 package 上限等同于内存上限。超限导入同时核对数据库无部分写入。

文档场景从源库写入报告 v1/v2，逐字节读回 20+20=40 字节；源库有 1 Artifact、2 Version、2 ContentRoot、2 Blob、1 ProvenanceLink、3 成功操作。新库导入后原记录/来源/内容/历史操作保持一致，增加 1 ImportReceipt 和 1 导入成功操作。原请求重放无新增；同 key 改输入退出 4。Python 入口在另一个新库运行，结果与 CLI 场景相同。

## 4. 证明范围

所有故障都记录具体边界，不把测试数量替代正确性说明。实际 SIGKILL 不等于真实设备掉电；没有 NAS snapshot/restore、两个真实消费者或独立第二 backend 的证明。没有新增托管 CI，也没有发布 PyPI 或 tag。Alpha 仅供已声明环境下的受限试用。

## 5. Codex Cloud 独立运行

[首轮 Cloud 任务](https://chatgpt.com/codex/cloud/tasks/task_e_6ab0002f0a94832999d515981db576b7)实际检出 D 与上述 tree，确认 C 是 D 的直接父提交。[完整报告](https://github.com/kongbu0621/infra-artifact-ledger/pull/3#issuecomment-5750890543)记录：

| 检查 | Cloud 实测 |
|---|---|
| 环境 | Python 3.11.15、SQLite 3.45.1；Linux 6.18.44 x86_64 / glibc 2.39；17 GiB RAM、无 swap |
| 普通 suite | `Ran 113 tests in 27.759s / OK (skipped=8)`：105 项通过，exit 0 |
| 显式资源 suite | 8 项全部通过，268.986s，exit 0；没有更改测试上限 |
| 资源峰值 | 1,549,448 KiB RSS，约 1.48 GiB；Cloud 的实际观测，不与本地数值混用 |
| 编译 | Python 3.11.15 `compileall -q src tests`，exit 0 |
| 工作树 | HEAD/tree 不变，staged/unstaged diff 为空 |
| 首轮构建工具 | BLOCKED：任务阶段包索引请求收到 `Tunnel connection failed: 403 Forbidden`；没有将预装旧工具替代固定版本 |
| 私有 companion | BLOCKED：Cloud `gh` 未认证，未能复读私有 R；未复制私有正文，也未在 Cloud 改 Gate 或实施新代码 |

首轮 Cloud 安装未执行，保留上述历史结果。随后使用 Cloud 界面正常提供的联网初始化阶段准备固定构建工具，任务阶段网络设置保持关闭。[第二轮 P5 任务](https://chatgpt.com/codex/cloud/tasks/task_e_6ab00269e720832990360037e9068fae)按[补验请求](https://github.com/kongbu0621/infra-artifact-ledger/pull/3#issuecomment-5750896002)，仅补跑安装环节，实测通过：

- 源 D/tree 精确相同；从 D 的 Git archive 导出临时源码并计算 tree，再在临时副本构建，原 checkout 不产生构建改动。
- Python 3.11.15 / SQLite 3.45.1；pip 24.0、setuptools 84.0.0、wheel 0.48.0、packaging 26.3，版本核对通过。
- `pip wheel --no-deps --no-build-isolation` 退出 0；wheel 33,756 字节，SHA-256：`25b91d6765395afbd4d593f94dc8c7dffb79004338347afc99087695f3121859`。本地与 Cloud 构建各有摘要，不宣称 ZIP 字节可复现。
- 另外新建 consumer venv，`pip install --no-index --no-deps` 退出 0，安装版本 `0.1.0a1`。
- consumer Python 使用 `-I` 执行 `installed_walkthrough.py`，退出 0、`result=PASS`、`library_guide=PASS`；导入路径属于 consumer site-packages。
- 25 次 CLI 中 24 次正常退出 0，故意制造的幂等冲突退出 4；报告实际 40 字节、两个版本、来源和历史查询一致，目标仅增加本次 Receipt/操作。USAGE 的 Python 代码块也实际执行。

[P5 完整补验报告](https://github.com/kongbu0621/infra-artifact-ledger/pull/3#issuecomment-5750914668)确认最终 HEAD/tree 不变、工作树和暂存区均无修改。因此本地和 Cloud 均已执行固定 D 的完整 wheel/独立消费闭环；没有修改产品源码来解除环境阻塞，没有重复大资源 suite。

维护工作台此前经授权读取了固定私有 R，并在 B/C 之后实施；Cloud 此处承担公开软件的只读消费和验证，不承担私有规则复核或实现授权判定。后续若由 Cloud 执行新实现，仍须满足自身规则可读性要求，不能把本轮公开运行通过当作豁免。
