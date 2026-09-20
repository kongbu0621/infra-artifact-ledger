# GX10 A1 实机执行证据

本目录保存 Owner 上传日志的公开脱敏副本，用于复核固定版本在 GX10 上的执行结果。它不包含机器配置、凭据、业务数据或可部署制品；不承担新的合同或授权职责。

## 来源与时间

- 固定源码：`a00926dff89ae5ddc498105472899a48381b1927`，tree `56dca8bbe9dca627e05b89d8bb7eea109846bf0b`。
- 安装验证开始：`2026-09-20T19:10:44Z`；资源验证开始：`2026-09-20T19:13:01Z`。北京时间分别为 2026-09-21 03:10:44、03:13:01；目录以 UTC 日期命名。
- 原始归档：`gx10-a1-evidence-20260921-031516.tar.gz`，7,144 字节，SHA-256 `0084f137b6ec6c4d2151e109fd5305b0ede309bbc5594b4df73f286353052422`。Owner 上传至维护会话保存；未将含私人路径的原始归档复制到公开仓库。
- 获取方式：Owner 在 GX10 本机运行会话提供的两段命令并上传日志。维护工作台检查原始字节、解析结果和固定源码中的用例；未把云端测试冒充 GX10 执行，也没有通过 Local Hand 执行此次安装。

## 文件与完整性

| 公开文件 | 来源与作用 |
|---|---|
| [installation.redacted.txt](installation.redacted.txt) | 原始 `gx10-0GFHzV/run.log`；系统与 ext4 信息、普通 suite、wheel 构建安装、25 次 CLI 和 Python 示例、最终退出码 |
| [resources.redacted.txt](resources.redacted.txt) | 原始 `resources-PgtBdF/run.log`；8 项容量测试、1 项响应节点测试、时间与内存峰值、最终退出码 |
| [manifest.json](manifest.json) | 原始归档及每个日志的摘要，公开副本摘要、替换项次数，以及已核对的结构化结果 |

公开副本仅对 UTF-8 文本作以下字面替换：操作者绝对工作根目录改为 `/example/works`，pip 缓存根目录改为 `/example/cache/pip`，相对工作根目录改为 `./works`，主机名改为 `gx10-node`。替换次数逐文件记录在 manifest。占位路径不是实际部署命令或产品固定路径；软件不依赖这些示例目录。

不删除行，不修改测试名称、状态、退出码、版本、时间、计数、制品摘要或资源测量。公开副本与原始日志不是逐字节相同的文件，使用各自的摘要核对；摘要提供字节识别，不认证执行者、主机身份或日志真实性。原始日志不在公开仓库，外部读者可校验公开副本，并在相同软件基线上自行复现。

## 已执行命令与结果

下表摘录会话实际执行步骤，变量代表 Owner 本机路径，不作为整段可直接运行的安装器。安装指南见 [USAGE](../../USAGE.md)。`LEDGER_BUILD` 与 `LEDGER_ENV` 分别指向该项目独立的构建和运行 venv；每台机器分别创建，不共享其他项目环境。`LEDGER_SRC` 是固定源码 checkout，`LEDGER_RUN` 是本次新建验收目录。

两阶段使用 `set -euo pipefail`；测试 `TMPDIR` 显式指向本项目验收目录。日志记录该数据目录所在挂载点为 ext4。安装阶段固定完整 SHA checkout 并核对 HEAD；资源阶段再次核对 HEAD 与跟踪文件无差异。

| 步骤 | 命令核心 | 日志结果 |
|---|---|---|
| 构建环境 | 构建 venv 执行 `python -I -m pip install pip==24.0 setuptools==84.0.0 wheel==0.48.0 packaging==26.3` | 对应四版本已安装或已满足 |
| 编译 | 构建 venv 执行 `python -m compileall -q src tests` | 静默；依据已发遇错即停脚本继续到最终 exit 0，未单独打印编译阶段状态 |
| 普通 suite | `PYTHONPATH="$LEDGER_SRC/src" "$LEDGER_BUILD/bin/python" -m unittest discover -s tests -v` | 166 discovered，157 passed，9 skipped；44.216s |
| 构建 wheel | 构建 venv 执行 `python -I -m pip wheel --no-deps --no-build-isolation --wheel-dir "$LEDGER_RUN/wheels" .` | 成功；36,448 字节 |
| 消费者安装 | 运行 venv 执行 `python -I -m pip install --no-index --no-deps "$LEDGER_RUN/wheels/infra_artifact_ledger-0.1.0a1-py3-none-any.whl"` | 安装 `0.1.0a1`，模块路径属于运行 venv 的 site-packages |
| 安装态使用 | `"$LEDGER_ENV/bin/python" -I tests/installed_walkthrough.py --repo "$LEDGER_SRC"` | `result=PASS`、`library_guide=PASS`，25 次 CLI，40 字节内容一致 |
| 容量专项 | 构建 venv、`PYTHONPATH="$LEDGER_SRC/src"` 执行 `python tests/test_resources.py -v` | 8 passed；52.350s |
| 响应节点专项 | 同一构建 venv 与源码路径执行 `python tests/test_response_boundaries.py -v` | 1 passed；18.628s |

普通日志逐项对应固定源码 166 个 unittest 方法；资源阶段 9 个方法恰好补齐 discovery 的 9 个 skip。合计 166 个不同用例全部执行通过。25 次 CLI 中的 `conflict.json` 返回 exit 4 是明确的预期幂等冲突，其余 24 次 exit 0；Python 示例额外通过，不并入 unittest 数量。

wheel 日志 SHA-256 为 `904e413fcc11d6fb0d3b6044c37fe8d1e0f34c1c5a163cd2302bfcab6255ede0`，pip 与 `sha256sum` 输出一致。归档没有 wheel 文件；本次复核没有独立核对该 wheel 二进制或已安装源文件与 Git blob 的逐字节对应，也不声明跨环境构建 ZIP 摘要相同。

## 结论边界

通过组合为 Linux 6.17.0-1031-nvidia / aarch64 / glibc 2.39、Python 3.12.3、SQLite 3.45.1、本机 ext4；仍保留 Linux/Python 3.11 必验基线。日志打印源码前缀 `a00926d`，完整 SHA 来自会话命令与 Git 对应关系；两份日志不包含完整执行脚本或独立源码签名。

资源 suite 的 `process_peak_rss_kib` 最大为 1,551,072；响应专项最大为 199,064。前者包含测试进程运行至该时刻的累计峰值，不能把每行数值当作该 case 单独占用；两者都不代表整机峰值或生产内存上限。

本证据只确认上述 A1 软件安装、功能和资源用例。未执行真实断电、NAS 快照恢复、真实消费者采用或独立第二 backend 验证；未接纳 ledger 到 Local Hand，未创建常驻服务。测试成功与证据归档不改变 Gate、软件 Alpha 状态或 A2–A4 授权范围。
