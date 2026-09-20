# A2 文档开工状态

本文件只管理A2；A1既有CLOSED范围由 [主Gate](../PROGRAM_REPOSITORY_DOCUMENTATION_GATE.md) 管理。A2不借用A1的Owner closure。

| 字段 | 当前值 |
|---|---|
| Gate rule source | `kongbu0621/engineering-sop` |
| Gate rule baseline R | `91ac1ad72a423079785725fafd4eb139f5cd7943` |
| Accessible rule source / integrity | [固定私有companion](https://github.com/kongbu0621/engineering-sop/blob/91ac1ad72a423079785725fafd4eb139f5cd7943/docs/workflow/program-repository-documentation-gate.md)；direct pinned source，不复制正文 |
| Rule status / adoption | Provisional / Owner-mandated |
| Decision Authority | Owner：`kongbu0621` |
| Exceptions / change rule | none；不得自动升级、弱化、撤销或新增exception |
| Gate state | **CLOSED for A2**；仅下述固定 R/A/scope |
| Requirements | [REQUIREMENTS.md](REQUIREMENTS.md) |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Implementation plan | [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) |
| Normative dependencies | [INTERFACE_PROFILE.md](INTERFACE_PROFILE.md)、[ACCEPTANCE.md](ACCEPTANCE.md) |
| Input implementation baseline | `bd5128e7cebc844d8fca622c791681f7c65184f8`；A1输入，不是A2文档基线 |
| Documentation baseline A | `29ae340addde172781aa2199f864dbb25ea29ccc`；源保护修订后的三层文档、接口与验收候选版本，由后续bookkeeping固定；替代 `817f1bff3b2ac8d59dbe22f6234b200c4dcf2ad1`（更早候选 `9636b1b7b987986c57399e36b9765799c32e09b9` 已被替代）；现由下述 Owner 决定批准，基线内容保持 |
| Proposed implementation scope | `A2-snapshot-nas-restore-v0.1`，S1–S5 |
| Authorized implementation scope | `A2-snapshot-nas-restore-v0.1`，S1–S5 及固定计划列明的交付 |
| Owner closure B | [准确原话及上下文基线副本](../decisions/A2_OPENING_DECISION.md)；记录事件 `A2-OWNER-CLOSURE-20260920-01` |
| CLOSED commit C | 包含此状态及决定副本的独立提交；完整 SHA 由 Git history 定位，实现 D 必须以其为祖先 |
| Reopen conditions | 三层文档、接口、验收边界、scope、R/A、规则来源/可访问性/完整性、Authority、采用mandate、exceptions或change rule实质变化；原Owner决定失效或无法核实。受影响范围自动OPEN，旧closure不得转授 |

Owner 已在设计 PR #5 合并后明确批准上述固定规则、文档基线与 S1–S5。此 CLOSED 提交只保存授权及状态记账，不包含实现。五份规范文档保留批准时原文，其中 Candidate / OPEN / NOT_RUN 描述的是设计基线形成时的状态；当前开工权限以本文件为准，实际验收另行留证。NAS 服务配置、生产数据处理、业务切换、Local Hand 接纳、Git Authority 和自动调度仍不在本 scope 内。

顺序为R → A → Owner明确决定B → 独立CLOSED记录C → 实现D。A是包含三层文档、接口和验收矩阵的既有完整commit，不预写自身SHA；C只保存授权和必要bookkeeping，不混实现或实质设计修改。合并须保留历史；squash产生新基线时重新定位后再确认。

设计历史：A 在上一候选的 UTF-8 预算、公开失败状态、恢复中断核对及配置固定基础上，补齐 WAL 打开前拒绝、源头预检进程隔离及源绑定稳定前置，并同步验收；先由独立 bookkeeping 固定，再通过 PR #5 合并。当前 C 不改变其规范内容；后续实质修订须再次固定新的 A 并按新版本审查，不能自动沿用本授权。
