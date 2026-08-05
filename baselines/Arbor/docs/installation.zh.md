# 安装

## 环境要求

- **Python ≥ 3.10**
- **Git**（Arbor 在隔离的 git worktree 中运行每个实验）
- 至少一个 LLM provider 的 API key（Anthropic、OpenAI，或任何通过 LiteLLM 接入的
  OpenAI 兼容端点）

## 安装

```bash
pip install arbor-agent          # 或：uv pip install arbor-agent
```

这一条命令就会把 Arbor 及 `arbor` 命令装进你当前的 Python 环境。我们建议用虚拟环境保持隔离：

=== "venv + pip"

    ```bash
    python -m venv .venv
    source .venv/bin/activate        # Windows：.venv\Scripts\activate
    pip install arbor-agent
    ```

=== "uv"

    ```bash
    uv venv
    source .venv/bin/activate
    uv pip install arbor-agent
    ```

!!! tip "升级"
    用 `pip install -U arbor-agent` 获取最新发布版本。

## 从源码安装（开发用）

若要修改 Arbor 本身的源码，从克隆仓库做可编辑安装：

```bash
git clone https://github.com/RUC-NLPIR/Arbor.git
cd Arbor
pip install -e .          # 或：uv pip install -e .
```

!!! info "为什么用可编辑安装（`-e`）？"
    可编辑安装让你通过 `git pull` 获取更新，而无需重新安装——适合在你修改 Arbor 自身源码时使用。

## 验证

```bash
arbor version
arbor doctor      # 检查 PATH、venv 泄漏、git 与 API key
```

`arbor doctor` 是发现安装问题最快的方式——它会报告你的 shell 解析到哪个 `arbor`、跑在哪个
Python 上、`git` 是否可用，以及用户配置是否存在。

## 可选：用 pipx 安装全局 `arbor` 命令

如果你希望在**任意**目录都能直接用 `arbor` 而无需激活 venv，可用
[pipx](https://pipx.pypa.io) 安装——它会替你管理隔离环境：

```bash
pipx install arbor-agent          # 全局安装
pipx upgrade arbor-agent          # 之后升级
```

## 故障排查

!!! failure "`arbor: command not found`"
    该包被装进了一个未激活、或不在 `PATH` 上的环境。激活正确的虚拟环境，或改用上面的 pipx
    安装。运行 `arbor doctor` 做诊断。

## 下一步

- [快速上手](quickstart.md) —— 配置一个 provider 并启动你的第一次运行。
- [配置](configuration.md) —— 每个选项，附示例。
