# 独立复用与接入指南

状态：**A1 `0.1.0a1` alpha 的独立接入指南**。可以从固定源码构建 wheel，在独立环境安装并运行库或 CLI；当前没有 PyPI 发布。本文不增加公共字段、方法或实现范围，字段与行为以 [公共接口](INTERFACE_PROFILE.md) 为准。实际源提交、构建和运行结果见 [验证记录](A1_VALIDATION.md)，支持矩阵见 [兼容表](COMPATIBILITY.md)。[文档 Gate](PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md) 已为 A1 的 P1–P5 记录 CLOSED。

## 1. 什么情况下复用

“制品”是需要保留精确内容和版本的数字成果，例如报告、数据集、工作流 JSON 或模型输出。Ledger 负责回答：它是哪一个对象、哪一个版本，内容是否完整，以及记录了哪些来源关系。消费者通过独立安装的库或 CLI 使用它，不必接入 Code Driver、模型账号或私有仓库。

| 消费者 | 交给 Ledger 的内容 | 消费者继续负责 |
|---|---|---|
| 普通报告工具 | 报告原文、v1/v2 版本及父关系 | 生成报告、选择对外展示版本、业务发布 |
| 本地知识库 | 原文或抽取结果的精确版本及来源引用 | Evidence、审核、知识状态、索引和检索 |
| 独立 Agent | 本次任务产生的文本、文件或结果版本 | 模型调用、任务状态、结果判断与后续行动 |
| 工作流或 Harness | 流程定义、节点输出及输入来源引用 | 节点执行、跳转、略过、调度和权限 |

完整的非 Code Driver 场景见 [报告 v1→v2 复用示例](REUSE_EXAMPLE.md)。该示例只使用合成报告数据，不需要 Agent 或大模型。

本组件不判断报告是否正确，不认证外部来源，不执行保存的脚本。调用者需要审核、当前版本指针或业务权限时，在自己的应用中管理，引用准确 Version；不直接修改 Ledger 表，也不往公共记录加入私有字段。

## 2. 选择接入方式与数据归属

| 选择 | 接入方式 | 适用边界 |
|---|---|---|
| Python 项目 | 安装后导入 `infra_artifact_ledger`，使用库操作 | 同一进程调用；公开操作、返回值与 `LedgerError` 见接口 §4/§6 |
| 非 Python 项目 | 用语言自身的进程 API 启动 `artifact-ledger`，交换 JSON 文件与结果 | 例如 C++、JavaScript 应用；仍需安装受支持的 Python 运行环境及 CLI，不代表原生 SDK |
| 两个独立项目交换成果 | 源 Ledger 导出 package 与 descriptor，目标 Ledger 整包导入 | 完整、有界的状态搬运；不是增量同步或业务合并 |

默认接入起点是**每个项目独立安装环境、显式选择自己的 Ledger 数据库**。复用同一组件不要求所有项目共用同一数据库，也不自动形成全局知识库。

同一受信域内的多个本机进程若访问同一 Ledger，由上层明确协调一个逻辑写入者，各进程使用自己的连接。项目间若无共同数据所有者，优先保留独立实例，通过明确的包交换分享数据；不要把多个项目随意指向同一文件当作权限隔离方案。

网络共享服务、多租户访问、HTTP API、原生 C++/JavaScript SDK、云同步均不在 A1 交付内。需要这些能力时另行设计消费者适配或后续实现，不从本地 CLI 推导出已经存在的服务。

## 3. 从安装准备到第一次读回

以下顺序适用于当前 alpha，在 [兼容表](COMPATIBILITY.md) 列出的已验证环境复现；使用合成数据开始。

1. 核对实际交付的 wheel、软件版本、来源提交、许可证和兼容证据；没有这些材料时不猜版本号或下载地址。
2. 在使用方项目的独立 venv 中安装该 wheel，不依赖维护者的 checkout 或其他项目的环境。
3. 选择本地可靠文件系统上的新数据库路径，和软件安装目录分开；确认只有一个逻辑写入者。
4. 先按下表完成一个合成报告的闭环，再连接真实业务数据。

**Linux/Python 3.11 构建与离线安装。** 把下面四个绝对路径替换为自己的目录。`LEDGER_SRC` 指向 [验证记录](A1_VALIDATION.md) 中固定源提交的完整 checkout；先核对来源 SHA。构建与消费者使用两个不同 venv，数据目录另选。`pyproject.toml` 固定 setuptools/wheel；这里同时固定其构建环境使用的 packaging 版本。准备构建工具这一步需要已配置的包索引，或事先准备的同版本 wheel。

```sh
LEDGER_SRC=/path/to/projects/infra-artifact-ledger
LEDGER_BUILD_VENV=/path/to/venvs/infra-artifact-ledger-build
LEDGER_WHEEL_DIR=/path/to/verified-wheels
LEDGER_VENV=/path/to/venvs/report-tool
git -C "$LEDGER_SRC" rev-parse HEAD
python3.11 -m venv "$LEDGER_BUILD_VENV"
"$LEDGER_BUILD_VENV/bin/python" -m pip install \
  "setuptools==84.0.0" "wheel==0.48.0" "packaging==26.3"
"$LEDGER_BUILD_VENV/bin/python" -m pip wheel --no-build-isolation --no-deps \
  --wheel-dir "$LEDGER_WHEEL_DIR" "$LEDGER_SRC"
python3.11 -m venv "$LEDGER_VENV"
"$LEDGER_VENV/bin/python" -m pip install --no-index --no-deps \
  "$LEDGER_WHEEL_DIR/infra_artifact_ledger-0.1.0a1-py3-none-any.whl"
"$LEDGER_VENV/bin/python" -I -c \
  'import importlib.metadata; print(importlib.metadata.version("infra-artifact-ledger"))'
```

构建成功后消费者安装完全使用本地 wheel，不依赖私有 checkout 或模型服务。执行结果应为版本 `0.1.0a1`；软件包与合同的版本含义分别见 §8。不执行 `pip install infra-artifact-ledger` 猜测公开索引来源。Windows 或其他平台的命令须随其实际验证提供。下列 CLI 操作使用上述消费者 venv 中的可执行文件，避免调用到其他版本。

开发者可在固定 checkout 内复核普通测试和显式资源测试，再从消费者 venv 运行文档闭环。资源测试会真实处理最高 384 MiB 包及 256 MiB 内容，并记录内存峰值，所需内存高于文件大小；运行前查看验证记录中的实测值。普通 discovery 会跳过资源检查，须分别显式执行下面两个资源脚本，才能覆盖内容、字节和累计响应节点的实际限额。

```sh
cd "$LEDGER_SRC"
PYTHONPATH=src "$LEDGER_BUILD_VENV/bin/python" -m unittest discover -s tests -v
PYTHONPATH=src "$LEDGER_BUILD_VENV/bin/python" tests/test_resources.py -v
PYTHONPATH=src "$LEDGER_BUILD_VENV/bin/python" tests/test_response_boundaries.py -v
"$LEDGER_VENV/bin/python" -I "$LEDGER_SRC/tests/installed_walkthrough.py" --repo "$LEDGER_SRC"
```

最后一条命令读取报告示例的合成输入，实际调用已安装 CLI，并执行本文的 Python 代码块；程序断言库来自消费者 venv，没有从 checkout 的 `src` 导入。该次 walkthrough 包含 25 次 CLI 调用；它读取文档作为测试输入，不使生产运行依赖源码仓库。每次复现自行保存输出与退出码，准确执行版本以验证记录为准。

| 次序 | 消费者动作 | 使用既有操作及确认点 |
|---|---|---|
| 1 | 初始化新的 Ledger | `initialize` / `init`；已存在目标拒绝。以后用 `open` 或命令的 `--db` 指向既有目标 |
| 2 | 生成并保存报告身份及本次请求 | `create_artifact` 通过 `execute` / `write` 提交；身份、scope、key 由调用者保存 |
| 3 | 登记 v1 及精确内容 | `append_version`；提供版本、内容记录、实际 bytes 及 SHA-256/长度；首次版本可无父 |
| 4 | 保存本次成功结果 | 核对 `COMMITTED`、`commit_state`、`result_ref`、`recorded_at`、`replayed`；保留准确 Version ID |
| 5 | 生成并登记 v2 | 新 Version ID 和新操作 key；父引用显式指向 v1；v1 内容继续保留 |
| 6 | 读回指定版本 | `get_record` 读取 Version，再按其引用读取 ContentRoot；需要时读取 Manifest，定位 BlobRef |
| 7 | 读取并校验真实内容 | `read_blob` / `read-blob`；CLI 选择明确的新输出文件；不能把只读 metadata 当成 bytes 已校验 |
| 8 | 核对历史及重试 | `get_history`、`get_operation`；原请求重放保持原结果，行为见下文 |
| 9 | 结束与重新打开 | 关闭库 handle；新进程再次打开并读回；需要全库检查时调用 `verify` |

每一步的完整输入和预期结果见 [报告示例](REUSE_EXAMPLE.md)；库/CLI 对照见 [接口 §4](INTERFACE_PROFILE.md#4-拟议-python-与-cli-表面)，写请求见 [接口 §5](INTERFACE_PROFILE.md#5-写请求与三类操作)。这些步骤不引入 `latest`、`current`、自动 merge 或新的便捷写 API。

## 4. Python 与跨语言调用约定

Python 消费者从包根导入 `initialize`、`open` 和需要处理的 `LedgerError`；其余操作在返回的 handle 上调用，不读取 SQLite 表布局或导入内部模块。写请求为严格 UTF-8 JSON bytes；payloads 使用 BlobRef 到不可变 bytes 的映射；不适用的传输参数省略。库 handle 在使用后关闭，并由创建它的线程使用。具体参数和返回类型沿用接口 §4，本文不重新定义签名。

**Python 接入示例。** 先按 [报告示例](REUSE_EXAMPLE.md) 准备 `create-report.json`、`append-v1.json`、`append-v2.json` 和两份 `report-v1.txt` / `report-v2.txt`。下面使用另一尚不存在的 `python-source.sqlite`，不与 CLI 示例的库混用。此代码块已由隔离安装 walkthrough 执行，版本和结果见验证记录。

```python
from pathlib import Path
from infra_artifact_ledger import initialize, open as open_ledger

create_request = Path("create-report.json").read_bytes()
inputs = [
    (Path(f"append-{label}.json").read_bytes(),
     {f"blob:quarterly-001-{label}": Path(f"report-{label}.txt").read_bytes()})
    for label in ("v1", "v2")
]
version_refs = []
with initialize("python-source.sqlite") as ledger:
    created = ledger.execute(create_request)
    assert created["status"] == "COMMITTED"
    for request, payloads in inputs:
        result = ledger.execute(request, payloads=payloads)
        assert result["status"] == "COMMITTED"
        version_refs.append(result["result_ref"])

with open_ledger("python-source.sqlite") as ledger:
    for version_ref, (_, payloads) in zip(version_refs, inputs):
        version = ledger.get_record("version", version_ref)
        root = ledger.get_record("content_root", version["content_root_ref"])
        assert root["kind"] == "blob"  # 本例固定为单 Blob 内容
        assert ledger.read_blob(root["blob_ref"]) == payloads[root["blob_ref"]]
    verification = ledger.verify()
```

示例保留原请求 bytes 与原 payload，不根据文件名推导读回路径；真实程序还须按 §5 保存这些输入和业务版本引用。领域失败由既有 `LedgerError` 携带 `code`、`commit_state` 和可选 `details` 抛出；上例不捕获并伪装成功，异常后的核对流程见 §6。两次上下文之间已关闭再打开连接；跨进程重启证据另按 P5 执行。

`with` 只管理连接，不把块内的三次 execute 合成一个事务。如果 v2 登记失败，已经成功的 create 和 v1 仍保留；先核对各次原请求，再决定后续业务动作。不要直接重跑整个初始化块：`python-source.sqlite` 已存在时 initialize 会拒绝，应按当前状态使用 open 和原请求恢复。append 复用全部既有内容而无需传入 bytes 时仍显式传 `payloads={}`；新空 Blob 则须传对应的 `b""`，二者含义不同。

非 Python 消费者先形成请求文件和需要的 payload-map，再通过进程 API 提交 CLI。可执行文件路径与操作选项由应用固定，路径和值作为独立 argv 元素传递；使用 `shell=false` 或该语言等价的直接进程启动方式，不拼接 shell 命令。payload-map 的 `input_path` 只用于读取文件，不成为制品身份。

CLI append 总是提供 `--payload-map`；全部复用既有 bytes 时文件内容为 `[]`。登记新空 Blob 时仍提供对应条目，input_path 指向空文件；具体允许和必需选项以接口 §4 为准。

当前 Linux CLI 从调用者明确指定的**普通文件**读取请求、payload-map、内容和包；不会把 FIFO、设备或无限输入流当作有界文件读取。read-blob/export 在输出目录创建临时文件，写完并核验后使用同目录 hard link 发布新目标，避免覆盖既有文件；需要支持硬链接的本地文件系统。输出不得使用当前数据库同名的 `-journal`、`-wal`、`-shm` 路径：CLI 在打开数据库和发布任一文件前检查全部输出，包含相对路径与符号链接解析，冲突返回 `INVALID_INPUT/not_applicable`。两个 export 文件仍是两个发布动作，必须配套验证，不能把一个文件成功写出解释为整个包已完成。实际平台边界见兼容表。

Python `initialize` 和 CLI `init` 同样要求支持同目录硬链接及目录 fsync 的本地文件系统：先在私有临时目录内建好完整数据库，再拒覆盖地发布新目标。目标或同名 `-journal`、`-wal`、`-shm` 侧车已存在时拒绝初始化，保留原文件。发布后的目录同步、临时清理或重开失败会报告错误，但目标可能已经存在；先用 `open` 核对该目标，不要自动删除后重试初始化。打开既有库会拒绝未知表、触发器、视图及不匹配的约束或索引；不要直接修改 SQLite schema。

CLI 接入应按以下顺序处理结果：

1. 分别捕获 stdout、stderr 和退出状态；同时消费两个输出通道，避免诊断输出阻塞进程。
2. stdout 按接口规定解析为一行 UTF-8 JSON；stderr 是诊断信息，不拼入 JSON。实际 Blob 内容由 `read-blob` 写入显式输出文件。
3. 同时核对退出码、`status` 和 `commit_state`；成功写入为 `COMMITTED`，读取等操作为 `OK`，各字段形状见接口 §6。
4. stdout 缺失、截断、解析失败或进程超时属于结果尚未取得；不能仅凭非零退出码推断写入未提交，转入原请求核对。

`--help` 是面向人的说明入口，输出普通帮助文本并退出 0；它不执行 Ledger 操作，也不属于上述机器 JSON 结果。消费者调用业务命令时不要把帮助输出当作成功 envelope。

幂等查询通过 `operation --request FILE` 传递 JSON，文件只包含接口规定的 scope、kind、key。合法 key 可能包含 JSON 转义的控制字符，不能改成 argv 的 key 参数或自动剔除字符。

## 5. 消费者必须保留什么

| 保留内容 | 用途与责任 |
|---|---|
| 业务对象到 Artifact ID 的映射 | 调用者生成并保存稳定身份；文件名、摘要或数据路径不能替代该 ID |
| 实际采用的 Version ID | 报告页面、知识条目或任务结果保存精确版本引用；哪个版本用于业务由消费者决定 |
| 原 scope、operation_kind、key 与原请求 | 同一次操作提交前保留，核对或重试继续使用；新业务操作另用新 key，不能拿新 key 猜测重建旧操作 |
| append 的原始内容 bytes | 保持与原请求的摘要、长度和 BlobRef 对应；不能把后来覆盖的同名文件当成重试输入 |
| import 的原 package、descriptor 与请求 | 导入核对/重试使用原输入；重新导出得到的新包不等于原包 |
| 成功结果或尚未核实的结果状态 | 保存原 `result_ref`、时间与可定位诊断；查询完成后更新消费者自己的状态 |

这些是调用者完成接入与恢复核对的责任，不新增 Ledger 字段，也不要求 Ledger 与消费者数据库组成跨库事务。消费者自行安排请求与业务引用的保存方式，使进程重启后仍能定位原请求。成功重放的 `replayed` 是响应信息，不是新历史事件。

## 6. 失败后如何继续

错误全集和精确语义见 [接口 §6](INTERFACE_PROFILE.md#6-指纹重试提交与错误)。调用者按结果分支处理，不把每种错误统一当作“再提交一次”。

| 已观察到的结果 | 消费者的下一步 |
|---|---|
| `COMMITTED`，包括 `replayed=true` | 使用原成功结果；重放不会增加 Version 或 Receipt |
| `BUSY` 且 `not_committed` | 本次已确认未提交，可稍后用原请求、原 key 重试 |
| `DURABILITY_UNKNOWN`、无 JSON、响应通道失败 | 保留原 ID/key/输入；用 `get_operation` 核对或重试原请求，不另造身份 |
| `IO_ERROR` / `INTERNAL_ERROR` 且 `committed` | 已确认提交，保留返回的原结果引用；核对原请求后继续，消费内容前仍须读取并校验 bytes，不为补响应追加版本 |
| 身份或幂等冲突 | 查明调用者映射、请求或历史差异；不自动改名、换 key 或覆盖历史 |
| `INTEGRITY_FAILURE` | 停止宣称内容完整；保留错误并调查；重试不能修补已经提交的历史 |
| 版本/profile 不支持或资源超限 | 调整明确支持的输入或兼容方案；不截断内容、伪造版本或静默降级 |

`get_operation` 未找到不证明仍在运行或恢复中的旧调用永远不会提交。判定旧调用未提交前，须确认其已停止、连接已恢复并核对提交状态；整个核对过程保留原请求身份。若原成功内容后来损坏，错误可为 `commit_state=committed`，不能据此否认原成功。

成功查询也有边界：get_record/get_history/get_operation 返回 metadata，查询成功不代表关联 bytes 当前完整。先匹配原幂等身份与请求指纹；业务需要使用内容时继续 read_blob，或 verify 全库。提交后仅 Blob bytes 损坏时，原成功操作仍可能查到，内容校验应失败，不能把“查到成功记录”当成“成果可用”。

## 7. 数据交换、本地与云上边界

独立实例间的交换顺序为：源库 `export_bundle` / `export` → 保存配套 package 与 descriptor → 在目标新库以 `import_bundle` 提交 → 核对 Receipt、精确记录和实际 bytes。传输方式由消费者安排，Ledger 不自动联网。完整格式见 [接口 §7](INTERFACE_PROFILE.md#7-完整字节包与导出)。

目标已有相同 Artifact 时，只接受完全一致的身份、全部版本及来源历史；子集、超集、不同分支均冲突。不要把包导入当作“给另一项目同步最新版本”。原始历史保持不变，新导入会增加本次 Receipt 及成功幂等结果；比较时不能要求导入后所有记录计数与源库完全相等。

A1 首个 backend 依赖本机可靠文件系统和单一受信数据域。云主机可以成为同类本机部署的候选位置，但其具体系统、持久磁盘和故障行为仍需验证；临时云容器中的文件不能直接称为长期保存。A1 不让多机共同写 NAS 挂载的同一个 SQLite 文件。

未来替换成本地其他实现或云商业产品，须核对相同接口行为、支持范围和完整状态搬运，不能只修改连接地址就宣布可替换。独立第二实现的替换证据属于 A4；NAS 一致快照与新目标恢复属于 A2，普通 export/import 不替代它们。

## 8. 容量与版本检查

接入前按 [接口 §3](INTERFACE_PROFILE.md#3-资源限额) 估算输入。单 Blob 最多 64 MiB；单次传入 payload 最多 256 MiB；新 Version 的完整内容闭包也最多 256 MiB，并包含复用 Blob。请求、payload-map、metadata 和普通响应各有 8 MiB JSON 限额；完整 package 最多 384 MiB，解码 payload 总量最多 256 MiB，descriptor 最多 4 KiB。

整库历史可以增长，但 A1 只能导出仍能装入一个 package 的完整 Ledger；超限就拒绝，不自动分片或挑选历史。大型模型权重或长期海量知识内容不能仅凭“制品”这个名称被视作首版适用对象；序列化上限也不等于内存峰值。

| 版本层次 | 当前实现与接入要求 |
|---|---|
| 软件包版本 | `0.1.0a1` alpha；从固定源提交构建 wheel，当前未发布 PyPI 包 |
| 公共 metadata 合同 | `contract_version=0.1.0`，状态 `candidate`；固定兼容标记不要求访问私有来源 |
| 行为 profile | `bounded-local-v0.1`；确认操作和上限，不把有相似 API 的产品自动视为兼容 |
| 传输封装 | `transport_version=0.1.0`；package 与 descriptor 均须支持并验证 |
| 本地存储格式 | `storage_schema_version=1`；未知格式拒绝打开，无自动 migration |
| 运行平台 | 最低 Python 3.11；Linux/Python 3.11 为 A1 必验，3.12 是附加项；其他平台未验不宣称支持 |

升级或软件回退前核对 [兼容表](COMPATIBILITY.md)，不直接降级数据库或复制变化中的 SQLite 文件。首个 alpha 没有旧版升级/降级兼容通过声明；未验证的平台仍保持未验证。

## 9. P5 接入验收

以下是接入验收清单。当前库与 CLI 合成报告闭环、实际资源阈值已有执行；最终源提交对应的完整结果和仍未证明事项统一见 [验证记录](A1_VALIDATION.md)，不以本指南存在代替通过。

| 接入验收 | 必须留下的证据 |
|---|---|
| 独立安装 | 实际 wheel、软件/源码版本、LICENSE、独立 venv；无私有 checkout 或模型依赖 |
| 报告完整闭环 | 库和 CLI 各自完成合成报告 create→v1→v2→指定版本读回、bytes 比较及 verify |
| 请求状态与重试 | 原 key 重放、异输入冲突、响应丢失后核对；结果和历史计数符合合同 |
| 库调用与查询边界 | 包根公开导出可调用；execute 参数组合正确；同一 with 后续失败保留此前提交；metadata 查询成功不能掩盖 bytes 损坏 |
| 进程与数据边界 | 原进程结束后新进程读回；新目标不覆盖；输出 JSON、stderr、退出码符合接口 |
| 独立实例交换 | 源库整包导出、目标新库导入；原 ID/时间/版本/bytes 保持，Receipt 增量可解释 |
| 兼容与容量 | Linux/Python 3.11、Python/SQLite 版本、声明上限与内存证据；未验平台明确列出 |
| 可复核操作记录 | 精确提交、命令、退出码和关键输出，其他使用者可按已交付步骤复现 |

跨语言消费者应按 §4 另验其宿主进程接入；CLI 通过本身不证明每种语言的封装均已通过。P5 的独立安装及库/CLI 验收不等于 A3 两类真实消费者接入，也不等于 A4 跨实现替换。阶段范围见 [实施计划](IMPLEMENTATION_PLAN.md)。
