# 运维与恢复

## 异常中断

中断后先读取任务、证据和原生运行状态，再核对目标仓库的 Git 现场、相关差异、未跟踪文件和
旧执行进程。旧执行者未确认停止时不能接手。

运行结果未知时：

1. 检查实际副作用和已有成果。
2. 使用 `settle` 保存恢复依据与下一步。
3. 验证已有代码后，保持原任务和问题标识新建接续运行。
4. 不直接修改 Spec Kit 私有运行状态。

```sh
.venv/bin/python scripts/flow.py settle specs/<feature> <attempt> \
  --evidence '<需求内证据路径>' --reason '<恢复结论>'
```

历史失败、尝试次数和版本证据跨会话保留。未知结果不等于失败，也不等于通过。

## 单向通知

通知是可选的飞书单向提醒，仅覆盖“需要用户介入”和“整个需求开发完成”。通知失败不改变
业务状态，也不会自动重试已经处于发送中或未知状态的事件。

```sh
.venv/bin/python scripts/notify.py check
.venv/bin/python scripts/notify.py test --dry-run
```

真实测试会发送一条明确标识的消息。Webhook 无法验证实际群名，发送后需要人工确认目标和
机器人。

## 请求用户决定

```sh
.venv/bin/python scripts/flow.py request-decision specs/<feature> review \
  --key '<稳定问题标识>' --summary '<问题摘要>' --next '<所需决定>'
```

- 需求或 UI 澄清使用 `review`。
- 执行计划澄清使用 `plan`。
- 任务中的问题使用任务 ID，例如 `T001`。

命令会先保存待确认事项，再尝试发送提醒。正式评审和计划确认仍须在用户回复后通过
`approve` 保存。群内消息本身不构成批准。

相同需求和版本下的事件会去重。通知失败、未配置或投递结果未知时，业务工作仍在 Codex
会话中继续处理。
