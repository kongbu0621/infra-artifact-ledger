# A1 执行验证记录

范围：`A1-local-ledger-v0.1` 的 P1–P5；软件 `0.1.0a1`；日期 2026-09-20。只使用合成数据。

当前复审修复 source H：`5e35af7bfbe55b876cda05e8d5b5c7fd4e08f4da`；tree：`e5138d72f377d224a1df89aaaf732e12629d1b9c`。H 的验证见 §7；§2–5 是初次实现 D 的历史结果，§6 是 F `d7b4ccfec5d07a9a88d087afd379a3e682ec5b6b`（tree `2cac6ae79075c5e6167a12a6105e21143183a41f`）的历史结果，不能直接当作 H 的本轮实测。

初次已验实现 D：`cbfa42bb8ded86ce81e003853847bab1d190a65f`；tree：`bfccf7cf071f53c6316afc960f8fe54319f1cfdb`。E `fd31f87f97e6f1b5505f5266f56ba28e96547223` 仅补录 D 的文档证据；F 在 E 后追加范围内缺陷修复，保留 C/D/E 历史。

D 对应的本地 wheel：`infra_artifact_ledger-0.1.0a1-py3-none-any.whl`，33,756 字节；SHA-256：`7c9275665e242f1820cae6a88d5d6a244e60660fd5048bfff61f0b651f977129`。该摘要标识本次构建产物；未声明不同时刻或工具环境构建的 ZIP 必然字节相同。wheel 内所有 Python 源文件与 D 逐字节相等，含原 MIT LICENSE，无 Requires-Dist 运行依赖。

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

## 6. 两个 PR 的再次独立复审与 F 补验

### PR #2：授权与 Gate

固定 C 的授权链复审通过：Owner 原话、明确标注执行者转录的 GitHub 记录及决定副本一致；C 仅 8 个 Markdown 文件，无源码、测试、运行配置；A 中 ARCHITECTURE、INTERFACE_PROFILE、REUSE_EXAMPLE 未变；D 的直接父提交是 C。没有需要重新请求 Owner 授权的范围变化。

发现并更正一条验收记录：原 C 的 `git diff --check main..C` 实际返回 2，原因是决定文件末尾多一个空行；PR #2 先前写“通过”不准确，现已更正。保留原 C 的不可变证据，后续文档整理去掉末尾空行；不重写 C，也不把此格式提示当作授权失效。合并顺序仍是先保留独立 C 合并 #2，再把 #3 转向 main。

### PR #3：实际发现与修复

复审先在 E 对应的旧源码上复现，再修复；没有为符合实现而改写批准的接口合同。

| 问题 | 修复前观察 | 修复和反例验收 |
|---|---|---|
| 初始化目标竞争 | O_EXCL 后、SQLite 打开前目标被替换；初始化会向另一数据库写入 ledger 表及 PRAGMA | 私有暂存完整建库、关闭、fsync 后硬链接拒覆盖发布；竞争文件字节、表和 journal 模式不变 |
| 遗留 SQLite 侧车 | 目标主文件缺失但存在真实热回滚日志，初始化会消费并删除该日志 | 构建前与发布前拒绝已有 -journal/-wal/-shm；真实热日志逐字节保留，目标不存在；发布后合法 writer 的日志不删除 |
| 异常退出丢失提交证据 | execute 已提交后响应失败，加上 close 失败，会把 committed/原身份覆盖成 not_applicable | context 保留原异常及精确身份，清理异常附 note；初始化/打开双重失败同样保留原问题 |
| 本地格式检查不完整 | 添加忽略 operation 插入的 trigger 后仍可 open，create 返回成功但缺失成功幂等记录 | 精确 schema/自动索引/显式索引/约束核对；trigger、view、缺 PK/FK/NOT NULL/index 均拒绝，不改库 |
| CLI 解析期状态错误 | 已识别读取命令缺参/重复/未知参数，返回 not_committed | 根据真实解析子命令归类；读取/辅助为 not_applicable，write 为 not_committed；帮助仍为文本 |
| 累计历史节点超限 | 两次各自合法的 Version 写入后，低于 8 MiB 的完整历史可超过 500,000 节点，旧实现仍成功返回 | 完整响应统一检查字节/深度/节点；公开库和真实 CLI 对 500,001 节点明确拒绝，不截断、不改记录 |

新增 storage 专项 18 项、CLI 测试方法 3 项、显式响应节点资源专项 1 项。节点专项用三个原始阈值 499,999 / 500,000 / 500,001，不修改数据库或上限。前后同一 500,001 节点子场景在 E 上失败（`LedgerError not raised`，5.958s），在修复上通过（9.000s）；预期完整响应 4,278,342 字节，隔离验证节点上限而非字节上限。

独立合同探针另试验 1,410 个 metadata/transport 类型或形状变异：1,384 个以 LedgerError 拒绝、26 个合同允许输入接受，无裸 Python 异常；这些是审读探针，不冒充新增的 unittest 数量。portable 输出 metadata 已严格解析；对于 B 个 Blob，package 的 `11 + 8B` 节点少于合法 metadata 的至少 `15 + 10B`，不存在同一出口漏检，因此没有对大型包重复解码。

### F 的本地最终验证

| 检查 | 精确结果 |
|---|---|
| 环境 | Python 3.11.16 / SQLite 3.53.1；Linux 6.18.44 x86_64 / glibc 2.39；固定构建工具版本沿用 §2 |
| 编译 | `python -m compileall -q src tests`，exit 0 |
| 普通回归 | `Ran 135 tests in 20.574s / OK (skipped=9)`；126 项通过，9 项资源默认跳过，exit 0 |
| 读取节点资源专项 | `tests/test_response_boundaries.py -v`：1 test / 3 阈值，29.596s；499999/500000 成功，500001 为 RESOURCE_LIMIT/not_applicable，CLI exit 2；进程峰值 231,924 KiB |
| 读取字节资源专项 | `tests/test_resources.py ActualProfileResourceTests.test_public_history_response_limit_includes_complete_envelope_and_lf -v`：1 test / 8 MiB±1 三阈值，13.226s，exit 0；峰值 99,548 KiB |
| 构建及安装 | 精确暂存文件树导出到新临时副本，固定工具构建 wheel，另建 consumer venv 离线安装，均 exit 0 |
| 独立消费 | consumer Python `-I tests/installed_walkthrough.py --repo 固定副本`，25 次 CLI（24 exit 0、1 预期冲突 exit 4），实际 USAGE Python 示例 PASS，验证 40 字节；仅使用 consumer site-packages |
| wheel 一致性 | 35,236 字节；SHA-256 `6a3da007e9c12077a84b9c5165ad519162b3c8dae540152c6e1f23356bdc5201`；10 个 Python 源文件逐字节等于 F，MIT 正文一致，无 Requires-Dist |

资源专项在相同响应修复代码上执行；随后只补初始化遗留侧车保护，最终 F 的全部普通测试及 wheel 安装在该补修后重新执行。没有把修复前的完整资源 suite 当作 F 的完整资源重跑；此次针对受影响的节点和响应字节两项补验，未重跑无改动的 64/256/384 MiB 内容算法上限。

### F 的 Codex Cloud 验证

[本轮请求](https://github.com/kongbu0621/infra-artifact-ledger/pull/3#issuecomment-5751030228)固定 F/tree，[实际 Cloud 任务](https://chatgpt.com/codex/cloud/tasks/task_e_6ab007b104dc8329a8f75dc55e4083fe)已经完成。[完整 Cloud 报告](https://github.com/kongbu0621/infra-artifact-ledger/pull/3#issuecomment-5751066782)与本地分开记录：

| 检查 | 固定 F 的 Cloud 实测 |
|---|---|
| 环境与来源 | Python 3.11.15 / SQLite 3.45.1；Linux 6.18.44 x86_64 / glibc 2.39；构建工具四个版本与 §2 相同；临时 Git archive 重算 tree 等于 F |
| 编译 | compileall，exit 0 |
| 普通 suite | `Ran 135 tests in 33.933s / OK (skipped=9)`；126 通过，exit 0 |
| 响应节点专项 | 499999/500000/500001 三阈值的公共库及 CLI，68.305s，exit 0；峰值 229,632 KiB |
| 响应字节专项 | 显式文件入口执行真实 8 MiB±1，22.336s，exit 0；峰值 87,212 KiB |
| wheel 与安装 | 固定工具构建、新 consumer 离线安装均 exit 0；wheel 35,236 字节，SHA-256 `73f6ad48388efc71d9f0cab2defda18957b684ff1fa0c0e7fb5904731e5d41d0` |
| 独立消费 | 25 次 CLI 及实际 USAGE Python 代码块 PASS；40 字节；模块来自 consumer site-packages |
| wheel 与源码对应 | 10 个 Python 源文件逐字节等于 F，MIT 正文一致，Requires-Dist 为零 |
| 只读性 | 原 checkout HEAD/tree 不变、staged/unstaged diff 为空；未改代码、commit、push 或 merge |

Cloud 首次调用缺失的 `/usr/bin/time` 返回 127；另一次以 unittest 模块形式调用资源项被正确跳过，均未计为有效通过。随后用资源脚本自身的计时/RSS 和规定的显式文件入口完成实测；没有改测试或资源上限。两个环境的 wheel 各有实际摘要，不宣称 ZIP 字节可重复构建。

Cloud 的私有 companion 复读仍因未认证单列 BLOCKED；它仅验证公开软件。维护工作台已读取固定 R 并在 Owner B/C 后实施，未把 Cloud 公开运行结果作为新授权或规则豁免。

F 当时的格式检查记录：修复与证据文档增量 `git diff --check` 通过；相对 main 的累计 diff 仍有初次 D 引入的 5 处文件末尾空行提示（`.gitignore`、`LICENSE`、`pyproject.toml`、`__init__.py`、`__main__.py`）。这是非语义格式提示；随后在 H 中追加清理，原 F 未重写，见 §7。

F 保持 A1/alpha 边界：未发布包或 tag，未新增 CI/网络服务，未改变 R/A/公共合同；未验证真实掉电、NAS 恢复、独立第二 backend 或真实消费者采用。初始化依赖的硬链接和目录同步要求，以及发布后失败时需 open 核对的行为，已写入 USAGE/COMPATIBILITY。

## 7. 本次分别复审与 H 补验

### PR #2：C2 只修格式，授权成立

维护工作台再次完整读取固定 R，逐项核对 A、Owner B、原 C 和 D 的父子关系。追加 C2 `8542b90983607d768b905ad627a411060fcec854`（tree `430e721b9cd1553a3a63243791c2d478554be31a`），唯一父提交为 C；唯一文件变化是决定副本末尾删除一个 LF，1417 → 1416 字节。R/A/B、授权正文与范围没有变化，无需重开 Gate。相对 main 的累计 `git diff --check` 通过，原 C 保留为不可变证据。

C2 与实现分支的共同祖先仍是 C；同样的尾部清理已在 G 中完成，两边内容一致。D 的直接父提交仍为 C。合并时先保留 C/C2 合并 #2，再将 #3 转向 main，不将授权记录与实现 squash。本次没有合并。

### PR #3：四项新反例与修复

H 的唯一父提交是 G `07106a83d0a440484ef00b3fc795d67cfc3b36f5`。以下是本轮新问题；§6 的六项问题属于先前 F。没有调整公共合同、资源上限或授权范围来迎合实现。

| 问题 | 修复前反例 | H 的行为与回归 |
|---|---|---|
| SQL 行与自身 JSON 未逐行绑定 | 交换两条合法 records 或 operations 的 JSON 正文，整体身份集合仍相等，verify/export 错误通过 | 每行核对自身身份、类型或幂等三元组及 result_ref；verify/export/new write 拒绝且不修写原数据；已知成功 replay 保留 committed/原结果。新增 4 个测试方法，旧函数同组 2 FAIL，修复后全通过 |
| 打开阶段误报损坏 | 有效库被真实 BEGIN EXCLUSIVE 锁住，open 返回 INTEGRITY_FAILURE；格式读取 I/O 故障也被归为损坏 | 仅 CORRUPT/NOTADB 归完整性错误；BUSY/LOCKED 和扩展 IOERR 保留分类。新增 3 个测试方法，包含真实锁和非 SQLite 文件 |
| CLI write 打开失败的状态错误 | 缺失、非 SQLite 或被锁数据库返回 not_applicable，未表达写入尚未开始 | execute 未开始时返回 not_committed；读取仍为 not_applicable；不会改写 execute 已返回的 committed/unknown 证据。新增 2 个测试方法，真实 CLI 锁等待也实跑 |
| 异常 Mapping 的重复项被覆盖 | 自定义 Mapping.items() 重复枚举同一 BlobRef，dict 转换静默保留最后值；普通 dict 不存在重复键 | 逐项检查身份与重复键，捕获同一份不可变 bytes；重复输入为 INVALID_INPUT，原有成功记录仍可查询和正常重放。新增 4 个测试方法，包含源 Mapping 随后变化与非法不可哈希键；旧实现同组有 6 个失败子场景，修复后通过 |

另有只读诊断：多跳 import/export 的 53 个断言验证不透明身份、时间和缺省字段保留、摘要分层、七类 ID 冲突原子拒绝及目标增长后的幂等重放；六个真实进程同时初始化为一成功五冲突；异常退出后的真实热日志可恢复原已提交状态；get_history 与另一进程真实提交竞争时保持一致快照。返回对象修改不改变持久记录。这些诊断不是新增 unittest 数量，也不是 A2/A3/A4 的验收。

H 还清理 §6 记录的五处尾部空行，每个文件仅删除一个 LF；MIT 正文及构建配置语义未变。增量和相对 main 的累计 diff 检查均通过。

### H 的本地验证

| 检查 | 固定 H 的结果 |
|---|---|
| 来源与环境 | H/tree 如文首；Python 3.11.16 / SQLite 3.53.1，Linux 6.18.44 x86_64 / glibc 2.39；固定构建工具沿用 §2 |
| 编译 | compileall -q src tests，exit 0 |
| 普通回归 | `Ran 148 tests in 36.246s / OK (skipped=9)`：139 项通过，9 项资源默认跳过，exit 0 |
| 响应节点专项 | 显式脚本入口，499999/500000/500001 三阈值，1 test，31.222s，exit 0；进程峰值 238,248 KiB |
| 响应字节专项 | 含完整 envelope/LF 的真实 8 MiB±1，1 test，14.019s，exit 0；峰值 106,996 KiB |
| 独立构建安装 | 冻结副本的全部 40 个跟踪文件逐字节等于 H，固定工具构建，新 consumer venv 离线安装；均 exit 0 |
| 文档复用 | 25 次 CLI：24 exit 0、1 预期冲突 exit 4；实际提取执行 USAGE Python 示例 PASS；40 字节经搬运后逐字节一致，模块来自 consumer site-packages |
| wheel | 35,633 字节，SHA-256 `cfffbd8d863e83ec10ce038559c824cee5b138247ff54a91f448710be23172ad`；全部 10 个 Python 源与清理后的 MIT 许可证等于 H，也等于实际安装内容；Requires-Dist 为空 |

逐行核验影响读取验证路径，因此在 H 上重跑两个真实响应资源专项；未重复未改动的 64/256/384 MiB 内容算法专项，D 的完整资源 suite 仍仅属历史证据。时间/RSS 为本次观测，不能当作稳定性能保证。

[GitHub 独立代码审查](https://github.com/kongbu0621/infra-artifact-ledger/pull/3#issuecomment-5751187211)在 H 上完成，未报告重大问题；这是一项代码审查结论，不替代编译、资源或安装实测。

### H 的 Codex Cloud 补验

[实际 Cloud 任务](https://chatgpt.com/codex/cloud/tasks/task_e_6ab00da1230083299204b9322e1ede33)在固定 H/tree 上完成，[维护工作台转录报告](https://github.com/kongbu0621/infra-artifact-ledger/pull/3#issuecomment-5751256648)保留来源与运行过程；下表只记录有实际输出和退出码的步骤。

| 检查 | 固定 H 的 Cloud 实测 |
|---|---|
| 环境与来源 | Python 3.11.15 / SQLite 3.45.1；Linux 6.18.44 x86_64 / glibc 2.39；固定四个构建工具版本与 §2 相同；checkout 的 HEAD/tree/parent 与 H 一致，archive 中 40 个跟踪文件逐字节匹配 H |
| 编译与普通 suite | compileall exit 0；148 项中 139 通过、9 资源按设计跳过，55.613s；过程中重复的一次为 52.968s，亦 exit 0；补验阶段未重跑普通 suite |
| 响应节点专项 | 前台显式脚本执行，499999/500000/500001 三阈值，1 test，78.737s，exit 0；峰值 230,372 KiB |
| 响应字节专项 | 真实 8 MiB±1 含 envelope/LF，1 test，22.844s，exit 0；峰值 101,752 KiB |
| 构建与消费 | 固定工具构建 wheel、新 consumer venv 离线安装、25 次 CLI 与实际 USAGE Python 示例通过，合并执行 exit 0；24 次 CLI exit 0、1 次预期冲突 exit 4；40 字节往返一致 |
| wheel | 35,633 字节，SHA-256 `c997a0b6322dc2aec59f8182f6a6190f781b4ea2c65eff310b507ef00dd58aa9`；10 个 Python 源与 MIT 许可证逐字节等于 H，Requires-Dist 为零；与本地构建各保留实际摘要，不宣称 ZIP 字节可复现 |
| 原工作树 | 结束时 HEAD/tree 仍等于 H，原 staged/unstaged diff 为空；未实施、commit、push 或 merge |

运行过程单列：初次版本探针直接同时导入 pip/setuptools 触发 distutils 断言，改用 importlib.metadata 读取安装版本后完成核对；初次 archive 内容比较使用了临时父目录，修正到 source-h 后全部 40 文件匹配；后台尝试曾没有有效资源日志且进程已退出，不能计为通过。维护工作台停止重复普通 suite，并明确仅补资源和安装，随后以前台执行取得上述有效结果。未因这些执行方式问题修改产品代码、测试上限或环境配置。

Cloud 私有 companion 读取仍单列 BLOCKED，仅承担已提交公开软件的只读验证。维护工作台已重读固定 R；本轮没有改变规则、Owner 决策或公共合同，也没有将 Cloud 结果作为后续实现的权限豁免。真实掉电/NAS、真实消费者采用和独立第二 backend 仍未证明。
