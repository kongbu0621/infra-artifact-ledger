# A2 构建、GX10 与 NAS 验收手册

目标是把固定源码构建成独立 wheel，分别保存 Linux/Python 3.11、GX10 本地运行和真实 NAS 恢复的证据。本文命令不会自动挂载 NAS、配置服务或接管业务库；所有演练使用本项目新建的合成数据目录。软件为 `0.2.0a1` alpha，尚无 PyPI 发布。实际完成项以 [A2_VALIDATION](A2_VALIDATION.md) 为准；本手册的存在不代表命令已在 GX10/NAS 运行。

操作语义与失败处理见 [A2_USAGE](A2_USAGE.md)，规范验收项见 [A2 验收矩阵](a2/ACCEPTANCE.md)。五份设计基线不因编写此手册改变。

## 1. 机器和目录约定

每台机器、每个项目独立虚拟环境；本阶段另建 build/runtime 两个环境，避免替换现有 A1 环境。代码、环境和数据分开。GX10 当前工作根为 `$HOME/xxj/works`，其他机器自行替换；不要把 GX10 的绝对路径用于 PRO6000 或 Windows。

| 类别 | GX10 示例 |
|---|---|
| 已有仓库 | `$HOME/xxj/works/projects/infra-artifact-ledger` |
| 固定提交验收 checkout | 同级新建 `infra-artifact-ledger-a2-<完整SHA>` |
| A2 构建环境 | `$HOME/xxj/works/venvs/infra_artifact_ledger_a2_build_env` |
| A2 运行环境 | `$HOME/xxj/works/venvs/infra_artifact_ledger_a2_runtime_env` |
| 本地证据根 | `$HOME/xxj/works/data/infra-artifact-ledger/acceptance` |
| 每次运行 | 证据根下新的 `a2-...` 子目录；不要复用上次目录 |

本地数据库、输出、暂存和恢复父目录须在已存在的 ext4/xfs/btrfs 目录上。下面命令先查看挂载；若实际为 overlay、tmpfs、网络文件系统或未知类型，应选另一可靠本地目录，不通过参数或修改分类函数把它伪装为已支持。

```sh
LEDGER_BASE="$HOME/xxj/works"
LEDGER_LOCAL_ACCEPTANCE_PARENT="$LEDGER_BASE/data/infra-artifact-ledger/acceptance"
findmnt -T "$LEDGER_LOCAL_ACCEPTANCE_PARENT" -o TARGET,SOURCE,FSTYPE,FSROOT
hostname
uname -m
python3 -c 'import sys, sqlite3; print(sys.version); print(sys.executable); print(sqlite3.sqlite_version)'
```

父目录不存在时先由部署者在选定本地文件系统创建它，再开始验收。现有 GX10 的 Python 3.12.3 / SQLite 3.45.1 是 A1 历史环境信息，A2 必须记录本次实际值。GX10/Python 3.12 的结果不能替代 Linux/Python 3.11 的必验结果。

## 2. 固定源码，构建与安装

将 `LEDGER_SOURCE_SHA` 替换为本次交付的完整源码 SHA，并与 PR、A2 验证记录核对。不要使用设计提交或 CLOSED 记账提交作为实现来源，也不要仅用随时间变化的 `main` 作证据。下例使用 GX10 的 `python3`；单独验证 3.11 时将 `LEDGER_PYTHON` 设置为该机实际的 `python3.11`，并为其选择不同的 build/runtime 环境目录。

```sh
set -euo pipefail
LEDGER_BASE="$HOME/xxj/works"
LEDGER_REPO="$LEDGER_BASE/projects/infra-artifact-ledger"
LEDGER_SOURCE_SHA='<本次交付的完整40位小写SHA>'
LEDGER_PYTHON=python3
LEDGER_A2_SRC="$LEDGER_BASE/projects/infra-artifact-ledger-a2-$LEDGER_SOURCE_SHA"
LEDGER_A2_BUILD_VENV="$LEDGER_BASE/venvs/infra_artifact_ledger_a2_build_env"
LEDGER_A2_RUNTIME_VENV="$LEDGER_BASE/venvs/infra_artifact_ledger_a2_runtime_env"
LEDGER_LOCAL_ACCEPTANCE_PARENT="$LEDGER_BASE/data/infra-artifact-ledger/acceptance"

test -d "$LEDGER_LOCAL_ACCEPTANCE_PARENT"
test ! -e "$LEDGER_A2_SRC"
test ! -e "$LEDGER_A2_BUILD_VENV"
test ! -e "$LEDGER_A2_RUNTIME_VENV"
git -C "$LEDGER_REPO" fetch origin
git -C "$LEDGER_REPO" cat-file -e "$LEDGER_SOURCE_SHA^{commit}"
git -C "$LEDGER_REPO" worktree add --detach "$LEDGER_A2_SRC" "$LEDGER_SOURCE_SHA"
test "$(git -C "$LEDGER_A2_SRC" rev-parse HEAD)" = "$LEDGER_SOURCE_SHA"
test -z "$(git -C "$LEDGER_A2_SRC" status --porcelain)"
LEDGER_A2_RUN=$(mktemp -d "$LEDGER_LOCAL_ACCEPTANCE_PARENT/a2-build-XXXXXXXX")
mkdir "$LEDGER_A2_RUN/wheels"

"$LEDGER_PYTHON" -m venv "$LEDGER_A2_BUILD_VENV"
"$LEDGER_PYTHON" -m venv "$LEDGER_A2_RUNTIME_VENV"
"$LEDGER_A2_BUILD_VENV/bin/python" -m pip install \
  'setuptools==84.0.0' 'wheel==0.48.0' 'packaging==26.3'
"$LEDGER_A2_BUILD_VENV/bin/python" -m pip wheel --no-build-isolation --no-deps \
  --wheel-dir "$LEDGER_A2_RUN/wheels" "$LEDGER_A2_SRC" \
  > "$LEDGER_A2_RUN/build.log" 2>&1
LEDGER_A2_WHEEL="$LEDGER_A2_RUN/wheels/infra_artifact_ledger-0.2.0a1-py3-none-any.whl"
sha256sum "$LEDGER_A2_WHEEL" > "$LEDGER_A2_RUN/wheel.sha256"
"$LEDGER_A2_RUNTIME_VENV/bin/python" -m pip install --no-index --no-deps "$LEDGER_A2_WHEEL" \
  > "$LEDGER_A2_RUN/install.log" 2>&1
"$LEDGER_A2_RUNTIME_VENV/bin/python" -I -c \
  'import importlib.metadata, infra_artifact_ledger, sqlite3, sys; print(importlib.metadata.version("infra-artifact-ledger")); print(infra_artifact_ledger.__file__); print(sys.version); print(sqlite3.sqlite_version)' \
  > "$LEDGER_A2_RUN/runtime.txt"
git -C "$LEDGER_A2_SRC" rev-parse HEAD > "$LEDGER_A2_RUN/source-commit.txt"
```

如果同名环境已存在，先选择新的环境名称，不在本流程中删除或重用未知环境。准备构建工具需要已配置的包索引或预先准备的同版本 wheel；安装产品本身使用已构建的本地 wheel，不执行 `pip install infra-artifact-ledger` 猜测来源。

runtime 输出应显示 `0.2.0a1`，模块来自该 runtime venv 的 `site-packages`。不要把 `PYTHONPATH=src` 的源码运行写成已安装软件验证。

`--source-commit` 是调用者声明的来源值；脚本核对其格式，但不据此证明 wheel 来自该 Git 提交。保留固定 checkout 的完整 SHA、干净工作树检查、构建日志、wheel 摘要、安装日志和实际模块位置，形成可复核的构建链。NAS 工具使用同一个固定文件描述符计算 wheel 摘要并比较已安装包字节；这证明本次比较的字节匹配，不等于 Git 来源证明。安装态 smoke 工具另行明确其不核对 wheel 字节，不能省略上述构建记录。

## 3. 源码回归、资源和安装态验证

以下命令沿用上一节设置的变量。各项失败保留日志并处理，不能把前一次运行结果写给新 SHA。

```sh
cd "$LEDGER_A2_SRC"
PYTHONPATH=src "$LEDGER_A2_BUILD_VENV/bin/python" -m unittest discover -s tests -v \
  > "$LEDGER_A2_RUN/unit.log" 2>&1
"$LEDGER_A2_BUILD_VENV/bin/python" -m compileall -q src tests tools/acceptance \
  > "$LEDGER_A2_RUN/compile.log" 2>&1
PYTHONPATH=src "$LEDGER_A2_BUILD_VENV/bin/python" tests/test_resources.py -v \
  > "$LEDGER_A2_RUN/a1-resources.log" 2>&1
PYTHONPATH=src "$LEDGER_A2_BUILD_VENV/bin/python" tests/test_response_boundaries.py -v \
  > "$LEDGER_A2_RUN/a1-response-boundaries.log" 2>&1
A2_RESOURCE_TESTS=1 PYTHONPATH=src "$LEDGER_A2_BUILD_VENV/bin/python" \
  -m unittest discover -s tests -p test_snapshot_resources.py -v \
  > "$LEDGER_A2_RUN/a2-resources.log" 2>&1
A2_RESOURCE_TESTS=1 PYTHONPATH=src "$LEDGER_A2_BUILD_VENV/bin/python" \
  -m unittest discover -s tests -p test_snapshot_semantic_resources.py -v \
  > "$LEDGER_A2_RUN/a2-semantic-resources.log" 2>&1
"$LEDGER_A2_RUNTIME_VENV/bin/python" -I "$LEDGER_A2_SRC/tests/installed_walkthrough.py" \
  --repo "$LEDGER_A2_SRC" > "$LEDGER_A2_RUN/a1-installed.log" 2>&1
"$LEDGER_A2_RUNTIME_VENV/bin/python" -I "$LEDGER_A2_SRC/tests/installed_snapshot_walkthrough.py" \
  --scratch-parent "$LEDGER_LOCAL_ACCEPTANCE_PARENT" \
  --source-commit "$LEDGER_SOURCE_SHA" > "$LEDGER_A2_RUN/a2-installed-local.log" 2>&1
```

资源专项会真实构造大文件/大数据并消耗明显高于序列化大小的内存和磁盘，需在日志中保留实际耗时/RSS；1 GiB 文件上限不是内存上限。普通 discovery 中被跳过的资源项不算已通过。小常量故障测试与实际上限测试各自留证，不能相互替代。

`test_snapshot_semantic_resources.py` 的四项专跑分别覆盖合法 Ledger 的 metadata 16 MiB、metadata 50,000 行、refs 250,000 行和 refs 32 MiB，共十二个 −1／等于／+1 场景。构造方式、完整恢复比对和总 TEXT 64 MiB 的可达性说明见 [A2_RESOURCE_BOUNDARIES](A2_RESOURCE_BOUNDARIES.md)。这组测试的文件系统类型分类使用明确的开发态模拟，实际执行数据库与文件操作；通过结果属于 `LOGIC_ONLY`，不能据此承认 Cloud overlay 的持久性或 GX10/NAS 的实机支持。未开启 `A2_RESOURCE_TESTS=1` 时四项跳过，须保留上述专门日志。

安装态 A2 脚本只在显式提供的本地父目录下生成合成库、两个新快照及新恢复目录；分别调用 API 与已安装 CLI，核对 Blob、已有操作重放和源库不变。未传 NAS 配置时，`nas_publish_flow=NOT_RUN`，只证明本地接入流程。本脚本无论是否提供 NAS 配置，都不能代替下一节的 NAS 丢失恢复演练。

## 4. 采集 NAS 配置

在部署侧确认用于合成验收的已挂载共享目录，收集实际挂载源、挂载点、root、文件系统类型和 archive_root。配置示例与字段见 [A2_USAGE §5](A2_USAGE.md#5-nas-保存与读回)。只把配置保存在本机部署目录，不提交实际内网端点或凭据到公开仓库。

```sh
LEDGER_NAS_ARCHIVE_ROOT='/替换为NAS上已存在的合成验收归档根'
LEDGER_STORAGE_CONFIG='/替换为本机私有storage-config.json绝对路径'
findmnt -T "$LEDGER_NAS_ARCHIVE_ROOT" -o TARGET,SOURCE,FSTYPE,FSROOT
```

目录存在不代表仍挂载到 NAS；挂载源和 root 必须由部署者确认，不用猜测值，也不把完整根 bind 的所有情况宣称可自动识别。首版候选类型为 nfs/nfs4/cifs，但名称符合不等于能力通过。需要独占 mkdir、O_EXCL、同文件系统硬链接拒覆盖、文件/目录 fsync、关闭重开和回读核对。

能力不足时保留失败证据，将该目标标记 `BLOCKED`。不能改用覆盖 rename、略过目录同步、用本地目录代替 NAS，或将跳过的实机项标记为 PASS。设备断网、全局卸载或关闭 NAS 会影响其他业务，不属于下述脚本动作；挂载丢失场景应使用隔离测试挂载或明确的故障注入并另行留证。

## 5. GX10 → NAS → GX10 合成恢复

前提是 S1–S4 已有相应证据、wheel 已安装且构建链已留存、已选择专属合成范围，且真实 NAS 配置已核实。工具先只读核对本地与 NAS 端点，再构造并关闭本轮合成库和本地快照，冻结精确归属清单；此后才创建 NAS 专属子树、执行当前挂载能力预检和发布。能力预检包含两个独立进程争用同一目录时只有一个创建者，失败不继续采用目标。挂载丢失/断网另列 `NOT_RUN`，不能由此竞争测试代替。

```sh
"$LEDGER_A2_RUNTIME_VENV/bin/python" -I "$LEDGER_A2_SRC/tools/acceptance/a2_nas_exercise.py" \
  --local-parent "$LEDGER_LOCAL_ACCEPTANCE_PARENT" \
  --storage-config "$LEDGER_STORAGE_CONFIG" \
  --source-commit "$LEDGER_SOURCE_SHA" \
  --wheel "$LEDGER_A2_WHEEL" > "$LEDGER_A2_RUN/a2-nas-console.log" 2>&1
```

脚本检查隔离解释器、安装包位于 venv 且不来自 checkout、安装包字节与指定 wheel 相符。本地创建全新的 `a2-nas-<32位ID>` 运行目录，NAS 也在指定 archive_root 下创建本次专属新子树，不复用既有 generation。

合成数据包含版本分支、多父合并、内容复用、Manifest、来源关系、已有导入及幂等记录。流程为构造并关闭合成库与快照 → 冻结明确生成路径的归属清单 → NAS 能力预检与发布 → 独立进程从 NAS 校验 → 删除经清单复核的本次可丢弃本地源库/导入源/快照/暂存 → 独立进程只从 NAS 恢复 → `check_restore` → 全部 SQL 行与 Blob 摘要对账 → 原请求重放 → 在原制品追加新版本及 payload。追加步骤核对父版本引用、新 payload 字节和精确计数增量；报告分别保留首次恢复核对结果与追加后的 summary。NAS 核验结果须匹配独立保存的 snapshot ID、manifest 摘要、数据库摘要与 summary，才进入本地删除步骤。预期记录只保留比较材料与摘要，不保留可用于偷偷恢复的数据副本。

删除动作只针对脚本本轮自行创建、已冻结清单的 disposable 子树。清单记录明确生成的相对路径、inode、挂载身份及文件大小、时间、link count 和分块 SHA-256；删除前检查整棵树精确匹配。后来新增的普通文件、同名替换或原文件改写都会阻止删除，不因其位于本轮目录内就自动视为可丢弃；符号链接、额外硬链接和挂载边界同样拒绝。操作者须持续独占管理本轮目录，这不是对任意并发管理员改动的原子删除保证。

脚本不删除已有项目、业务库、NAS 归档或其他验收目录，也不挂载、卸载或改 NAS 服务。失败现场与原归档保留，避免覆盖掩盖问题。

本地运行目录保存 `report.json`、`expected.json`、`snapshot-reference.json`、`disposable-ownership.json` 等证据；其中可能包含部署路径/端点，只在部署侧保管。控制台返回有界状态与 run_id；若在创建运行目录前失败，run_id 可以为空，此时先检查原始调用条件，不猜测证据路径。

失败输出中的 `evidence_saved=false` 表示本轮未确认报告已可靠记录，可能是尚未建立运行目录，也可能是报告写入或同步失败；它不表示数据操作没有发生，也不表示 `report.json` 必定不存在。报告保存失败不会替换此前的操作错误。保留控制台日志、原 run_id、独立摘要和现有目录，按实际停止阶段核对；不得因为缺少完整报告而覆盖重试或清理已发布对象。

`scope=synthetic_nas_roundtrip` 的 PASS 只证明这次真实配置下的合成 NAS 恢复链路。它不自动覆盖 T01–T15 的全部反例，不代表 NAS 服务端断电、硬件故障、全部挂载丢失模型或所有 nfs/cifs 系统栈已通过。仍需按验收矩阵核对剩余项；未跑项目保留 `NOT_RUN`。

## 6. 汇总、失败与移交

每个机器/解释器组合分别记录完整源 SHA、wheel SHA-256、Python/SQLite/内核/架构、真实文件系统与挂载 profile、执行命令、退出码、日志、资源峰值以及失败/跳过项。公开 PR 只放经过核对的摘要与非敏感证据，不上传真实配置或个人数据。

| 检查结果 | 可以形成的结论 |
|---|---|
| 源码单元与故障测试通过 | 对应模型下的格式/预算/错误/事务边界通过；不等于文件系统实机支持 |
| Linux/Python 3.11 安装验证通过 | 该固定 wheel 在该环境可独立使用 |
| GX10 本地流程通过 | 该 GX10 环境下的本地闭环通过；不能代替真实 NAS |
| 真实 NAS 合成恢复通过 | 该挂载配置下、本次合成保存与丢失恢复通过 |
| 全部 S1–S5 证据齐全 | 才可汇总 A2 的完整验收状态；不能只凭一个 PASS 标记全部完成 |

发布结果为 `unknown` 或输出完全丢失时，按 [失败处理](A2_USAGE.md#6-失败后怎么做) 保留原身份、预期摘要和路径，进行 verify/check_restore；不因失败退出而自动同 ID 重试、覆盖或删除。软件回退也不删除归档或恢复目录。

通过验收后，是否启用真实业务、清理中断暂存、制定归档轮换、纳入 Local Hand 或建立知识库级备份，仍按各自范围单独安排。本手册不自动执行这些动作。
