# 独立复用示例：普通报告的版本归档与搬运

状态：**A0 设计示例，尚未实现或运行**。以下命令和预期结果均用于指导 A1 交付后的验证，不是当前可执行教程或已通过证据。安装入口见 [USAGE](USAGE.md)，字段和错误以 [公共接口提案](INTERFACE_PROFILE.md) 为准；概念示例不能替代 [实施计划](IMPLEMENTATION_PLAN.md) P1–P5 的真实验收。

场景：一个普通报告管理程序要保存季度报告 v1、v2，明确 v2 来自 v1，随后将完整历史搬到另一新 Ledger。它不需要 Code Driver、Agent、模型账号或私有仓库。报告审批、展示名称和“当前采用哪个版本”由报告程序负责；Ledger 只保存精确身份、版本、内容与来源关系。

## 1. 安装与准备

先按 [USAGE 的安装前提](USAGE.md) 选择将来经验证的 wheel 与 Python 3.11 环境。当前没有可安装包，不执行 `pip install infra-artifact-ledger` 猜测包来源。本示例拟在 Linux 上运行；在 A1 安装完成后，从一个全新、本地可靠文件系统目录开始，所有数据库和输出目标必须不存在。

主流程中每条命令都须退出 0、响应及内容符合本节预期后再继续；任何非预期失败都停止并按错误语义核对。§6 的冲突反例单独预期退出 4，不混同主流程成功。以下 shell/Python 片段也是待 A1 的操作说明；Python 仅生成合成输入和独立核对输出，不实现 Ledger。`LEDGER_VENV` 必须改为 USAGE 中已安装 wheel 的同一个 venv 的绝对路径；本例显式使用该环境，不依赖激活：

```sh
LEDGER_VENV=/path/to/venvs/report-tool
mkdir ledger-report-demo
cd ledger-report-demo
"$LEDGER_VENV/bin/python" - <<'PY'
from pathlib import Path
for name, data in [("report-v1.txt", b"Quarterly report v1\n"),
                   ("report-v2.txt", b"Quarterly report v2\n")]:
    with Path(name).open("xb") as stream:
        stream.write(data)
PY
"$LEDGER_VENV/bin/artifact-ledger" init --db source.sqlite
```

这两个文件均为精确 UTF-8/ASCII，末尾只有一个 LF，无 BOM。不要用编辑器将 LF 改为 CRLF。文档编写时已独立重算下表；这是样例字节核对，不是产品验证。

| 文件 | 原始字节长度 | SHA-256 |
|---|---:|---|
| `report-v1.txt` | 20 | `8265a0f41e6eedc0cca8eac41aafdf58cb1c3a13c7e85c70fe2cf22afecd70cc` |
| `report-v2.txt` | 20 | `e9e325ce5cc05f1a9472d39515a371f68294c50bb726a5f07e8910a52754b927` |

下列 JSON 分别按标注文件名保存到该目录，使用 UTF-8、无 BOM。它们没有注释、省略号或占位摘要。所有 owned ID 跨类型唯一；文件名只是 CLI 输入位置。应用须保留请求、幂等三元组和输入字节用于核对或重试，不自行填入新 `recorded_at`。

## 2. 创建报告身份

保存为 `create-report.json`：

```json
{
  "profile": "bounded-local-v0.1",
  "contract_version": "0.1.0",
  "operation_kind": "create_artifact",
  "idempotency_scope_ref": "demo:reports",
  "idempotency_key": "create-quarterly-001",
  "body": {
    "artifact": {
      "artifact_id": "artifact:quarterly-001",
      "namespace_ref": "demo:reports",
      "type_ref": {"namespace": "demo", "type_id": "report", "type_version": "1"}
    }
  }
}
```

```sh
"$LEDGER_VENV/bin/artifact-ledger" write --db source.sqlite --request create-report.json
```

预期退出 0，`status=COMMITTED`、`commit_state=committed`、`operation_kind=create_artifact`、`result_ref=artifact:quarterly-001`、`replayed=false`，以及本次服务端生成的 `recorded_at`。此刻只有报告身份，尚无内容或版本。

## 3. 登记 v1 和实际内容

保存为 `append-v1.json`：

```json
{
  "profile": "bounded-local-v0.1",
  "contract_version": "0.1.0",
  "operation_kind": "append_version",
  "idempotency_scope_ref": "demo:reports",
  "idempotency_key": "append-quarterly-v1",
  "body": {
    "version": {
      "version_id": "version:quarterly-001-v1",
      "artifact_id": "artifact:quarterly-001",
      "content_root_ref": "root:quarterly-001-v1",
      "parent_version_refs": [],
      "external_source_refs": [],
      "capture_refs": [],
      "handling_policy_refs": []
    },
    "content_roots": [{"content_root_ref": "root:quarterly-001-v1", "kind": "blob", "blob_ref": "blob:quarterly-001-v1"}],
    "blobs": [{"blob_ref": "blob:quarterly-001-v1", "digest": {"algorithm": "sha256", "value": "8265a0f41e6eedc0cca8eac41aafdf58cb1c3a13c7e85c70fe2cf22afecd70cc"}, "byte_length": 20, "payload_availability": "available"}],
    "manifests": [],
    "provenance_links": []
  }
}
```

保存为 `payload-v1.json`：

```json
[{"blob_ref": "blob:quarterly-001-v1", "input_path": "report-v1.txt"}]
```

```sh
"$LEDGER_VENV/bin/artifact-ledger" write --db source.sqlite --request append-v1.json --payload-map payload-v1.json
```

预期 `COMMITTED`，`result_ref=version:quarterly-001-v1`，`replayed=false`。CLI 必须读取真实文件并校验长度和摘要；仅保存 Blob metadata 不构成本步骤成功。

## 4. 登记 v2、父关系与来源关系

保存为 `append-v2.json`：

```json
{
  "profile": "bounded-local-v0.1",
  "contract_version": "0.1.0",
  "operation_kind": "append_version",
  "idempotency_scope_ref": "demo:reports",
  "idempotency_key": "append-quarterly-v2",
  "body": {
    "version": {
      "version_id": "version:quarterly-001-v2",
      "artifact_id": "artifact:quarterly-001",
      "content_root_ref": "root:quarterly-001-v2",
      "parent_version_refs": ["version:quarterly-001-v1"],
      "external_source_refs": [],
      "capture_refs": [],
      "handling_policy_refs": []
    },
    "content_roots": [{"content_root_ref": "root:quarterly-001-v2", "kind": "blob", "blob_ref": "blob:quarterly-001-v2"}],
    "blobs": [{"blob_ref": "blob:quarterly-001-v2", "digest": {"algorithm": "sha256", "value": "e9e325ce5cc05f1a9472d39515a371f68294c50bb726a5f07e8910a52754b927"}, "byte_length": 20, "payload_availability": "available"}],
    "manifests": [],
    "provenance_links": [{
      "provenance_id": "provenance:quarterly-v2-from-v1",
      "subject_version_ref": "version:quarterly-001-v2",
      "relation_ref": {"namespace": "demo", "relation_id": "derived_from", "relation_version": "1"},
      "object": {"kind": "artifact_version", "ref": "version:quarterly-001-v1"}
    }]
  }
}
```

保存为 `payload-v2.json`：

```json
[{"blob_ref": "blob:quarterly-001-v2", "input_path": "report-v2.txt"}]
```

```sh
"$LEDGER_VENV/bin/artifact-ledger" write --db source.sqlite --request append-v2.json --payload-map payload-v2.json
```

预期 `COMMITTED`，`result_ref=version:quarterly-001-v2`，`replayed=false`。父关系表达版本演进，ProvenanceLink 表达调用方声明的来源；二者分别保存。这里不证明报告内容真实，也不自动把 v2 设为当前采用版本。

## 5. 精确读取与内容核对

```sh
"$LEDGER_VENV/bin/artifact-ledger" get --db source.sqlite --kind version --id version:quarterly-001-v1
"$LEDGER_VENV/bin/artifact-ledger" get --db source.sqlite --kind content_root --id root:quarterly-001-v1
"$LEDGER_VENV/bin/artifact-ledger" get --db source.sqlite --kind version --id version:quarterly-001-v2
"$LEDGER_VENV/bin/artifact-ledger" get --db source.sqlite --kind content_root --id root:quarterly-001-v2
"$LEDGER_VENV/bin/artifact-ledger" history --db source.sqlite --artifact-id artifact:quarterly-001
"$LEDGER_VENV/bin/artifact-ledger" read-blob --db source.sqlite --blob-ref blob:quarterly-001-v1 --output read-v1.txt
"$LEDGER_VENV/bin/artifact-ledger" read-blob --db source.sqlite --blob-ref blob:quarterly-001-v2 --output read-v2.txt
"$LEDGER_VENV/bin/artifact-ledger" verify --db source.sqlite
```

应用从所选 Version 返回的 `content_root_ref` 找 ContentRoot，再按其中的 `blob_ref` 取 bytes；本例命令显式列出预期引用，实际使用不可按 ID 前缀或命名习惯猜测。`get` 只读取记录，不能替代 `read-blob`/`verify` 的内容校验。重跑读取时给 `--output` 换一个尚不存在的路径；已有输出不能覆盖。后面的 export 两个输出同样必须使用新路径。

```sh
"$LEDGER_VENV/bin/python" - <<'PY'
from hashlib import sha256
from pathlib import Path
for name, expected in [("read-v1.txt", b"Quarterly report v1\n"),
                       ("read-v2.txt", b"Quarterly report v2\n")]:
    actual = Path(name).read_bytes()
    assert actual == expected, name
    print(name, len(actual), sha256(actual).hexdigest())
PY
```

预期实际 bytes 与原始输入逐字节一致，长度和摘要符合上表。不同 CLI 命令是不同进程，后续读取也应在前一进程退出后成功；这不是断电或 NAS 恢复验证。源库预期有 1 Artifact、2 Version、2 ContentRoot、2 Blob、0 Manifest、1 ProvenanceLink、3 成功幂等记录、0 ImportReceipt；`verified_blob_count=2`、`verified_byte_length=40`。

## 6. 重试、冲突与未知提交结果

原样再次执行 `append-v2.json` 及其 payload-map，预期返回原 `result_ref` 和原 `recorded_at`，`replayed=true`，上述记录数不变。不得重新生成 key 或改 ID 来“重试”。

冲突反例：复制 `create-report.json` 为 `create-report-conflict.json`，只将 `body.artifact.type_ref.type_version` 改为 `"2"`，其余字段特别是幂等三元组保持原值。单独执行以下命令，**预期退出 4**、`IDEMPOTENCY_CONFLICT`、`commit_state=not_committed`；原报告身份和历史不变。这是被拒绝输入的预期结果，不是主流程成功退出。

```sh
"$LEDGER_VENV/bin/artifact-ledger" write --db source.sqlite --request create-report-conflict.json
```

无论是否发生响应丢失，都将以下查询保存为 `query-v2.json` 并执行，验证已经提交的 v2 可按原幂等身份找回；§7 也会复用此文件：

```json
{
  "idempotency_scope_ref": "demo:reports",
  "operation_kind": "append_version",
  "idempotency_key": "append-quarterly-v2"
}
```

```sh
"$LEDGER_VENV/bin/artifact-ledger" operation --db source.sqlite --request query-v2.json
```

正常流程预期退出 0，查询返回原幂等记录和完整 v2 记录；核对 `data.idempotency_record.result_ref` 与原结果，并按接口 §6 的规则重算原请求的语义指纹，与 `data.idempotency_record.request_fingerprint` 比较。

附加故障场景：若 v2 写入响应丢失或收到 `DURABILITY_UNKNOWN`，仍用同一查询核对。`NOT_FOUND` 不证明尚在运行的旧调用永不提交；旧执行停止、连接恢复后再核对，或保留原三元组和原请求重试。`BUSY/not_committed` 可按原请求稍后重试；已确认 `committed` 的响应处理错误不能当作回滚。非零退出、断线或没有 JSON 都不能单独证明未提交。

## 7. 完整导出，再导入新的 Ledger

```sh
"$LEDGER_VENV/bin/artifact-ledger" export --db source.sqlite --package report-package.json --descriptor report-package-descriptor.json
"$LEDGER_VENV/bin/artifact-ledger" init --db destination.sqlite
```

预期 export 产生完整 package 和独立 descriptor，包含全部历史、原时间、成功幂等记录及两个 Blob 的 bytes。保留这两份原始文件，不手改或重新序列化后沿用旧摘要；新时间由运行时产生，因此这里不伪造完整 package 或 descriptor。缺件、任何摘要/长度不符或整库超过 profile 限额都不能导入；SQLite 文件不充当 package。

保存为 `import-report.json`：

```json
{
  "profile": "bounded-local-v0.1",
  "contract_version": "0.1.0",
  "operation_kind": "import_bundle",
  "idempotency_scope_ref": "demo:report-copy",
  "idempotency_key": "import-quarterly-001",
  "body": {"import_receipt_id": "receipt:quarterly-copy-001"}
}
```

```sh
"$LEDGER_VENV/bin/artifact-ledger" write --db destination.sqlite --request import-report.json --package report-package.json --descriptor report-package-descriptor.json
"$LEDGER_VENV/bin/artifact-ledger" history --db destination.sqlite --artifact-id artifact:quarterly-001
"$LEDGER_VENV/bin/artifact-ledger" get --db destination.sqlite --kind import_receipt --id receipt:quarterly-copy-001
"$LEDGER_VENV/bin/artifact-ledger" operation --db destination.sqlite --request query-v2.json
"$LEDGER_VENV/bin/artifact-ledger" read-blob --db destination.sqlite --blob-ref blob:quarterly-001-v1 --output copied-v1.txt
"$LEDGER_VENV/bin/artifact-ledger" read-blob --db destination.sqlite --blob-ref blob:quarterly-001-v2 --output copied-v2.txt
"$LEDGER_VENV/bin/artifact-ledger" verify --db destination.sqlite
```

预期新写入返回 `COMMITTED`、`operation_kind=import_bundle`、`result_ref=receipt:quarterly-copy-001`、`replayed=false` 和本次导入时间。Receipt 的 `imported_artifact_refs` 为 `["artifact:quarterly-001"]`。目标中的原身份、版本、父关系、来源、字节、历史幂等结果和原时间应与源库一致；本次导入另新增 1 Receipt 和 1 成功幂等记录，因此目标总计 4 幂等记录、1 Receipt，其余计数及 40 bytes 保持一致，不能要求两个 Ledger 全部状态逐字相同。

```sh
"$LEDGER_VENV/bin/python" - <<'PY'
from pathlib import Path
for original, copied in [("report-v1.txt", "copied-v1.txt"),
                         ("report-v2.txt", "copied-v2.txt")]:
    assert Path(original).read_bytes() == Path(copied).read_bytes(), copied
PY
```

原样重放 `import-report.json`、**同一份原 package 和 descriptor**，预期返回原 Receipt、原导入时间与 `replayed=true`，计数不再增加。不要重新导出目标库再当作原 import 请求重试，也不要把导入当作增量同步：同 Artifact 的历史子集、超集或不同分支都会冲突。

## 8. 此示例的验收边界

A1 应记录确切软件 SHA、wheel 摘要、Python/SQLite 版本、逐步命令/退出码、关键响应以及前后计数，并用库入口完成等价闭环。本文列出的期望尚未由产品执行；输入样例能解析、摘要正确不等于安装、事务和导入已实现。

这说明普通业务程序怎样独立复用同一组件和搬运格式。单机同实现的往返不证明真实 NAS 恢复、多个消费者采用、跨厂商替换或第二实现兼容；这些分别由 A2、A3、A4 取得独立证据。
