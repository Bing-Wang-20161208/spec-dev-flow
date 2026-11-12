# Spec Dev Flow

一套面向多仓库交付的规格驱动开发工作流。

Spec Dev Flow 在 GitHub Spec Kit 之上补齐版本化确认、任务调度、当前代码验证、独立检查、
异常恢复和可选通知。流程中心负责定义交付边界；代码、测试和仓库规则仍由目标项目掌握。

## 核心原则

- **原生能力优先**：保留 Spec Kit 的需求、计划、任务和运行记录，不另建平行状态系统。
- **单任务单一责任**：每个子任务由一个执行 Agent 承接，并对实现、验证和交付负责。
- **验收基于证据**：完成判定使用当前代码版本的真实测试和检查结果。
- **业务资料留在本地**：真实需求、业务代码、证据和密钥不进入通用框架仓库。

## 快速开始

需要 Python 3.11、`uv`、Git 和可用的 Codex CLI。

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.lock
cp .specify/flow.example.yml .specify/flow.local.yml
cp .specify/publish.example.json .specify/publish.local.json
chmod +x .githooks/pre-commit .githooks/pre-push
git config core.hooksPath .githooks
.venv/bin/python scripts/flow.py --help
```

随后在 `.specify/flow.local.yml` 中配置仓库路径。完整步骤见
[配置指南](docs/configuration.md)。

## 工作流

```mermaid
flowchart LR
    S[需求与评审] --> P[计划与任务]
    P --> E[任务执行]
    E --> V[测试与独立检查]
    V -->|未通过| E
    V -->|全部通过| D[开发完成]
    D -.-> R[上线状态单独记录]
```

每个子任务由一个执行 Agent 承接。进程只记录该任务的交付结果和证据，不限制执行侧内部的
组织方式。任务完成必须依赖当前代码版本，过期结果不会用于通过。

## 文档

| 文档 | 内容 |
| --- | --- |
| [配置指南](docs/configuration.md) | 环境、仓库映射、模型 provider 和私有配置 |
| [工作流](docs/workflow.md) | 确认节点、命令、任务执行和完成判定 |
| [运维与恢复](docs/operations.md) | 中断恢复、运行状态和可选飞书通知 |
| [发布与边界](docs/publishing.md) | 公共仓库允许内容、提交检查和发布流程 |

仓库内的 [AGENTS.md](AGENTS.md) 是当前工作流的操作约定；本文档仅提供入口和概览。

## 仓库边界

本仓库只接受可复用的框架、模板、脚本、合成测试和通用文档。不得提交真实需求、设计资料、
接口定义、业务代码、执行证据、密钥或通知配置，私有仓库也不例外。

提交和推送检查由 `scripts/repo_guard.py` 与 `.githooks/` 执行；路径检查不能替代对正文和
Git 历史的语义审查。

## 开发

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/repo_guard.py --index
```

详见 [发布与边界](docs/publishing.md)。

## 许可证

[MIT](LICENSE)。GitHub Spec Kit 的来源与许可信息见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
