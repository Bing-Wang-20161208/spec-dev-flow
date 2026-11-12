# 工作流

## 生命周期

1. 在 `specs/<feature>/spec.md` 记录需求来源、范围、UI、验收标准和用户决定。
2. 完成需求/UI 评审，并通过 `approve` 保存对应当前版本的确认。
3. 生成 `plan.md` 和 `tasks.md`，明确仓库、范围、依赖、API/mock 策略和验证命令。
4. 完成执行计划确认。
5. 为每个子任务登记一个责任 Agent。
6. 收集当前代码的测试、独立检查和总体验证证据。
7. 通过 `complete` 完成任务或父需求；上线状态单独维护。

真实接口是否可用及接口地址只接受用户明确提供的信息，不从代码或环境配置推断为已就绪。

## 常用命令

```sh
# 检查需求文件
.venv/bin/python scripts/flow.py check specs/<feature>

# 保存正式确认
.venv/bin/python scripts/flow.py approve specs/<feature> review \
  --quote '<用户原话>' --source '<确认来源>'
.venv/bin/python scripts/flow.py approve specs/<feature> plan \
  --quote '<用户原话>' --source '<确认来源>'

# 执行单个任务的实现、测试或独立检查
.venv/bin/python scripts/flow.py run specs/<feature> T001 --phase implement \
  --model '<当前候选>' --why '<选择理由>'
.venv/bin/python scripts/flow.py run specs/<feature> T001 --phase test \
  --command '<已确认的验证命令>'
.venv/bin/python scripts/flow.py run specs/<feature> T001 --phase review \
  --model '<当前候选>' --why '<选择理由>'

# 完成单个任务或父需求
.venv/bin/python scripts/flow.py complete specs/<feature> --task T001
.venv/bin/python scripts/flow.py complete specs/<feature>
```

命令中的 `<...>` 都是占位符，不能原样执行。实际参数由当前任务和仓库规则决定。

## 任务与执行

每个子任务仅登记一个责任 Agent。流程中心只接收该 Agent 的最终交付、测试结果和证据，
并以这些内容判断子任务是否完成。

执行前会保存代码与需求版本指纹。执行后检查：

- 是否有超出任务范围的修改。
- 需求中心文件是否被任务执行者改写。
- Git HEAD 是否保持稳定。
- 原生进程是否已经退出。
- 测试或检查是否对应当前代码版本。

## 完成判定

任务完成需要满足：

- 依赖任务已完成。
- 存在当前代码版本的真实测试通过证据。
- 需要独立检查时，检查已通过且对应当前版本。
- 需要联调或 mock 清理时，相关任务已完成。
- 父需求完成前，总体验证覆盖当前全部任务代码。

`complete` 只表示开发流程完成，不表示已经上线。

## 版本与确认

需求正文、计划或任务范围变化后，相关版本会变化。旧版本确认和测试证据不能自动沿用；应根据
变化范围重新确认并补足验证。
