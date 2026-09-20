# Program Repository Documentation Gate

这是本仓库的采用元数据与开工状态记录，不是上游规则的公开副本。权威规则通过固定的私有 companion 路径读取。

A2 由 [独立 A2 Gate](a2/GATE.md) 管理，已获 Owner 单独授权并记录 CLOSED（S1–S5）。下表仍只记录 A1 的既有授权，两个范围分别追溯。

| 字段 | 当前值 |
|---|---|
| Gate rule source | `kongbu0621/engineering-sop` |
| Gate rule baseline R | `91ac1ad72a423079785725fafd4eb139f5cd7943` |
| Accessible Gate rule source | [固定规则路径](https://github.com/kongbu0621/engineering-sop/blob/91ac1ad72a423079785725fafd4eb139f5cd7943/docs/workflow/program-repository-documentation-gate.md)，Private companion，需有授权的执行者读取 |
| Gate rule source integrity | Direct pinned source；本地无摘录、转换或 snapshot |
| Rule status / adoption | Provisional / Owner-mandated for all program repositories |
| Decision Authority | Owner：`kongbu0621` |
| Adoption exceptions | none |
| Adoption change rule | 禁止自动升级、弱化、撤销或新增 exception；须有 Owner 决策 |
| Gate state | **CLOSED** for `A1-local-ledger-v0.1` |
| Requirements | [REQUIREMENTS.md](REQUIREMENTS.md) |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Implementation plan | [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) |
| Normative interface dependency | [INTERFACE_PROFILE.md](INTERFACE_PROFILE.md)，与三文档一起固定 |
| Consumer documentation | [USAGE.md](USAGE.md)、[REUSE_EXAMPLE.md](REUSE_EXAMPLE.md)，随本轮文档一起固定；字段与语义以规范接口为准 |
| Documentation baseline A | `a74c8f1650699b987f4f299ff95eb5006ab97130`；由历史 bookkeeping 提交固定；当前 closure 提交保留该既有文档基线 |
| Authorized implementation scope | `A1-local-ledger-v0.1`，P1–P5 及计划列明交付 |
| Proposed scope | `A1-local-ledger-v0.1`，仅实施计划 P1–P5 与其明确列出的交付 |
| Owner closure decision B | [Owner 原话副本](decisions/A1_OPENING_DECISION.md)；[稳定转录记录 5750709424](https://github.com/kongbu0621/infra-artifact-ledger/pull/1#issuecomment-5750709424)，转录时间 2026-09-20T15:22:46Z |
| CLOSED state commit C | 包含本状态与决定副本的独立 closure 提交；完整 SHA 由 Git history 定位，后续实现必须以它为祖先 |
| Reopen conditions | 需求、架构、计划、规范接口、scope、R/A、规则来源/完整性、Authority、mandate、exceptions 或 change rule 实质改变；无法核实原决定或读取一致规则来源 |

仓库创建、Connector 授权和 PR #1 文档合并不记录为关闭决定。本次 CLOSED 依据上述单独 Owner 决定 B，仅覆盖指定 R/A/scope；原话与转录 event/time 已保存在独立决定记录。旧阶段的 OPEN 与未授权描述属于下述历史，不否认本次后续授权。

前一文档基线 `00338602338f5d66864b33ba4783c31d5c753d18` 保留为历史，已由本轮兼容性、错误分类和接口边界修订替代，不再代表当前待批准设计。此前没有 Owner closure；本次复审请求也不构成关闭决定。

复用指南修订前的文档基线 `883f1c869117c8dd23ad9a6459d44c0b34a0f98d` 与云端验证提交 `dc24276b69e3d3800f7a81c969ba5a996ce1d3d0` 保留为历史证据。新接入文档与 P5 验收要求须形成新的 A；旧验证报告不能自动覆盖新增内容。本轮修复请求只授权文档完善，未记录为 Owner closure。

接入合同复审前的 A `a6599939de2db91503b97aaefd9ce68d82a184c2` 与云端验证提交 `06d04b1d8599b78e4955b8c600ccb0ac6219153c` 保留为历史。本轮明确 Python 公开出口/参数、上下文事务边界和 metadata 查询含义，并补相应验收反例；这些说明与新 A 一起接受复核，不沿用旧报告作新提交的验收结果。

交付历史必须能够分别定位 R、文档 A、Owner 决定 B、独立 CLOSED 状态 C 与后续实现 D。若按 PR 内 A 批准，应使用保留 A 的 merge 方式；若 squash 产生新基线，先重新确定已合并文档 SHA，再取得对应确认。C 仅记录关闭与必要 bookkeeping，不能同时修改设计或加入实现。

本文件的公开维护状态不赋予私有来源披露权。执行者不能读取 companion 或证明采用有效时保持 OPEN，并指出具体缺口。公开软件的安装与使用则按本仓自包含文档设计，不依赖 companion。
