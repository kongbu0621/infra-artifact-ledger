# A2 验收矩阵与GX10/NAS演练

状态：**Candidate；全部A2执行项当前NOT_RUN**。这是 [实施计划](IMPLEMENTATION_PLAN.md) 的规范性附件；规划不代表测试已编写/运行。字段见 [接口](INTERFACE_PROFILE.md)。

## 1. 环境与数据前置

记录固定source SHA、wheel hash、Python/SQLite/内核/架构。GX10现有3.12.3/3.45.1/ext4只是起点，不自动获得A2通过；Linux/Python3.11必验。build/runtime venv独立，测试数据限定本项目acceptance/run-id。

部署侧提供真实mount source、root、fstype、mountpoint、archive_root、storage_ref。能力探测在独立合成目录验证mkdir/O_EXCL、同FS hardlink拒覆盖、文件/目录fsync、关闭重开、并发冲突和挂载丢失；凭据不记日志。能力未通过标BLOCKED，不用本机目录替代真实NAS。

不卸载整个业务共享盘、不关闭NAS、不改全机mount/service；挂载丢失/断网优先用隔离测试挂载或故障注入。会影响其他业务的设备操作另行安排。新增验收代码在A2 CLOSED后编写。

## 2. 验收映射

| ID | 需求/责任 | 正反例与检查 | 通过依据 |
|---|---|---|---|
| A2-T01 | R01 / SQLite | 空库、多版本/分支/Manifest/来源/已有导入；T0前后写握手、未提交事务、backup失败 | 固定完整状态、不混后续提交、源不被快照修改 |
| A2-T02 | R01/R07 / 锁 | 锁竞争、hot journal、WAL/未知schema、期限、backup增长 | 明确失败、有限检查、源锁在本地backup后释放 |
| A2-T03 | R02 / 格式 | 缺/多成员、symlink/hardlink数据/FIFO、非法ID、marker先到、重复键/超深JSON | 不完整或不匹配拒绝，不以marker单独判完成 |
| A2-T04 | R04 / 校验 | 结构坏、外键坏、合法SQLite但业务关系/幂等坏、Blob坏、summary伪造 | 物理、外键和业务校验各有能捕获的反例 |
| A2-T05 | R03 / 挂载 | 正确NAS、未挂载同名本机目录、换export/root、非预期子挂载/overlay、fd mnt_id不符、运行中变化；完整根bind可识别性限制 | 不回退本机，非预期绑定拒绝/unknown，不声称识别所有bind |
| A2-T06 | R03 / 发布 | DB/manifest/marker前中后中断，短写/空间/权限/回读失败 | 旧代不变；未完成不可读；不明如实报告 |
| A2-T07 | R03/R06 / 冲突 | 两进程同ID、既有不完整目录、同ID不同内容 | 至多一个独占，无覆盖/续传/自动清理 |
| A2-T08 | R02/R03/R06 / 不明 | marker link后fsync失败、mount消失、stdout断开；create响应完全丢失且尚无manifest hash | 原ID/hash核对当前状态；create按接口自洽性流程处理，不伪称事先摘要、不重复发布 |
| A2-T09 | R07 / 资源 | 文件1GiB、metadata16MiB、refs32MiB、全部TEXT64MiB、行数及JSON limit/limit+1 | 合格上限通过，超限拒绝，记录实测RSS/时间 |
| A2-T10 | R04 / 固定读取 | 验证后路径替换、复制中修改、超长度、digest错、截断 | 验证/发布绑定同份暂存，不偷换对象 |
| A2-T11 | R05 / 拒覆盖 | 新目录；旧空/非空目录、断链、旧sidecar、并发同目标 | 旧对象完整保留，只有独占目录可发布 |
| A2-T12 | R05/R06 / 恢复发布 | copy/verify/link前失败，link后同步失败，丢响应/终止 | 不公开半库，公开后保留，check_restore不写 |
| A2-T13 | R05 / 状态延续 | 逐行/全部Blob，旧key重放、冲突重试、新请求 | 不增Receipt/operations，旧结果保持，新写只在测试库 |
| A2-T14 | R08 / 接入 | wheel独立venv，五入口API/CLI、参数/错误/JSON，全部A1回归 | 不从checkout导入，无私有依赖，3.11及GX10分别记录 |
| A2-T15 | R09 / 真NAS | 保存→独立回读→移除本地测试副本→NAS恢复→全量核对 | 真挂载证据、无fallback、原业务数据不动 |

T09还包含累计payload>256MiB且SQLite文件>384MiB的合法库；文件limit±1与合法页边界按实施计划区分。所有注入测试检查失败后的实际状态，不能只断言报错。

预算边界区分“可达的合法输入”和解析/预检器边界。合法可达上限须真实成功并检查超限；固定schema或其他更紧预算使某边界不可独立达到时，在解析/预检层核验limit±1，之后仍可按字段/语义拒绝。不得要求1024节点marker等畸形对象在限额处整流程成功，也不得把未触达的实际边界标成已验。

## 3. 真实演练步骤

1. 创建专属run-id并记录路径所有权；合成数据覆盖七类owned record、成功幂等、分支、多父、内容复用。保存独立小型预期清单：精确metadata/关系/operations摘要、每Blob长度/hash及合成数据生成参数；不保存可直接还原数据库的完整副本作fallback。
2. create快照，保管snapshot_id、manifest hash、数据库hash、软件来源。预期清单对应固定快照状态；不用后来变化的源状态作比较。关闭业务写入连接。
3. publish真实NAS，新句柄回读并完整验证；独立进程再读取NAS generation。可用独立客户端补强缓存边界；没有则如实记录，不宣称物理断电已验。
4. 仅在NAS验证成功后，移除本次run-id产生的源库、本地快照和全部可恢复暂存。逐项证明路径属于本次专属目录、由本次创建、不是链接/挂载点/业务库；任一证明失败停止。不得删除任意用户路径，也不以仅重命名源库代替丢失实验。
5. 全新进程仅从NAS generation恢复到全新目录，记录输入来源和本地副本缺失；逐项对账owned记录、operations、refs和Blob实际内容，不只看计数。
6. 写入启用前check_restore，随后按原key重放检查原结果和计数；最后在恢复验收库新增版本确认可继续使用。保留首次恢复hash与新增写入后的状态区别。

## 4. 报告与停止条件

实现阶段A2_VALIDATION记录source/tree、wheel、实际解释器/包路径、命令/退出码、PASS/FAIL/BLOCKED/NOT_RUN、失败注入点与副作用、耗时/RSS、storage profile及未测事项。原始证据留Owner数据目录，公开脱敏副本分别计算hash，不提交数据库、payload、凭据或内部NAS配置。

profile不支持、mount身份不明、hash/语义不符、unknown未解释、拒覆盖失效或A1回归失败阻止对应完成声明。A2结束不自动证明真实断电、冗余备份、Git Authority、Local Hand、A3或A4。
