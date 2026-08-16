# Master 目录整理设计

## 目标

在不改变现有模块路径、启动命令、训练命令、迁移入口和测试发现规则的前提下，让 `master` 根目录的源码、文档、本地数据和可再生成内容边界清晰，减少旧 worktree、缓存和运行产物造成的混淆。

## 范围

本次包含：

- 清理可安全再生成的 Python 测试缓存、字节码缓存和临时日志；
- 完善 `.gitignore` 对通用日志、coverage、mypy、ruff 和编辑器缓存的规则；
- 更新 README 的目录树，使其反映当前后端、前端、训练、迁移、部署和文档结构；
- 新增 `docs/README.md`，索引用户指南、设计规格和实施计划；
- 记录本地资源目录的用途、是否受版本控制以及是否可以删除后重建；
- 核验 Git 只注册 `master` 工作区，并处理已注销的 `.worktrees/web-console` 磁盘残留。

本次不包含：

- 不把 Python 包迁移到 `src/`；
- 不移动 `backend/`、`frontend/`、`training/`、`tests/`、`migrations/` 或 `deploy/`；
- 不重命名模块、API、环境变量、训练参数或 Alembic revision；
- 不移动 `.venv`、Node 依赖、数据集、训练结果和模型权重；
- 不删除用户数据、训练集、训练结果、数据库、上传文件或模型文件；
- 不修改业务行为、数据库结构或前端交互。

## 目标目录边界

```text
README.md                  # 项目入口、运行与常用操作
.env.example               # 环境变量模板
.gitignore                 # 源码与本地产物边界
requirements*.txt          # Python 依赖
alembic.ini / pytest.ini   # 工具配置

backend/                   # FastAPI、鉴权、数据库、推理与批量任务
frontend/                  # React/Vite 管理控制台
training/                  # 数据转换、训练、续训、验证与导出 CLI
tests/                     # Python 自动化测试
migrations/                # Alembic 环境和版本脚本
deploy/                    # Dockerfile 与 Compose
docs/                      # 用户指南、设计规格、实施计划和文档索引
model_repo/                # 导出模型的本地落点；仅保留 .gitkeep

data/                      # 本地 SQLite、上传、YOLO/Matplotlib/npm 缓存；忽略
datasets/                  # 原始与转换后的训练集；忽略
runs/                      # Ultralytics 训练和验证结果；忽略
.venv/                     # Python 虚拟环境；忽略
frontend/node_modules/     # Node 依赖；忽略
frontend/dist/             # 前端构建输出；忽略，可由 npm run build 重建
.worktrees/                # 临时 Git worktree；忽略，不是项目源码入口
```

根目录的 `yolo*.pt` 和其他模型权重继续按本地产物处理，由 `*.pt` 规则忽略。本轮不移动它们，避免改变 `--model yolo11n.pt` 的现有使用方式或触发重复下载。

## 清理策略

仅清理明确可再生成且不包含业务数据的目标：

- 根目录及子目录中的 `.pytest_cache/`；
- Python 包和测试目录中的 `__pycache__/`、`*.pyc`；
- 已停止测试服务留下的 `*.log` 临时日志；
- 已被 Git 注销且已确认所有提交都在 `master` 的 worktree 残留。

以下内容不清理：

- `data/` 中的 SQLite、上传文件、品牌文件和运行配置；
- `datasets/`、`runs/`、`model_repo/` 中的用户数据或模型；
- `.venv/`、`frontend/node_modules/` 和 `frontend/dist/`；
- 任何受版本控制文件。

`.worktrees/web-console` 当前未出现在 `git worktree list`，功能分支也已合入并删除；但其中部分缓存目录的 Windows ACL 拒绝当前进程取得所有权。自动清理必须只针对这个精确绝对路径。若当前权限仍失败，则保留目录并提供管理员 PowerShell 命令，不能通过扩大删除范围或绕过 ACL 处理。

## `.gitignore` 规则

保留现有项目规则，并补充：

```gitignore
*.log
.coverage
.coverage.*
htmlcov/
.mypy_cache/
.ruff_cache/
.idea/
.vscode/
```

不添加会隐藏源码、迁移、文档、测试或配置的宽泛规则。`model_repo/.gitkeep` 必须继续被跟踪。

## 文档结构

新增 `docs/README.md`，提供三组索引：

1. 用户指南：训练集构建与训练；
2. 设计规格：`docs/superpowers/specs/`；
3. 实施计划：`docs/superpowers/plans/`。

根 README 的目录树更新为当前真实结构，并明确本地目录不会进入 Git。README 继续作为首次使用入口，不把现有运行、训练或生产数据库说明拆散到多个必须跳转的页面。

## 验证

目录整理不改变生产代码，因此采用轻量但完整的结构验证：

- `git status --short` 只出现预期文档和 `.gitignore` 修改；
- `git check-ignore` 验证日志、coverage、缓存、数据集、训练结果、构建产物和模型权重被忽略；
- `git ls-files` 验证源码、迁移、测试、README、训练指南和 `model_repo/.gitkeep` 仍受版本控制；
- `python -m compileall -q backend training tests` 通过；
- 运行训练参数与目录相关的聚焦测试；
- `npm run typecheck` 通过，证明未破坏前端配置边界；
- `git diff --check` 通过；
- `git worktree list` 只包含 `D:/free` 的 `master`。

若仅因 Windows ACL 无法删除已注销目录，应把它作为环境清理残留报告，而不是宣称 Git worktree 仍存在或让整理任务失败。

## 验收标准

- `master` 的源码目录和所有既有命令保持不变；
- README 目录树与磁盘/版本控制边界一致；
- 文档可以从 `docs/README.md` 找到；
- 新增本地产物不会污染 `git status`；
- 可再生成缓存已清理，不删除任何数据集、训练结果、模型或运行数据库；
- Git 只注册 `master` 工作区；
- 无 Python 导入、前端类型或格式检查回退；
- 无法自动删除的 ACL 残留被准确说明并提供最小权限的管理员处理方式。
