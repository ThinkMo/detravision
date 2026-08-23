# Windows / Linux 本地一键启动

本项目提供两个平台入口，内部共用 `scripts/start_local.py`，因此 Windows 与 Linux 的准备和启动行为一致。

## 环境要求

- Python 3.12 或更高版本
- Node.js 24.x（必须是 `v24.*`）
- 首次安装依赖时能够访问 Python 与 npm 软件源

## Windows

在项目根目录双击 `start.cmd`，或在 PowerShell / CMD 中运行：

```powershell
.\start.cmd
```

## Linux

在项目根目录运行：

```bash
chmod +x start.sh
./start.sh
```

首次启动会按需创建 `.venv`、安装 `requirements.txt`、执行 `npm ci`、构建前端，然后以前台方式启动 FastAPI。已有 `.venv` 和 `frontend/node_modules` 时不会重复安装依赖，但默认仍会重新构建前端。

启动成功后访问：

- 控制台：<http://127.0.0.1:8000/>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

终端按 `Ctrl+C` 即可停止服务。

## 常用参数

```bash
# 已确认依赖存在时跳过安装
./start.sh --skip-install

# 已有 frontend/dist 时同时跳过安装和构建
./start.sh --skip-install --skip-build

# 允许局域网访问并修改端口
./start.sh --host 0.0.0.0 --port 8080
```

Windows 使用相同参数，例如：

```powershell
.\start.cmd --skip-install --skip-build --port 8080
```

`--skip-install` 在 `.venv` 或 `frontend/node_modules` 缺失时会明确报错；`--skip-build` 在 `frontend/dist` 缺失时也会拒绝启动，避免只看到空白页或 503。

## 本地配置

启动器会读取项目根目录可选的 `.env`。操作系统或当前终端已经设置的环境变量优先级更高，启动器不会覆盖它们，也不会打印密码等变量值。没有 `.env` 时使用以下本地默认配置：

```env
APP_ENV=development
DATABASE_URL=sqlite:///data/safety_detect.db
UPLOAD_DIR=data/uploads
BATCH_RETENTION_DAYS=30
BATCH_WORKER_COUNT=1
```

需要推理时请在 `.env` 中设置实际模型，例如：

```env
MODEL_PATH=runs/detect/train-3/weights/best.pt
```

本启动器仅面向本地 SQLite 单实例开发与验收，不负责生产 MySQL 迁移、部署或多实例进程管理。生产环境请遵循根目录 README 的“数据库迁移与生产配置”。

## 常见问题

- 提示 Node.js 版本错误：安装 Node.js 24，并重新打开终端确认 `node --version` 为 `v24.*`。
- 提示缺少 Python：安装 Python 3.12+，Windows 安装时启用 Python Launcher 或加入 PATH。
- 依赖安装失败：检查网络/软件源后重新运行；启动器不会删除现有虚拟环境或数据。
- 端口被占用：先停止旧服务，或使用 `--port 8080` 更换端口。
- SQLite 报 `database is locked`：确认只有一个本地服务实例且 `BATCH_WORKER_COUNT=1`，停止重复实例后重启。
