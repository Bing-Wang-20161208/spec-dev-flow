# 发布与边界

## 允许内容

- 通用脚本、模板和原生技能。
- 不含真实业务内容的合成测试。
- 配置示例、依赖锁和框架文档。

## 禁止内容

- 真实需求来源、spec、plan、tasks 和用户决定。
- 设计稿、接口定义、接口地址、业务代码和业务数据。
- 执行日志、测试证据、截图、通知回执和业务复盘。
- 凭据、Webhook、签名密钥和本机私有配置。

不得将业务内容复制进 README、示例、测试或提交说明来绕过目录边界。私有仓库同样适用。

## 本机目录

| 路径 | 用途 |
| --- | --- |
| `specs/` | 真实需求、任务和证据 |
| `.specify/workflows/runs/` | 原生运行记录 |
| `.specify/*.local.*` | 本机 provider、仓库、通知和发布配置 |
| `.tmp/` | 临时材料 |

Git 忽略不会删除文件，因此公共框架仓库不能承担真实业务资料的备份。

## 发布检查

```sh
.venv/bin/python -m unittest discover -s tests -v

# 只暂存已经审核的框架路径
git add <framework-paths>
.venv/bin/python scripts/repo_guard.py --index
git commit -m '<框架改动说明>'
git push
```

提交钩子和推送钩子会检查路径、正文中的本机关键词以及即将上传的新提交历史。不要使用
`--no-verify`、禁用钩子、强制暂存被忽略资料或推送未经审核的分支和标签。

新增框架文件时，先确认用途，再更新 `scripts/repo_guard.py` 白名单。路径检查不能识别所有
业务语义，发布者仍须审查实际变更和 Git 历史。
