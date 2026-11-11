# Spec Dev Flow

在本机用原生 Spec Kit 管理需求、评审、计划、任务、开发与验证。业务代码在配置的目标仓库中执行；本仓库只保存通用流程框架，不保存具体业务资料。

## 许可证

本框架使用 [MIT 许可证](LICENSE)，允许使用、修改和再分发，并要求保留相应版权与许可声明。包含或改编的 GitHub Spec Kit 文件的来源和原始许可见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 框架与业务资料

可以上传：通用脚本、原生技能、模板、合成测试、依赖锁和使用说明。框架以后可以持续更新、提交和推送。

禁止向个人 GitHub 上传：组织/公司具体需求、来源资料、spec/plan/tasks、设计稿、接口定义/地址、业务代码或数据、执行日志、截图、测试证据及业务复盘明细。不要把这些内容复制进 README、模板、测试或提交说明；私有仓库也不例外。

以下仅保留在本机：

| 路径 | 用途 |
|---|---|
| `specs/` | 每个真实需求的说明、计划、任务与证据 |
| `.specify/workflows/runs/` | Spec Kit 原生运行状态 |
| `.specify/flow.local.yml` | 当前机器的模型 provider 和代码仓库映射 |
| `.specify/notifications.local.json` | 飞书通知目标、Webhook、安全配置 |
| `.specify/notifications/` | 通知投递回执 |
| `.specify/publish.local.json` | 禁止上传的本机关键词 |
| `流程重构/`、`.tmp/` | 本机交接、验证归档与临时材料 |

Git 忽略不会删除这些文件。业务资料的备份应使用组织批准的位置，个人 GitHub 不承担其备份。

## 新机器或另一个项目的配置

需要 Python 3.11、uv、Git，以及已配置并可用的 Codex CLI。此框架锁定 Spec Kit 1.3.3，不固定执行模型。

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.lock
cp .specify/flow.example.yml .specify/flow.local.yml
cp .specify/publish.example.json .specify/publish.local.json
mkdir -p specs
chmod +x .githooks/pre-commit .githooks/pre-push
git config core.hooksPath .githooks
```

编辑 `.specify/flow.local.yml`：`repositories` 的键是任务使用的仓库别名，值为目标代码仓库的绝对路径，可添加多个仓库。示例中的 `app` 和路径须替换为实际值；现有配置不会自动同步到新机器。目标仓库须为独立 Git 仓库，编码规范和验证命令以该仓库自己的文档为准。

`provider: null` 表示使用当前 Codex 默认 provider；需要指定时填写本机已配置的名称，不在这里保存令牌或模型 ID。派发前重新查询模型目录，按任务和风险选型。

编辑 `.specify/publish.local.json` 的 `blocked_terms` 数组，加入本组织名称、仓库标识及其他禁止上传的关键词。此文件不上传；检查结果只报告路径，不打印命中正文。关键词检查不能代替对业务语义的审查。

同一流程中心可绑定多个仓库，每个需求单独建 `specs/<feature>/`。需要另一套独立的资料、配置和运行隔离时，另克隆一份框架并重新配置。不要复制旧项目的真实 specs、日志、密钥或运行状态作为模板。

## 日常推进

先读 [AGENTS.md](AGENTS.md)，资料生成在本中心执行，代码操作绑定配置的目标仓库。用户提供需求后，使用 `.agents/skills/` 中原生 specify、clarify、plan、tasks、analyze 等技能。

1. 在 `specs/<feature>/spec.md` 保存需求来源、范围、UI 状态、验收与用户决定。
2. 需求/UI 评审确认后记录当前版本；在 plan 中明确接口/mock、执行策略，生成原生 tasks。
3. 执行计划确认后，每次只派发指定任务和阶段；状态与下一步直接写在对应任务下。
4. 实际测试、必要独立检查及父级总体验证通过后才能完成。mock 阶段完成不代表整个需求完成；上线另记。

```sh
.venv/bin/python scripts/flow.py --help
.venv/bin/python scripts/flow.py check specs/<feature>
.venv/bin/python scripts/flow.py approve specs/<feature> review --quote '<用户原话>' --source '<来源>'
.venv/bin/python scripts/flow.py approve specs/<feature> plan --quote '<用户原话>' --source '<来源>'
.venv/bin/python scripts/flow.py models
.venv/bin/python scripts/flow.py run specs/<feature> T001 --phase implement --model '<当前候选>' --why '<理由>'
.venv/bin/python scripts/flow.py run specs/<feature> T001 --phase test --command '<已确认的验证命令>'
.venv/bin/python scripts/flow.py run specs/<feature> T001 --phase review --model '<当前候选>' --why '<理由>'
.venv/bin/python scripts/flow.py complete specs/<feature> --task T001
.venv/bin/python scripts/flow.py complete specs/<feature> --thread '<当前会话标识>'
```

上述 `<...>` 为占位符，不能原样运行。AI 根据当前技能、任务和仓库规则填参数；不要为占位命令建立任务。真实接口就绪与地址由用户提供，接口定义从目标仓库读取，不把配置存在当作真实接口可用。

## 中断与恢复

先读需求文件、证据及原生运行状态，核对目标仓库 Git 现场、实际副作用和旧执行者。旧进程未停止不能接手；未知结果不算通过。同一中心一次一个写入执行者。

正常 paused/failed 可通过辅助脚本受控 resume；running 必须先核对现场，使用 settle 后新 run 接续，不改写原生私有状态。相同问题与累计限制跨会话保留。详细参数以 `flow.py --help` 为准，不直接调用原生 workflow 绕过校验。

## 可选：飞书单向通知

```sh
.venv/bin/python scripts/notify.py init
.venv/bin/python scripts/notify.py check
.venv/bin/python scripts/notify.py test --dry-run
```

在生成的 `.specify/notifications.local.json` 填写自定义机器人的 `webhook`；`target` 为可配置的目标显示名；已有签名或关键词要求时填写 `secret` 或 `keyword`。文件权限要求 600，凭据不进入模型提示、命令参数或业务证据。不配置通知也能使用开发流程。

真实测试命令 `notify.py test --thread '<当前会话标识>'` 会发送一条明确标识的测试消息；Webhook 无法验证实际群名，收到后应核对群和机器人。仅发送「需要用户介入」与「整个需求开发完成」；不接收群回复，群内消息不构成批准。

需介入时先保存原生事实，再调用 `flow.py notify specs/<feature> --key '<稳定问题标识>' --summary '<简短问题>' --next '<建议/所需决定>' --thread '<会话标识>'`，可加 `--dry-run` 预览。父级完成校验并保存后自动通知，单任务完成不发。

同一事件与版本去重；发送中、拒绝和未知投递均不自动重发。网络阻塞超时 5 秒，短时投递子进程总时限 8 秒，通知失败不改变业务结果。不提供后台监控或补发，关机/强杀无法即时通知。新机器不会自动创建复盘定时任务，原有机器的自动化也不会通过此仓库迁移。

## 更新与发布框架

业务工作期间无需提交 specs；只在修改通用框架时提交。按实际内容审核变更，新增框架文件先加入 `scripts/repo_guard.py` 白名单，合成测试不要包含真实业务片段。

```sh
.venv/bin/python -m unittest discover -s tests
# 只暂存本次已审核的框架路径
git add <framework-paths>
.venv/bin/python scripts/repo_guard.py --index
git commit -m '<框架改动说明>'
git push origin main
```

提交钩子检查暂存区，推送钩子检查将上传的每个新提交，防止业务资料先提交后删除仍随历史上传；同时检查本机禁止词及提交说明。不要使用 `--no-verify`、禁用钩子、强制暂存业务资料或推送未经审核的其他分支/标签。检查无法识别所有业务语义，发布者仍须审查正文。

升级依赖、原生技能或模板后运行测试并记录框架变更；模型目录在执行时发现，不把某次成功型号固化进模板。仅框架文件及合成测试可以随更新推送，真实业务资料始终留在本地/组织批准的位置。
