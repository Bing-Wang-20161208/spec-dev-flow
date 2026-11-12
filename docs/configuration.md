# 配置指南

## 环境

需要 Python 3.11、[uv](https://docs.astral.sh/uv/)、Git 和已登录的 Codex CLI。项目锁定
Spec Kit 1.3.3，实际版本由 `requirements.lock` 决定。

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.lock
cp .specify/flow.example.yml .specify/flow.local.yml
cp .specify/publish.example.json .specify/publish.local.json
chmod +x .githooks/pre-commit .githooks/pre-push
git config core.hooksPath .githooks
```

需要消息通知时，再运行：

```sh
.venv/bin/python scripts/notify.py init
```

## 私有文件

| 文件 | 用途 |
| --- | --- |
| `.specify/flow.local.yml` | Codex provider 和代码仓库路径 |
| `.specify/publish.local.json` | 发布时拦截的本机关键词 |
| `.specify/notifications.local.json` | 可选飞书机器人配置 |

这些文件不进入版本控制。凭据、Webhook 和签名密钥不得放入命令参数、模型提示、日志或证据。

## 仓库映射

编辑 `.specify/flow.local.yml`：

```yaml
provider: null
repositories:
  app: /absolute/path/to/your-project
  admin: /absolute/path/to/your-admin-project
```

`repositories` 的键是任务使用的别名，值是独立 Git 仓库的绝对路径。一个流程中心可以绑定多个
仓库，但每个任务的仓库和文件范围仍须明确。

目标仓库的编码规范、依赖版本、启动方式和验证命令以该仓库就近的 `AGENTS.md` 与项目文档为准。

## 模型 provider

`provider: null` 表示使用当前 Codex 默认 provider。需要指定时，只能填写本机已配置的名称，
不要在此文件保存模型 ID 或凭据。

派发前读取当前模型目录：

```sh
.venv/bin/python scripts/flow.py models
```

模型目录在执行时获取，不在计划、模板或 README 中固化。后续无法确认实际下游模型或用量时，
应将相关字段记录为“未知”。

## 发布拦截

编辑 `.specify/publish.local.json`：

```json
{
  "blocked_terms": []
}
```

可加入组织名称、仓库标识和其他不应出现在公共框架文件中的词。该检查只辅助发现风险，最终
仍需人工或 AI 审查文件正文、提交说明和 Git 历史。

## 通知配置

`notify.py init` 会生成本地配置。文件权限应为 `600`；不配置通知也可以使用完整开发流程。
通知的发送行为和命令见[运维与恢复](operations.md)。
