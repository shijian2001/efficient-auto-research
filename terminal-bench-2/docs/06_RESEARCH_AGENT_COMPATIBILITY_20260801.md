# MLEvolve 与 Efficient Agent Research 的 Terminal-Bench 兼容性

测试时间：2026-08-01（Asia/Shanghai）

## 验证环境

- Harbor：`0.20.0`
- Terminal-Bench 2 数据集：本地 89 个任务
- 目标模型：`gpt-5.5`
- OpenAI-compatible relay 已单独验证可用

## Harbor 要求

自定义 Agent 必须提供一个继承 Harbor `BaseAgent` 的类，并实现：

- `name()`
- `version()`
- `setup(environment)`
- `run(instruction, environment, context)`

## 结构与加载测试

- 扫描 MLEvolve 源码：`BaseAgent` 实现数量为 0。
- 扫描 Efficient Agent Research 源码：`BaseAgent` 实现数量为 0。
- Harbor 工厂加载 `agent.run:main`：失败，因为导入对象是函数，不是 Agent 类。
- Harbor 工厂加载 MLEvolve `run:run`：失败；它不是 Harbor Agent 类，且 Harbor Python 环境不包含 MLEvolve 的完整依赖。
- `terminal-bench-2/agent_adapters` 当前只有接口说明，没有生产 Adapter。

原始探测结果：`terminal-bench-2/results/research_agents_compatibility_probe_20260801.txt`。

## 结论

MLEvolve 和 Efficient Agent Research 当前都不能直接运行 Terminal-Bench 任务。问题不是 relay 或 `gpt-5.5`，而是 Agent 接口和执行模型不同：

- 两者原生输入是 Kaggle 数据目录与 competition description。
- 两者原生输出是 `submission.csv` 和本地验证指标。
- Terminal-Bench 要求 Agent 接收自然语言任务，并通过 Harbor environment 在隔离容器中持续执行 shell、读取结果和修改文件。

因此仅写一个导入包装器不够。要公平地称为 MLEvolve/Efficient Agent Research 的 Terminal-Bench 版本，需要新增专用 Harbor Adapter，并为核心搜索循环增加通用终端工具调用、任务状态观察和 verifier 反馈机制。在完成该适配前，不应把 Harbor 内置 Agent 或简单 LLM shell loop 的结果标记成这两个研究 Agent 的成绩。

