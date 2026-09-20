# A1 公共接口与可移植封装候选

状态：A0 设计候选，尚无实现或运行验收。本文中的接口、命令、限额和退出码是拟议的 A1 交付要求，不能据此宣称当前仓库已经可安装或运行。实施资格以仓库文档 Gate 为准。

本文件定义 `bounded-local-v0.1` 的公共工程接口。首个实现使用本机 SQLite，在同一事务中保存元数据、有界内容与成功幂等结果。profile 固定有界、单域和原子行为，不要求独立替代实现也使用 SQLite。它不提供 HTTP 服务、身份认证、分布式写入、主动删除或垃圾回收。

## 1. 版本、身份与能力边界

| 名称 | A1 固定候选值或含义 |
| --- | --- |
| profile | `bounded-local-v0.1` |
| contract_version | `0.1.0` |
| contract_status | `candidate` |
| transport_version | `0.1.0`，只版本化本文定义的字节包 |
| Python 包导入名 | `infra_artifact_ledger` |
| CLI 名称 | `artifact-ledger` |
| 实现候选 | 最低 Python 3.11 标准库；SQLite 本地文件；Linux/Python 3.11 必验，3.12 可作附加验证，其他版本和平台支持须另行验证 |

七类 owned ID 共用一个可移植身份空间：Artifact、ArtifactVersion、ContentRoot、Blob、Manifest、ProvenanceLink、ImportReceipt。不同类型也不能使用相同 ID；`namespace_ref` 不划出允许重复 ID 的局部空间。调用者生成并保存稳定 ID；重试保持原 ID 和幂等键。ID 前缀只帮助阅读，不用于判断实际对象类型。

Artifact 的 namespace 与 type 不可修改。相同内容可以拥有不同 Artifact ID、BlobRef；系统不按内容、名称或外部来源合并身份。读取必须指定准确版本，不能将最后写入的版本暗中解释为 current、canonical 或 active。

工作流、Skill、报告、图片等都是待记录的数据。库不会加载或执行其中的代码或工作流节点。外部 principal、policy、capture、runtime 引用不产生认证、授权或领域准入结果。

## 2. 数据类型与完整 metadata 形状

下列约束用于独立实现和消费接口；不要求调用者访问任何其他仓库。所有对象拒绝未声明字段；所有字符串必须是合法 Unicode，拒绝孤立 surrogate。日期字段必须是带时区的 RFC 3339 日期时间；实现生成的新时间使用 UTC `Z`，导入保留原历史字符串，不静默规范化。

| 简写 | 精确含义 |
| --- | --- |
| `StableId` | 长度 3–256；首字符为 ASCII 字母或数字；其余字符只能是 ASCII 字母、数字、`.`、`_`、`:`、`/`、`-` |
| `OpaqueRef` | 长度 1–512；不得含 U+0000–U+001F 或 U+007F；不解析成物理位置 |
| `TypeRef` | 恰含 `namespace`、`type_id`、`type_version` 三个字符串；长度分别 1–128、1–128、1–64 |
| `RelationRef` | 恰含 `namespace`、`relation_id`、`relation_version` 三个字符串；长度分别 1–128、1–128、1–64 |
| `Digest` | 恰含 `algorithm: "sha256"` 和 `value`；value 为 64 个小写十六进制字符 |
| `Time` | 上述日期时间字符串 |

字符串长度按 Unicode 字符计数。不同 Unicode 表示、大小写和数组顺序保持原样；不自动做 Unicode normalization、排序或去重。字段明确要求唯一的数组，重复时拒绝。

### 2.1 Portable metadata 顶层

metadata 是严格 UTF-8 JSON 对象，包含以下全部必需字段，以及一个可选字段 `import_receipts`。

| 字段 | 值 |
| --- | --- |
| `contract_version` | `"0.1.0"` |
| `contract_status` | `"candidate"` |
| `schema_id` | `"urn:code-driver-theory:artifact-ledger:v0.1:candidate"` |
| `theory_baseline` | 恰含 `repository: "kongbu0621/code-driver-theory"`、`commit: "9e2bc8217f05d5b604e24fa56dad2525f35983f1"` |
| `artifacts` | Artifact 数组，可空 |
| `versions` | ArtifactVersion 数组，可空 |
| `content_roots` | ContentRoot 数组，可空 |
| `blobs` | Blob 数组，可空 |
| `manifests` | Manifest 数组，可空 |
| `provenance_links` | ProvenanceLink 数组，可空 |
| `idempotency_records` | 成功幂等记录数组，可空 |
| `import_receipts` | ImportReceipt 数组，可选；缺省按空集合解释，但不改写输入 JSON |

`schema_id` 与 `theory_baseline` 是既有 wire 格式的固定兼容标记。它们不要求下载或阅读所指仓库，不赋予上游网络服务运行职责，也不是本实现已经通过验证的证据。工程 profile 和 transport 的版本放在各自封装中，不改写这些 metadata 字段。

### 2.2 七类记录

除明确标注“可选”的字段外，各行字段均必需。

| 记录 | 字段与约束 |
| --- | --- |
| Artifact | `artifact_id: StableId`；`namespace_ref: OpaqueRef`；`type_ref: TypeRef`；`recorded_at: Time` |
| ArtifactVersion | `version_id: StableId`；`artifact_id: StableId`；`content_root_ref: StableId`；`parent_version_refs: StableId[]`；`external_source_refs: OpaqueRef[]`；`capture_refs: OpaqueRef[]`；可选 `principal_ref: OpaqueRef`；`handling_policy_refs: OpaqueRef[]`；`recorded_at: Time`。四个数组均不允许重复 |
| ContentRoot，blob 类型 | 恰含 `content_root_ref: StableId`、`kind: "blob"`、`blob_ref: StableId` |
| ContentRoot，manifest 类型 | 恰含 `content_root_ref: StableId`、`kind: "manifest"`、`manifest_ref: StableId` |
| Blob | `blob_ref: StableId`；`digest: Digest`；`byte_length` 为非负整数；`payload_availability` 为 `available` 或 `erased`。本 profile 仅支持 `available` |
| Manifest | `manifest_ref: StableId`；`digest: Digest`；`entries` 为非空数组，每项恰含 `entry_key` 与 `blob_ref: StableId`。entry_key 长度 1–512，同一 Manifest 内必须唯一 |
| ProvenanceLink | `provenance_id: StableId`；`subject_version_ref: StableId`；`relation_ref: RelationRef`；`object` 恰含 `kind` 与 `ref: OpaqueRef`；`recorded_at: Time` |
| ImportReceipt | `import_receipt_id: StableId`；`imported_artifact_refs: StableId[]`，唯一且可空；`recorded_at: Time` |

ProvenanceLink.object.kind 只能是 `artifact_version`、`external_source`、`capture`、`principal`、`policy`、`runtime_ref`、`other`。`artifact_version` 的目标必须是存在的 Version；其他类型是 opaque 引用。来源图不代替版本 parent DAG，也不自行证明外部事实。

`entry_key` 是逐字符精确比较的逻辑键，不是路径。读取、导入和导出不将其拼接到文件系统路径；A1 不提供目录展开。

### 2.3 成功幂等记录

每条记录恰含：`idempotency_scope_ref: OpaqueRef`、`operation_kind`、`idempotency_key`、`request_fingerprint: Digest`、`result_ref: StableId`、`recorded_at: Time`。key 为长度 1–256 的字符串。

operation_kind 仅允许 `create_artifact`、`append_version`、`import_bundle`。三元组 `(scope, kind, key)` 唯一。result_ref 分别必须解析为 Artifact、ArtifactVersion、ImportReceipt，不以字符串形似或前缀替代类型检查。拒绝和未知提交状态不是成功记录。

### 2.4 状态闭包

版本属于存在的 Artifact，引用存在的 ContentRoot。所有 parent 必须存在、属于同一 Artifact，且版本关系无环。ContentRoot 与 Manifest 的引用必须存在；所有 ContentRoot 必须至少被一个 Version 引用，所有 Manifest 必须被 ContentRoot 引用，所有 Blob metadata 必须被 ContentRoot 或 Manifest 引用。

Artifact 可以暂时没有 Version；空 Ledger 合法。Provenance 主体与 artifact_version 类型的目标必须存在。Receipt 内 Artifact 引用和幂等结果必须存在。物理暂存文件或数据库实现的废弃页不是 portable 记录，不能导出为不可达内容对象。

Manifest digest 覆盖仅含 `entries` 的 JSON 对象按 RFC 8785 规范化后的 UTF-8 bytes。这个固定形状仅含字符串字段，entries 保持原序，每个 entry 的对象键顺序为 `blob_ref`、`entry_key`。验证时重新计算，不能只检查 digest 字段格式。完整内容验证还须逐 Blob 重算实际 bytes。

## 3. 资源限额

MiB = 1,048,576 bytes。上限包含等于，超过即拒绝；不得截断、舍入或部分发布。

| 资源 | A1 上限 |
| --- | --- |
| 单个 Blob 原始内容 | 64 MiB |
| 一次 append 或 import 传入的原始 payload 总量 | 256 MiB |
| append 待登记 Version 的完整内容闭包 | 不同 BlobRef 的 byte_length 累加最多 256 MiB；包含复用的既有 Blob |
| 单份写请求、operation 查询、payload-map、metadata 或普通读取响应 JSON | 各 8 MiB UTF-8 bytes；package 和 descriptor 使用各自独立限额 |
| 单个完整 transport package | 384 MiB serialized bytes |
| 全包解码后的 payload 总量 | 256 MiB |
| JSON 容器嵌套深度 | 16 层；根对象算第 1 层 |
| 一个 JSON 文档中的对象、数组、标量总节点数 | 500,000 |
| profile 中 byte_length | 整数 `0..9007199254740991`；同时受对应资源限额约束 |

JSON 输入拒绝重复 key、NaN、Infinity、非法 UTF-8、BOM、孤立 surrogate、未知字段及尾随非空白内容。byte_length 不接受浮点或指数数字 token，也不接受布尔值。空 payload 合法，必须校验其实际 SHA-256 与长度 0。

读取文件先施加 serialized 大小限额，解码和累计大小也检查上限；不能等到全部无限制载入后再验证。实现必须记录默认上限下的实测内存需求，不把 serialized 上限等同于内存峰值。实现不得把通用 metadata 能表达的更大数据或 erased 状态改写成支持状态。

单个 Ledger 可以随合法写入增长。A1 导出只支持仍能装入一个上述 package 的完整 Ledger；超限返回 `RESOURCE_LIMIT`，不导出历史子集。分片、增量同步和选择性历史导出须后续另定合同。

append 的传入 bytes 与待登记内容闭包分别计量。相同 BlobRef 被 Manifest 多次引用只在闭包总量中计一次；不同 BlobRef 即使内容相同仍分别计量。不能用“没有重传 bytes”绕开闭包上限。例如 5 个不同的既有 64 MiB Blob 组成一个新 Version 时应拒绝；4 个可达到 256 MiB，仍须通过其余校验。整个 Ledger 的历史累计量不因此限制为 256 MiB。

## 4. 拟议 Python 与 CLI 表面

下表是接口设计要求，不是可执行代码或当前可用命令。库与 CLI 使用同一语义校验与提交路径；CLI 只处理参数和文件传输。

Python 包根 `infra_artifact_ledger` 必须公开导出 `initialize`、`open` 和 `LedgerError`。`initialize(path)` / `open(path)` 为包级函数，返回 Ledger handle；下表其余库操作均为该 handle 的方法。消费者无需导入内部 `service` 或 `sqlite_store` 模块，也不依赖内部 handle 类的路径。

| 库操作 | CLI 候选命令 | 行为 |
| --- | --- | --- |
| `initialize(path)` | `artifact-ledger init --db PATH` | 仅创建新数据库；已存在目标拒绝，不覆盖或自动迁移 |
| `open(path)` | 其他命令的 `--db PATH` | 仅打开既有且版本兼容的 Ledger；不存在时不悄悄创建 |
| `execute(request_utf8, *, payloads=None, package=None, descriptor=None)` | `artifact-ledger write --db PATH --request FILE`，按操作附加 `--payload-map FILE` 或 `--package FILE --descriptor FILE` | 三类写操作的唯一公共提交入口；后三个参数仅按关键字传入，`None` 等同未提供 |
| `get_record(kind, id)` | `artifact-ledger get --db PATH --kind KIND --id ID` | 读取七类记录之一；返回准确 metadata，不声称已检查全部 payload |
| `get_history(artifact_id)` | `artifact-ledger history --db PATH --artifact-id ID` | 一致读取该 Artifact、全部 Version 和以这些 Version 为主体的 Provenance；超出 JSON 响应上限则拒绝 |
| `get_operation(scope, kind, key)` | `artifact-ledger operation --db PATH --request FILE` | 查询文件恰含 `idempotency_scope_ref`、`operation_kind`、`idempotency_key`；返回成功幂等记录及可解析结果，或未找到；用于核对原写入 |
| `read_blob(blob_ref)` | `artifact-ledger read-blob --db PATH --blob-ref ID --output FILE` | 校验实际 bytes 后返回内容；CLI 仅写调用者明确指定的新目标文件，不按 ID 或 entry_key 推导路径 |
| `verify()` | `artifact-ledger verify --db PATH` | 一致检查全部记录、闭包、幂等结果、Manifest 与实际 payload，返回计数及失败项 |
| `export_bundle()` | `artifact-ledger export --db PATH --package FILE --descriptor FILE` | 从一致读取视图导出完整 Ledger 与全部 available bytes；输出两个新文件，拒绝覆盖 |

库写操作接收 UTF-8 JSON bytes，使严格解析和数字 token 规则一致；不提供可绕过校验的“信任调用者 dict”入口。payloads 是 BlobRef 到原始 bytes 的传输参数，不是 portable 记录字段。库只负责已有调用进程内的调用，不自动启动后台服务。

request_utf8、package、descriptor 的库参数使用不可变 bytes；payloads 为 BlobRef 到不可变 bytes 的映射。进入调用时冻结映射项并按限额校验，后续写入必须消费同一份已验证 bytes；CLI 不能校验一次文件、再重新打开可能已变化的路径写入。未使用的参数省略，append 无需传入 bytes 时 payloads 为空映射。Ledger handle 提供 close 与关闭连接的上下文管理，不能让用户猜测连接释放方式。

具体参数组合：create 只提供 request_utf8，其他参数均为 None；append 必须提供 payloads 映射（无传入 bytes 时显式 `{}`），package/descriptor 为 None；import 必须提供 package 与 descriptor bytes，payloads 为 None。空 bytes `b""` 是已提供的内容，不等同缺省；新增空 Blob 仍须有对应 BlobRef 的空 bytes，不能用 `{}` 代替。非适用参数或缺失必需参数返回 INVALID_INPUT，不触发写入。

CLI 使用同一组合：append 必须提供 `--payload-map`；无传入 bytes 时该文件为 `[]`，映射成 `payloads={}`，新空 Blob 则仍需一项 input_path 指向 0 字节文件。create/import 不接受 `--payload-map`，create/append 不接受 `--package` 或 `--descriptor`；import 必须同时提供后二者。缺少必需选项或提供不适用选项均返回 INVALID_INPUT，不触发写入。

handle 的上下文管理只负责资源生命周期：进入返回该 handle，退出关闭连接，不吞掉异常。每次 execute 仍是独立写事务；`with` 块中先后 create、append v1、append v2，若第三次失败，前两次已确认的提交仍然存在。close/退出上下文不把多次操作合成一个外层事务，也不撤销已确认提交；调用者保留每次原请求并逐次核对结果。

`get_record` 的 kind 允许 `artifact`、`version`、`content_root`、`blob`、`manifest`、`provenance_link`、`import_receipt`，按真实类型解析。history 的集合返回顺序采用对应 owned ID 的 ASCII 升序；每条记录内的数组保持原序。这个读取展示顺序不改变写入指纹。

CLI 的普通成功 `data` 固定如下；不存在所请求对象时走错误 envelope，不以空对象伪装成功。

| 操作 | `data` 字段形状 |
| --- | --- |
| init | 恰含 `profile` 与 `storage_schema_version: 1`；本地 schema 版本不进入 portable metadata |
| get | 对应类型的完整记录对象本身，字段见 §2 |
| history | 恰含 `artifact`、`versions`、`provenance_links`；后两个数组允许空 |
| operation | 恰含 `idempotency_record`、`result`；result 是依 operation_kind 解析出的完整 Artifact/Version/ImportReceipt 记录 |
| read-blob | 恰含 `blob_ref`、`byte_length`、`digest`；实际 bytes 写入显式输出文件 |
| verify | 恰含 `counts`、`verified_blob_count`、`verified_byte_length`；counts 恰含 §2.1 八个记录集合名，各值为非负记录数量 |
| export | 恰含 `descriptor`，值为 §7 完整包 descriptor |

counts 的八个名称为 artifacts、versions、content_roots、blobs、manifests、provenance_links、idempotency_records、import_receipts。计数和累计字节使用 `0..9007199254740991` 的整数，溢出明确 `RESOURCE_LIMIT`，不舍入。verify 只有全部检查通过才返回 OK；失败时按 §6 返回 INTEGRITY_FAILURE，details 恰含 `failures` 与 `truncated`：前者最多 100 项，每项恰含字符串 `location`、`reason`，后者说明失败列表是否有省略。省略报告不允许提前宣称未检查内容已通过。

Python initialize/open 返回本地 Ledger handle，execute 返回 §6 写成功 envelope；get_record/get_history/get_operation/verify 返回上述 data 形状。read_blob 返回已经验证的原始 bytes；export_bundle 返回恰含 `package_utf8` 与 `descriptor_utf8` 的两份 bytes。领域失败统一抛出 LedgerError，携带 §6 的 code、message、commit_state、可选 details，由 CLI 映射成同一错误 envelope；不泄漏 SQLite 异常作为公共合同。

普通 JSON 输出采用 §6 的对象键、字符串和整数编码规则，布尔值使用 JSON 的 true/false，一行紧凑 UTF-8 JSON 加结尾 LF。响应大小包含完整 envelope 与 LF；库的对应读取在返回 data 前也按这一 envelope 计量，使限额判断与 CLI 一致。CLI 不把日志混入 stdout；诊断送 stderr。read-blob 的 bytes 进入明确输出文件，stdout 仍只输出操作结果。输出文件先完整写入并校验后再发布；失败不留下被标为成功的输出。package 与 descriptor 两个文件不是一个文件系统原子操作：任何缺件、摘要不符或不完整组合都不能导入，CLI 只有两者完成后才报成功。

get_operation 未找到不自动证明并发或尚在恢复的旧调用永不提交；只有在旧执行已停止、连接恢复并核对提交状态后，才可判定未提交。调用者始终保留原请求以按原 key 重试。

get_record、get_history 与 get_operation 都是 metadata 查询，不证明关联 payload 当前完整。get_operation 的成功只确认已记录的历史提交与结果引用；消费者应核对原幂等三元组及 request_fingerprint，再按需要用 read_blob 读取并校验具体内容，或用 verify 核验全库。某个 Blob 在提交后损坏时，metadata 与成功记录仍可查询到；这不改变原提交事实，也不能代替内容校验或触发自动修补。

operation 查询文件适用相同 strict JSON 和 8 MiB 限额，各字段约束与写请求一致。合法幂等键可能包含 JSON 转义的 U+0000 等控制字符，操作系统 argv 无法承载其中某些值；因此查询通过 JSON 文件传递，不收窄 portable key 的语义。库/CLI 必须验证这种键的写入、查询与重放闭环。

## 5. 写请求与三类操作

请求顶层固定包含 `profile`、`contract_version`、`operation_kind`、`idempotency_scope_ref`、`idempotency_key`、`body`；可选 `request_fingerprint`。profile 与 contract_version 必须匹配本文。不接受传入的新 recorded_at。

### 5.1 create_artifact

body 恰含 `artifact`，后者恰含 `artifact_id`、`namespace_ref`、`type_ref`。不接受 payload 或 package。成功事务写入新 Artifact 与幂等记录；Artifact 可以没有 Version。

同一成功请求的重试返回原结果。使用不同幂等身份 create 已存在 Artifact，即使三个字段相同，也返回 `IDENTITY_CONFLICT`，不把它解释为新成功事件。

### 5.2 append_version

body 恰含 `version`、`content_roots`、`blobs`、`manifests`、`provenance_links`。version 使用 §2 的 Version 字段但不含 recorded_at；四个辅助数组必须显式提供，无新增记录时为 `[]`。新 ProvenanceLink 使用 §2 的字段但不含 recorded_at。

新增 Version 必须属于既有 Artifact。父集合可以为空；多个父表示调用者明确登记合并版本，仍由调用者提供合并后的内容，Ledger 不合并 bytes 或选择分支。首次 Version 不要求父引用；其他版本也不能被自动连接到所谓 latest。

本 profile 的 append 只新增主体为本次新 Version 的 ProvenanceLink；给既有 Version 后补来源不在 A1 接口内。新 Version/Provenance ID 不可复用；ContentRoot、Manifest、Blob 可以复用完全相同的既有记录，任一 immutable 字段不同则冲突。

bytes 通过 payloads 传入。每个新 available Blob 必须有一份 bytes，digest 和长度必须重算；复用现有 Blob 时可不重传，但仍验证持久 bytes。显式重传现有 Blob 时也验证该输入。payload 不得引用本次内容闭包之外的 Blob，重复 BlobRef 拒绝。一次写入不新增与本次 Version 无关的内容记录。

CLI payload-map 是传输用 JSON 数组，每项恰含 `blob_ref` 与字符串 `input_path`；它适用 strict JSON、8 MiB serialized 上限以及 §3 的深度/节点限制，必须在无限制分配或读取内容文件前检查。重复 BlobRef 拒绝。input_path 只由 CLI 读取，不进入 metadata、指纹或导出包；操作系统无法表示的路径返回 INVALID_INPUT，不静默截断。CLI 对调用者指定的输入文件读取真实 bytes 后使用相同库入口，不把文件名当成身份。

### 5.3 import_bundle

CLI/library 传输请求的 body 恰含 `import_receipt_id`，另提供 §7 的 package 与 descriptor。适配层验证封装并严格解析 metadata 后，构造本操作的语义 body：`{import_receipt_id, bundle}`，其中 bundle 是完整解析后的 metadata。只允许这一确定映射；当前 transport 字段不混入语义指纹。

整个输入先完成验证，再在提交事务中重新核对目标。对于重复 Artifact ID，Artifact 本身、该 Artifact 的全部 Version 及以其 Version 为主体的全部 Provenance 历史必须完全一致；版本子集、超集、不同分支均为冲突。记录集合比较不依赖顶层集合排列，但记录内字段、数组顺序和历史时间必须一致。引用的内容记录及 bytes 仍须分别核对。

其他已存在 owned ID 只允许相同类型、相同 immutable 记录收敛；跨类型冲突直接拒绝。包内历史幂等三元组如已存在，完整记录必须相同，否则返回 `IDEMPOTENCY_CONFLICT`；不能只因 result_ref 相同就覆盖指纹或时间。除已确认的原成功请求重放外，当前导入的幂等三元组若与包内已有成功记录相撞，返回 `IDEMPOTENCY_CONFLICT`，不能由本次新 Receipt 抢占该身份。

本次 import_receipt_id 必须是新 owned ID，与现有 Ledger 和输入包均不碰撞；同一已成功请求重试则返回原 Receipt。重放判定先于把原 Receipt 误判为新 ID 碰撞：严格解析并计算指纹、验证提供的内容后，在事务内先查询当前三元组，再决定重放或进入新提交的碰撞检查。Receipt 的 imported_artifact_refs 是输入 Artifact ID 集合，去重后按 ASCII 升序保存，可为空。本次 Receipt、幂等结果、全部被接受的输入 metadata 与 bytes 在同一事务公开。原包内 Receipt 是历史记录，不充当本次 Receipt。

发现 erased Blob 时返回 `UNSUPPORTED_PROFILE`，整个目标保持不变。不能将 erased 改写成 available，不能从旧副本补回 bytes。导入不是增量同步、版本追加或自动 merge。

## 6. 指纹、重试、提交与错误

语义指纹仅覆盖固定 envelope：`contract_version`、`idempotency_scope_ref`、`operation_kind`、`body`。create/append 的 body 如 §5；import 使用包中全部 parsed metadata 构造 bundle，保留历史 recorded_at 和历史幂等记录。当前 key、当前成功时间、响应、文件位置和字节封装不加入 envelope。若调用者提供 request_fingerprint，服务端仍重算并比对。

确定编码：已声明的 ASCII 对象键按 ASCII 升序；数组保持输入序；可选字段缺省保持缺省；字符串不做 Unicode normalization。双引号、反斜杠转义；控制字符使用 JSON 的 `\b`、`\t`、`\n`、`\f`、`\r`，其余 U+0000–U+001F 使用小写 `\u00xx`；其余合法字符直接 UTF-8 输出，不转义 `/`。整数使用无前导零十进制；不加空白、BOM 或换行。对结果 bytes 算 SHA-256。

这是有限操作形状的指纹编码，不宣称通用 JSON canonicalizer。Manifest digest、metadata 原始字节 digest、全包 digest 和请求指纹分别验证，不能相互替代。包内数组改序可能改变请求指纹；调用者应保存原请求。不能因为 history 集合相等就重写某次请求的指纹。

同一幂等三元组和同一指纹收敛到原 result_ref、原历史时间，不产生新版本、Receipt 或成功记录；不同指纹返回 `IDEMPOTENCY_CONFLICT`。提交后响应丢失时使用原三元组核对或原请求重试，不另换 ID/key。输入 bytes 始终按约定验证；已提交内容后来损坏时另报完整性失败，重试不修补历史或再造 Version。

import 重试须保存原 package 和 descriptor 以及原请求。重新导出当前 Ledger 得到的包可能包含新增 Receipt、版本或不同数组内容，不能充当原输入。

SQLite 同一写事务负责 owned ID 占用、引用闭包、metadata、BLOB 和幂等结果。单写入者表示每一时刻最多一个写事务；多个进程争用同一 Ledger 时只允许序列化成功或明确 BUSY，不能各自产生不同 durable 结果。提交前的可恢复失败必须确认回滚；提交或回滚状态无法确认时只能报告未知。COMMIT 锁冲突不能直接当作事务未发生；本 SQLite backend 在确认完整回滚后才返回 BUSY/not_committed，回滚不能确认则返回 DURABILITY_UNKNOWN。

| status/code | CLI exit | 结果语义 |
| --- | --- | --- |
| `COMMITTED` | 0 | 三类写操作提交已确认；返回 result_ref、operation_kind、原 recorded_at；必须带 replayed 布尔值，但不是历史事件 |
| `OK` | 0 | 读取、校验、init 或导出成功，不伪装成第四种写 operation_kind |
| `INVALID_INPUT`、`UNSUPPORTED_VERSION`、`UNSUPPORTED_PROFILE`、`RESOURCE_LIMIT` | 2 | 已确认本次未修改 Ledger；返回具体错误位置或限额名称 |
| `NOT_FOUND` | 3 | 所需准确对象或成功操作未找到 |
| `IDENTITY_CONFLICT`、`IDEMPOTENCY_CONFLICT` | 4 | 已确认本次未修改 Ledger；不得自动 rename 或换 key 重发 |
| `INTEGRITY_FAILURE` | 5 | 摘要、长度、引用闭包或已存内容失败；不得报告内容完整可读 |
| `BUSY` | 6 | 锁冲突且已确认本次未提交：未取得写事务，或完整回滚已确认；原请求可稍后重试 |
| `DURABILITY_UNKNOWN` | 7 | 无法确认提交状态；必须保留原幂等身份核对 |
| `IO_ERROR`、`INTERNAL_ERROR` | 1 | 状态已知时报告对应错误：未提交为 not_committed，提交后处理失败为 committed，读取/辅助输出为 not_applicable；只有状态确实无法确认时使用 DURABILITY_UNKNOWN |

JSON 错误响应恰含 `status: "ERROR"`、`code`、`message`、`commit_state`，以及可选 `details` 对象；code 使用上表分类。commit_state 为 `not_committed`、`unknown`、`committed` 或对于纯读取的 `not_applicable`。已确认原请求提交、但重放时发现其持久 bytes 损坏，应返回 `INTEGRITY_FAILURE`、`commit_state: "committed"`，在 details 保留原 result_ref；不能宣称旧操作已回滚。`not_committed` 只描述本次被拒绝的输入未产生提交，不否认相同 key 以前可能绑定的另一成功请求。进程被终止可能没有 JSON 或上述退出码，调用方不得将这种传输失败等同于未提交。

COMMIT 已确认成功后，若响应准备或资源清理失败且仍能构造错误响应，使用 IO_ERROR/INTERNAL_ERROR 与 commit_state=committed；details 必须保存 result_ref、idempotency_scope_ref、operation_kind、idempotency_key、原 recorded_at。不能把已知成功改报 unknown 或 not_committed，也不能为补发结果追加新记录。若 stdout 等响应通道已经失效，可能没有可读 JSON；调用者使用原请求身份核对，不能依非零退出码推断未提交。纯读取、export/read-blob 输出失败使用 not_applicable，只说明未改变 Ledger，不声称输出文件不存在或已持久化。

写成功响应恰含 `status: "COMMITTED"`、`commit_state: "committed"`、`operation_kind`、`result_ref`、`recorded_at`、`replayed`。普通成功响应恰含 `status: "OK"`、`commit_state: "not_applicable"` 与 `data`；data 携带对应读取记录、校验报告或辅助操作结果。

下面是合成 create 请求与响应示例；时间由首次成功提交生成，示例不代表已经运行：

```json
{
  "profile": "bounded-local-v0.1",
  "contract_version": "0.1.0",
  "operation_kind": "create_artifact",
  "idempotency_scope_ref": "demo:local",
  "idempotency_key": "create-report-001",
  "body": {
    "artifact": {
      "artifact_id": "artifact:demo-report-001",
      "namespace_ref": "demo:reports",
      "type_ref": {"namespace": "demo", "type_id": "report", "type_version": "1"}
    }
  }
}
```

```json
{
  "status": "COMMITTED",
  "commit_state": "committed",
  "operation_kind": "create_artifact",
  "result_ref": "artifact:demo-report-001",
  "recorded_at": "2026-09-20T00:00:00Z",
  "replayed": false
}
```

响应丢失后提交原请求，成功结果保持 result_ref 与 recorded_at，replayed 为 true；该字段属于响应，不写成新的历史事件。若改用相同 key 提交不同 type_ref，则返回错误：

```json
{
  "status": "ERROR",
  "code": "IDEMPOTENCY_CONFLICT",
  "message": "The idempotency identity is already bound to a different request.",
  "commit_state": "not_committed"
}
```

## 7. 完整字节包与导出

package 为不压缩的 UTF-8 JSON，顶层恰含：`transport_version`、`profile`、`metadata`、`payloads`。不使用 ZIP/TAR，不展开目录，不按 BlobRef 生成文件路径。

| 字段 | 形状 |
| --- | --- |
| `transport_version` | `"0.1.0"` |
| `profile` | `"bounded-local-v0.1"` |
| `metadata` | 恰含 `encoding: "base64"`、`data`、`byte_length`、`digest: Digest` |
| `payloads` | 数组；每项恰含 `blob_ref`、`encoding: "base64"`、`data`、`byte_length`、`digest: Digest` |

data 使用 RFC 4648 标准 Base64 字母表、正确 padding、无空白且无非字母表字符；解码后重编码必须得到原字符串。metadata.data 解码后是 §2 的完整原始 UTF-8 JSON，metadata digest 和长度覆盖这份精确 bytes，不先重新序列化。payload digest 和长度覆盖解码后的原始 bytes。

payloads 恰好覆盖 metadata 中全部 available BlobRef，每个一次，无缺失、重复或额外项。每项长度/digest 必须同时与 metadata 中对应 Blob 及实际 bytes 一致。即使目标已有相同 Blob，完整输入包也必须带齐全部 bytes。

外部 descriptor 是独立严格 UTF-8 JSON，恰含 `transport_version: "0.1.0"`、`package_byte_length`、`package_digest: Digest`。它对整个 package 文件的实际 bytes 计算长度与 SHA-256，解决把全包摘要放回包内的自引用问题。descriptor 文件不得超过 4 KiB；package_byte_length 使用相同整数规则并受 384 MiB 上限约束。import 要求 package 与 descriptor 同时提供。

导出采用一个一致读取视图：metadata 与 bytes 来自同一已提交状态。metadata 的各记录集合按 owned ID 排序；幂等记录按 scope、kind、key 的 Unicode 字符序排序。记录内数组不改序。导出显式包含 import_receipts 数组，payloads 按 BlobRef 的 ASCII 升序。全包文件可使用确定紧凑 JSON，但导入只要求合法封装和精确摘要，不要求其他实现产生逐字节相同的包装空白。

descriptor 证明传输完整性，不证明发送者身份或来源真实性。重算全部 bytes、关系与冲突后才能导入；一个 SHA-256 字段看起来合法不足以接受。

SQLite 文件不是 portable package。A1 不提供 snapshot、NAS 搬运或恢复 CLI；这些属于后续阶段，不能把文件复制或 export/import 的成功冒充真实恢复验证。

## 8. A1 必须覆盖的验收反例

| 类别 | 必须验证的行为 |
| --- | --- |
| 输入与边界 | 重复 key、非法 Unicode、未知字段、错误版本、整数浮点/指数 token；所有尺寸阈值的低于/等于/超过；payload-map 超限及低节点数/长路径输入；0 byte 内容；空 Ledger 与空包 |
| 身份 | 七类 ID 跨类型碰撞；相同内容不同 ID；已有 Artifact 再 create；不可变 namespace/type 不可改 |
| 版本与内容 | 缺失/跨 Artifact parent、分支与显式多父 merge；错误 Manifest digest；逻辑键大小写和 Unicode 差异；不可达内容；实际 bytes 缺失或被改动；复用 bytes 仍计入 Version 闭包上限；校验后输入文件被替换不能改变提交 bytes |
| 幂等与故障 | 相同请求重放、同 key 不同请求、合法控制字符 key 经 CLI 写入/查询/重放、竞争写入、提交前回滚、COMMIT 持锁回滚、提交后响应丢失、已确认提交后的处理异常、未知状态核对；COMMITTED 始终含 replayed；不得产生重复历史；同一 handle 后续写入失败不撤销此前提交 |
| 导入 | 相同完整历史收敛；子集/超集/不同分支冲突；输入后段失败时全无部分提交；当前请求与包内历史幂等冲突；Receipt ID 冲突；erased 拒绝；导入后目标新增版本，原 key/原包重放仍收敛原 Receipt，新 key/新 Receipt 导入旧包则按完整历史差异拒绝 |
| 封装 | metadata bytes/hash/length 不一致；重复/缺失/额外 payload；无效 Base64；全包 descriptor 不符；中断或仅生成一个输出文件不能视为可导入成果 |
| 读取与重启 | 原进程退出后新进程仍可读出准确身份、版本、来源及 bytes；已损坏内容不能返回完整成功；仅 bytes 损坏时成功操作仍可查询，read_blob/verify 必须识别损坏，不能以查询成功替代内容验收 |
| 范围诚实 | 只证明声明的本地 profile；未执行真实 NAS 恢复、两个实际消费者与独立第二实现之前，不宣称相关更高阶段已经完成 |

这些是后续实现的验收要求；本 A0 文档没有执行它们，也不以静态文档校验代替它们。
