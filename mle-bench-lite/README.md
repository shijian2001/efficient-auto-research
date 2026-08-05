# MLE-Bench Lite Local Installation

本目录管理 OpenAI MLE-Bench Lite（Low split，22 个 Kaggle 竞赛）的本地安装。

## 当前结构

- `source/`：链接到本机已有的官方 `openai/mle-bench` 最新源码
- `data/`：链接到 `/mnt/sdc` 上约 254GB 的完整 Lite 数据目录
- `.venv/`：本次新建的官方完整 Python 依赖环境
- `legacy-python-env/`：旧的六题轻量 Conda 环境，仅供参考
- `scripts/`：安装、授权检查、准备和验收脚本
- `docs/`：完整安装记录与运行说明
- `logs/`：每一步的原始命令输出

## 当前数据结论

- 官方 Lite 清单：22 项
- 当前数据目录：22 项
- 当前完整 prepared：22 项
- 必需 benchmark 文件完整：22 项
- 缺失任务：0 项
- 后台下载或准备任务：无

## 安装状态

- MLE-Bench：`1.0.0`
- Python：`3.11.15`
- TensorFlow：`2.21.0`
- Kaggle CLI：`1.6.17`
- 官方源码：与 2026-08-01 的上游 `main` 一致
- 下载代理：`🇺🇸美国4号-0.1倍`
- 数据完成时间：`2026-08-02`

完成安装后运行：

```bash
source scripts/env.sh
mlebench-local --help
./scripts/verify_installation.sh
./scripts/show_status.sh
```
