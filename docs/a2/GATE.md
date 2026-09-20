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
| Gate state | **OPEN for A2** |
| Requirements | [REQUIREMENTS.md](REQUIREMENTS.md) |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Implementation plan | [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) |
| Normative dependencies | [INTERFACE_PROFILE.md](INTERFACE_PROFILE.md)、[ACCEPTANCE.md](ACCEPTANCE.md) |
| Input implementation baseline | `bd5128e7cebc844d8fca622c791681f7c65184f8`；A1输入，不是A2文档基线 |
| Documentation baseline A | `817f1bff3b2ac8d59dbe22f6234b200c4dcf2ad1`；本轮复审后的三层文档、接口与验收候选版本，由后续bookkeeping固定；替代原候选 `9636b1b7b987986c57399e36b9765799c32e09b9`，尚未获Owner closure |
| Proposed implementation scope | `A2-snapshot-nas-restore-v0.1`，S1–S5 |
| Authorized implementation scope | **none for A2**；当前推进文档准备、审查和只读核对 |
| Owner closure B / CLOSED commit C | 尚无；“推进A2”、设计PR合并和A1授权不自动成为closure |
| Reopen conditions | 三层文档、接口、验收边界、scope、R/A、规则来源/可访问性/完整性、Authority、采用mandate、exceptions或change rule实质变化；原Owner决定失效或无法核实。受影响范围自动OPEN，旧closure不得转授 |

用户同意先准备A2设计PR，再按准确基线审查与关闭Gate。本提交不加production/test source、可执行探针、依赖、schema或运行配置，不做NAS写入、挂载调整、数据删除或恢复演练。

顺序为R → A → Owner明确决定B → 独立CLOSED记录C → 实现D。A是包含三层文档、接口和验收矩阵的既有完整commit，不预写自身SHA；C只保存授权和必要bookkeeping，不混实现或实质设计修改。合并须保留历史；squash产生新基线时重新定位后再确认。

本轮实质修订覆盖UTF-8预算前提、公开失败状态、恢复中断核对及配置固定，并同步验收。提交此设计后再用独立bookkeeping固定新的A；该记录不是CLOSED记录C。后续实质修订须再次固定新的A并按新版本审查，不能自动沿用旧候选基线。
