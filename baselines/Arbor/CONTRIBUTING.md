# Contributing to Arbor

Thanks for your interest in improving Arbor! This guide covers how to set up a dev
environment, the conventions we follow, and how to get a change merged.

*[中文版见下方](#中文)*

---

## Ways to contribute

- **Share an idea or ask a question** — start a [Discussion](https://github.com/RUC-NLPIR/Arbor/discussions). Open-ended ideas, feature wishes (upvotable), usage Q&A, and "look what I built" all belong here.
- **Report a bug** — open a [Bug report](https://github.com/RUC-NLPIR/Arbor/issues/new?template=bug_report.yml).
- **Request a feature** — once an idea is concrete and actionable, open a [Feature request](https://github.com/RUC-NLPIR/Arbor/issues/new?template=feature_request.yml).
- **Send a pull request** — see below.

For anything larger than a small fix, please open an issue first so we can agree on
the approach before you invest time.

## Development setup

**Requirements:** Python ≥ 3.10 and Git.

```bash
git clone https://github.com/RUC-NLPIR/Arbor.git
cd Arbor
python -m venv .venv && source .venv/bin/activate
pip install -e .          # or: uv pip install -e .
arbor doctor              # verify PATH, git, and API keys
```

## Running tests

Arbor's tests are self-contained — they map the `arbor` package onto `src/` and need
no install step. Run a test file directly, or collect everything with `pytest` if you
have it installed:

```bash
python tests/test_executor_resume.py     # run one file directly
pytest tests/                            # or collect all (pip install pytest first)
```

Please run the tests relevant to your change and confirm they pass before opening a PR.

## Branch & commit conventions

- **Branch off `main`** with a descriptive name: `feat/...`, `fix/...`, `docs/...`.
- **Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):**
  `type(scope): summary`, where `type` is one of `feat`, `fix`, `docs`, `refactor`,
  `test`, `chore`. Examples:
  - `fix(config): accept llm.timeout as alias for llm_timeout`
  - `feat(executor): add needs_retry status and resume/retry support`
- Keep each commit focused; prefer several small commits over one large mixed one.

## Pull request process

1. Keep PRs small and focused on a single concern.
2. Link the issue it resolves (`Closes #123`).
3. Fill in the PR template, including how you tested the change.
4. If behavior or configuration changed, update the docs and **both** READMEs
   (`README.md` and `README.zh-CN.md`).
5. Never commit secrets, API keys, or tokens.

## Code style

Match the style of the surrounding code — naming, comment density, and idioms. Arbor
values readable, self-documenting code over cleverness.

## License

By contributing, you agree that your contributions are licensed under the project's
[Apache-2.0 License](LICENSE).

---

## 中文

感谢你有兴趣改进 Arbor！本指南介绍如何搭建开发环境、我们遵循的约定，以及如何让你的改动被合并。

### 贡献方式

- **分享想法或提问** —— 来[讨论区（Discussions）](https://github.com/RUC-NLPIR/Arbor/discussions)发帖。开放式想法、功能许愿（可点赞投票）、使用问答、晒成果都欢迎放这里。
- **报告缺陷** —— 提交 [Bug report](https://github.com/RUC-NLPIR/Arbor/issues/new?template=bug_report.yml)。
- **请求功能** —— 当想法已足够具体、可执行时，提交 [Feature request](https://github.com/RUC-NLPIR/Arbor/issues/new?template=feature_request.yml)。
- **提交 PR** —— 见下文。

对于比小修复更大的改动，请先开一个 issue 讨论方案，再动手实现，以免白做。

### 开发环境

**环境要求：** Python ≥ 3.10 与 Git。

```bash
git clone https://github.com/RUC-NLPIR/Arbor.git
cd Arbor
python -m venv .venv && source .venv/bin/activate
pip install -e .          # 或：uv pip install -e .
arbor doctor              # 检查 PATH、git 与 API 密钥
```

### 运行测试

Arbor 的测试是自包含的——它们会把 `arbor` 包映射到 `src/`，无需安装步骤。可以直接运行单个测试文件，或在装有 `pytest` 时一次性收集：

```bash
python tests/test_executor_resume.py     # 直接运行单个文件
pytest tests/                            # 或收集全部（需先 pip install pytest）
```

提交 PR 前，请运行与你改动相关的测试并确认通过。

### 分支与提交约定

- **从 `main` 切分支**，使用有意义的名字：`feat/...`、`fix/...`、`docs/...`。
- **提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)：**
  `type(scope): summary`，其中 `type` 取 `feat`、`fix`、`docs`、`refactor`、`test`、`chore` 之一。例如：
  - `fix(config): accept llm.timeout as alias for llm_timeout`
  - `feat(executor): add needs_retry status and resume/retry support`
- 保持每个 commit 聚焦；宁可多个小 commit，也不要一个混杂的大 commit。

### Pull Request 流程

1. PR 保持小而聚焦，一次只解决一件事。
2. 关联其解决的 issue（`Closes #123`）。
3. 填写 PR 模板，包括你如何验证改动。
4. 若行为或配置有变更，请同步更新文档与**两个** README（`README.md` 和 `README.zh-CN.md`）。
5. 切勿提交任何密钥、API key 或 token。

### 代码风格

与周围代码保持一致——命名、注释密度与惯用写法。Arbor 更看重可读、自解释的代码，而非炫技。

### 许可证

提交贡献即表示你同意你的贡献以本项目的 [Apache-2.0 许可证](LICENSE) 授权。
