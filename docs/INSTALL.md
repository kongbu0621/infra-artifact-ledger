# 安装与首次使用

本文面向首次使用 `infra-artifact-ledger` 的应用开发者。当前软件为 `0.2.0a1` alpha，尚未发布 PyPI 包；使用公开仓库的固定源码构建 wheel，再安装到项目独立虚拟环境。运行只依赖 Python 标准库，不需要模型账号、Local Hand 或维护者的部署配置。

## 1. 准备源码与目录

以下步骤使用 Linux、Bash、Git 和 Python 3.11。Python 需要提供 `venv`、`pip` 和 `sqlite3`；其他已验证组合见[兼容表](COMPATIBILITY.md)。准备好公开仓库的 Git checkout：

```bash
git clone https://github.com/kongbu0621/infra-artifact-ledger.git /path/to/projects/infra-artifact-ledger
```

从兼容表及对应验证记录选择包含当前实现的完整 40 位提交 SHA，确认该提交已在本地仓库中。已有 checkout 可以直接使用，不必重新克隆或切换分支。下方脚本将该提交取入独立 Git 对象库后导出构建副本，不套用原 checkout 的本地替换引用或导出属性，不纳入工作区未提交的改动，也不向原 checkout 写构建产物。

预先创建一个可写的安装父目录，将下方 `/path/to/...` 和提交 SHA 替换为自己的值。脚本在父目录中创建全新的安装子目录，将构建环境、运行环境和 wheel 分开；应用数据目录另选本地可靠文件系统，不放进安装目录。

## 2. 构建并安装当前版本

构建工具需从已配置的包索引取得，或通过 pip 的本地 wheel 来源预先准备；版本与本仓构建配置一致。安装生成的产品 wheel 使用 `--no-index --no-deps`，不从公开索引猜测同名产品包。

```bash
set -euo pipefail
LEDGER_REPO='/path/to/projects/infra-artifact-ledger'
LEDGER_SOURCE_SHA='<所选实现的完整40位小写提交SHA>'
LEDGER_INSTALL_PARENT='/path/to/ledger-installs'
LEDGER_PYTHON=python3.11

[[ "$LEDGER_SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]
LEDGER_SOURCE_TYPE=$(git --no-replace-objects -C "$LEDGER_REPO" cat-file -t "$LEDGER_SOURCE_SHA")
test "$LEDGER_SOURCE_TYPE" = commit
test -d "$LEDGER_INSTALL_PARENT"
LEDGER_INSTALL_DIR=$(mktemp -d "$LEDGER_INSTALL_PARENT/ledger-install-XXXXXXXX")
LEDGER_SRC="$LEDGER_INSTALL_DIR/source"
LEDGER_EXPORT_REPO="$LEDGER_INSTALL_DIR/export.git"
LEDGER_BUILD_VENV="$LEDGER_INSTALL_DIR/build"
LEDGER_VENV="$LEDGER_INSTALL_DIR/runtime"
LEDGER_WHEEL_DIR="$LEDGER_INSTALL_DIR/wheels"
mkdir "$LEDGER_SRC" "$LEDGER_WHEEL_DIR"
git init --quiet --bare --template= "$LEDGER_EXPORT_REPO"
GIT_NO_REPLACE_OBJECTS=1 git -C "$LEDGER_EXPORT_REPO" -c protocol.file.allow=always \
  fetch --quiet --no-tags --no-recurse-submodules --depth=1 -- "$LEDGER_REPO" "$LEDGER_SOURCE_SHA"
git --no-replace-objects -C "$LEDGER_EXPORT_REPO" -c core.attributesFile=/dev/null \
  archive --format=tar "$LEDGER_SOURCE_SHA" | tar -xf - -C "$LEDGER_SRC"
printf '%s\n' "$LEDGER_SOURCE_SHA" > "$LEDGER_INSTALL_DIR/source-commit.txt"

"$LEDGER_PYTHON" -m venv "$LEDGER_BUILD_VENV"
"$LEDGER_PYTHON" -m venv "$LEDGER_VENV"
"$LEDGER_BUILD_VENV/bin/python" -m pip install \
  'setuptools==84.0.0' 'wheel==0.48.0' 'packaging==26.3'
"$LEDGER_BUILD_VENV/bin/python" -m pip wheel --no-build-isolation --no-deps \
  --wheel-dir "$LEDGER_WHEEL_DIR" "$LEDGER_SRC"
LEDGER_WHEEL="$LEDGER_WHEEL_DIR/infra_artifact_ledger-0.2.0a1-py3-none-any.whl"
sha256sum -- "$LEDGER_WHEEL" > "$LEDGER_INSTALL_DIR/wheel.sha256"
"$LEDGER_VENV/bin/python" -m pip install --no-index --no-deps "$LEDGER_WHEEL"
"$LEDGER_VENV/bin/python" -I -c \
  'import importlib.metadata, infra_artifact_ledger; print(importlib.metadata.version("infra-artifact-ledger")); print(infra_artifact_ledger.__file__)'
"$LEDGER_VENV/bin/artifact-ledger" --help
```

版本输出应为 `0.2.0a1`，模块路径应位于新 runtime 的 `site-packages`。保留源码 SHA、wheel 及摘要，并记下 `LEDGER_VENV` 的绝对路径；这些用于确认实际安装来源。同名 alpha 版本号本身不能区分不同源码提交。

命令失败时，先查看具体错误和已生成文件，修正后从合适步骤继续；已安装成功的环境可以继续使用。脚本的全新目录策略不会覆盖旧安装，也不执行、迁移或删除用户数据库。

## 3. 运行第一个示例

继续[普通报告示例](REUSE_EXAMPLE.md#1-安装与准备)，把其中的 `LEDGER_VENV` 设置为上一步 runtime 的绝对路径；使用全新的数据目录，完成报告 v1/v2 保存、内容读回、重试核对和新库导入。Python 接入方式见[库调用示例](USAGE.md#4-python-与跨语言调用约定)。

本指南只完成软件安装；是否可在某个存储目标运行，还须核对[兼容范围](COMPATIBILITY.md)。需要一致快照或 NAS 保存时继续[快照与恢复指南](A2_USAGE.md)，真实 NAS 验收仍未完成。维护者的部署接续和验收命令另见[运行手册](A2_RUNBOOK.md)。
