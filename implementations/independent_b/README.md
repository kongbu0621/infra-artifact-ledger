# Artifact Ledger independent B

这是 A4 的独立第二实现，提供 A1 candidate 的 Python 公共入口与 `artifact-ledger` CLI。采用独立的请求验证、指纹、引用关系、导入/重放与事务实现；只使用 Python 标准库及 SQLite，不导入或委托仓库根实现 A。

**当前为实验实现，A4 整体仍为 PARTIAL。** 已验证的互通闭环、边界修复和未覆盖项见仓库[开发记录](../../docs/DEVELOPMENT.md#a4-独立-b-边界复核2026-10-29)。它不提供 A2 snapshot/NAS/recovery，不能替换现有业务安装。Python 最低语法目标为 3.11，本轮现场验证为 Linux aarch64 / Python 3.12；3.11 的运行资格尚未取得。

## 独立安装

A 与 B 提供同名 Python 模块和 CLI，**必须使用不同虚拟环境**。从仓库根目录执行下面命令，在调用者选定的新目录中构建、安装，不复用原业务环境：

```sh
python3 -m venv /tmp/ledger-b-build
/tmp/ledger-b-build/bin/python -m pip install setuptools==84.0.0 wheel==0.48.0
/tmp/ledger-b-build/bin/python -m pip wheel --no-build-isolation --no-deps \
  ./implementations/independent_b --wheel-dir /tmp/ledger-b-wheels
python3 -m venv /tmp/ledger-b-runtime
/tmp/ledger-b-runtime/bin/python -m pip install --no-index --no-deps \
  /tmp/ledger-b-wheels/infra_artifact_ledger_b-0.1.0a1-py3-none-any.whl
```

Python 包根公开 `initialize`、`open`、`LedgerError`；命令、字段、限额和错误语义使用[公共合同](../../docs/INTERFACE_PROFILE.md)。`initialize` 只创建新目标。B 的数据库有独立格式标记、对象表及幂等表，不能直接打开 A 的数据库；两者之间只交换完整 portable package + descriptor。运行时不依赖本 README 或仓库目录。

## 复现本轮检查

安装态检查从仓库外执行，以 `-I` 隔离源码搜索路径；下面的 `TEST_DIR` 指向本目录的绝对 `tests` 路径：

```sh
/tmp/ledger-b-runtime/bin/python -I -m unittest discover -s "$TEST_DIR" -v
python3 "$TEST_DIR/cross_implementation.py" \
  --a-python "$A_RUNTIME/bin/python" --b-python /tmp/ledger-b-runtime/bin/python \
  --workdir /tmp/ledger-cross-new
/tmp/ledger-b-runtime/bin/python -I "$TEST_DIR/resource_probe.py" \
  --workdir /tmp/ledger-b-resource-new
```

A_RUNTIME 必须是另一个只安装 A wheel 的验收环境。互通检查自行创建新合成库，依次验证 A→B、原 create/append/import 请求重放、B 继续写入、B 新进程读回、B→全新 A；包含空 Artifact、分支/多父历史与历史 Receipt，不读取已有业务数据。两个测试 workdir 必须不存在。容量检查会留下约 320 MiB 的合成数据库并报告实测进程峰值，不连接 NAS，也不降低正式资源上限。

单元检查使用公共调用及独立期望值；少量 SQLite/连接代理访问仅用于明确的故障注入。锁冲突、回滚失败、提交确认丢失、CLI 关闭失败及输出管道断开各有独立断言；这些不等于真实设备断电验收。查询成功仅确认历史提交，实际内容须由 `read_blob` / `verify` 另行核验。
