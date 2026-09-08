# DetraVision — 通用目标检测与训练平台

DetraVision 是一个基于 FastAPI、React 和 Ultralytics YOLO 的通用目标检测与训练平台。它可以接入多路 RTSP 视频或批量图片，识别自定义目标，生成可确认的事件，并提供数据集标注、模型训练、评估和版本管理能力。安全帽违规、明火和烟雾是项目内置示例，而不是类别限制。

项目适合本地试用、算法验证和可信内网中的单机部署。没有模型或摄像头时，管理控制台和 API 仍可正常启动，方便先体验账号、配置、事件和训练集管理。

## 核心能力

- 实时检测：多路 RTSP 解码、共享推理队列、ROI、连续帧确认和事件冷却去重
- 多图识别：一次上传最多 20 张图片，在线预览标注结果并下载单图或 ZIP
- 事件闭环：事件查询、确认、关闭、抓拍、WebSocket 推送和飞书通知
- 管理控制台：摄像头、人员、告警组、模型、用户、品牌和审计日志管理
- 本地训练：任意 YOLO 矩形框类别、基础/高级预设、停止续训和实验对比
- 训练评估：总体及分类别指标、建议置信度、误检/漏检和 PR/F1/P/R 曲线
- 模型管理：训练候选模型注册、激活，以及 CPU ONNX / GPU TensorRT 导出
- 安全基础：服务端会话、管理员/普通用户权限、首次强制改密和操作审计

## 5 分钟本地试用

### 1. 准备环境

- Python 3.12 或更高版本
- Node.js 24.x（必须为 `v24.*`）
- Windows 或 Linux

先确认版本：

```powershell
python --version
node --version
```

### 2. 一键启动

Windows：

```powershell
.\start.cmd
```

Linux：

```bash
chmod +x start.sh
./start.sh
```

首次运行会自动创建 `.venv`、安装缺失依赖、构建前端，并使用本地 SQLite 启动同源服务。以后已有依赖和构建产物时，可以快速启动：

```powershell
.\start.cmd --skip-install --skip-build
```

Linux 对应命令为 `./start.sh --skip-install --skip-build`。按 `Ctrl+C` 停止服务。

更多端口、跳过安装和配置优先级说明见 [Windows / Linux 本地一键启动](docs/local-start.md)。

### 3. 打开并登录

- 管理控制台：<http://127.0.0.1:8000/>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

本地数据库为空且未设置 `ADMIN_INITIAL_PASSWORD` 时，使用以下开发账号：

```text
用户名：admin
临时密码：admin123456
```

首次登录后必须立即修改密码。该默认密码只能用于本地开发；生产环境不会使用默认密码。

### 4. 按顺序体验

1. 登录后查看“安全态势”和各管理页面。
2. 若暂时没有模型，可先配置人员、告警组、摄像头和训练集；实时页会显示推理未启用。
3. 若已有 Ultralytics 兼容的 PT、ONNX 或 Engine 模型，在根目录 `.env` 中设置 `MODEL_PATH`，重启服务后进入“多图识别”上传测试图片。
4. 要测试实时监控，添加 RTSP 摄像头并关联告警组，然后在“实时监控”选择摄像头。
5. 要训练模型，先配置 `TRAINING_DATASET_ROOTS`，再到“模型管理 → 训练集”定义并发布数据集，最后在“训练任务”创建训练。

示例 `.env`：

```env
APP_ENV=development
DATABASE_URL=sqlite:///data/safety_detect.db
MODEL_PATH=runs/detect/train-3/weights/best.pt
TRAINING_DATASET_ROOTS=default=D:\datasets
```

启动器会读取根目录可选的 `.env`，但不会覆盖当前终端已经设置的环境变量。完整示例见 [.env.example](.env.example)。

## 选择运行方式

| 场景 | 数据库 | 启动方式 | 适用范围 |
|---|---|---|---|
| 本地试用 | SQLite | `start.cmd` / `start.sh` | 单应用实例、单批量工作线程 |
| 前后端开发 | SQLite | Uvicorn + Vite | 开发调试和热更新 |
| 生产式容器基线 | MySQL 8.4 | Docker Compose | 部署联调；上线前仍需环境验收 |

不要把 SQLite 本地验证结果当作生产数据库验收结果。生产数据库要求 MySQL 8.0.13 或更高版本；上线前必须在目标环境执行迁移、仓储、批量任务、备份和恢复测试。

## Docker + MySQL 部署

容器镜像使用 Node.js 24 构建前端，运行阶段安装已锁定的 Python 直接依赖，并以非 root 用户启动。应用容器会先执行 Alembic 迁移，成功后再启动 Uvicorn。

```bash
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env，替换所有示例密码
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up --build -d
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps
```

部署后访问 `http://127.0.0.1:8000/api/health` 检查服务。Compose 默认提供：

- MySQL 8.4 数据库和持久化卷
- 应用数据与模型仓库持久化卷
- `DATASET_HOST_DIR` 到 `/app/datasets` 的训练集挂载
- `TRAINING_MODEL_HOST_DIR` 到 `/app/data/training-models` 的基础权重挂载
- 数据库和应用健康检查

注意：

- `deploy/.env` 不得提交到 Git。
- `DATABASE_URL` 中密码的特殊字符必须进行 URL 编码。
- HTTPS 反向代理后应设置 `COOKIE_SECURE=true`。
- 数据集挂载需要支持人工标注写入，宿主机权限应仅授予服务账户。
- 当前镜像不包含 CUDA 或 Atlas 专用运行时；GPU 部署需按目标主机扩展镜像。

## 主要使用流程

### 实时检测与事件

管理员先创建人员和告警组，再添加摄像头并配置 RTSP、ROI、抽帧间隔和目标告警组。系统按配置启动采集线程，将帧送入全局有界队列；队列满时丢弃最旧帧，避免实时视频无限积压。

`fire`、`smoke` 可直接形成候选事件；`no_helmet` 需要与人员框上方 40% 的头部区域关联。候选事件默认在 5 秒内连续命中 3 次后确认，同摄像头同类型事件默认冷却 30 秒。

浏览器通过 `/ws/events` 接收事件，通过 `/ws/live/{camera_id}` 接收实时 JPEG。WebSocket 使用浏览器会话 Cookie，不会把令牌写入 URL 或浏览器存储。

### 多图识别

1. 进入“多图识别”，选择 PNG、JPEG 或 WebP；每张最大 10 MiB，合计最多 20 张。
2. 点击“开始识别”，任务会从等待、处理中进入完成、部分失败或失败状态。
3. 完成后可查看放大标注图、下载单张标注图或结果 ZIP。
4. 普通用户只能查看自己的任务，管理员可以查看全部任务。

`MODEL_PATH` 为空时，页面仍能使用，但多图上传会返回 `model_unavailable`，不会创建任务或遗留上传文件。原图与结果默认保留 30 天，由后台清理器定期删除。

### 数据集与模型训练

训练只允许访问 `TRAINING_DATASET_ROOTS` 明确授权的目录：

```env
TRAINING_DATASET_ROOTS=construction=D:\datasets\construction;customer=D:\datasets\customer
```

每个授权根目录必须是已存在的绝对目录，不能相互嵌套。管理员可在控制台中：

1. 浏览授权目录并定义训练、验证、测试分片和类别。
2. 复制已发布版本、扫描新增图片、框选标注并校验发布。
3. 选择基础或高级训练模式，设置预设、学习率、增强参数和验收门槛。
4. 停止或续训任务，复制参数创建新实验，并对比 2～4 个同数据快照任务。
5. 查看总体/分类别指标、混淆矩阵、建议置信度和误检/漏检分析。
6. 注册通过评估的候选模型并激活。

训练平台支持任意类别的 YOLO 矩形框目标检测。Construction-PPE 的 `person`、`helmet`、`no_helmet` 只是内置安全帽模板，不是类别限制。

详细操作：

- [训练集构建与模型训练指南](docs/training-guide.md)
- [训练集版本与人工标注指南](docs/dataset-annotation-guide.md)

## 权限说明

| 页面或能力 | 管理员 | 普通用户 |
|---|---:|---:|
| 安全态势、实时监控、事件中心 | 是 | 是 |
| 确认或关闭事件 | 是 | 是 |
| 创建多图任务 | 是 | 是 |
| 查看多图任务 | 全部任务 | 仅自己的任务 |
| 摄像头、人员、告警组、模型与训练 | 是 | 否 |
| 网站品牌、用户与审计日志 | 是 | 否 |

前端隐藏无权入口只用于改善体验，所有权限仍由后端强制校验。

## 常用配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `APP_ENV` | `development` | 运行环境；开发环境启动时自动迁移 |
| `DATABASE_URL` | `sqlite:///data/safety_detect.db` | SQLAlchemy 数据库 URL |
| `ADMIN_INITIAL_PASSWORD` | 空 | 首次创建 `admin` 的密码；生产环境必填 |
| `SESSION_TTL_HOURS` | `12` | 登录会话有效期（小时） |
| `COOKIE_SECURE` | `false` | HTTPS 生产环境应设为 `true` |
| `MODEL_PATH` | 空 | 启动时加载的 PT、ONNX 或 Engine 模型 |
| `UPLOAD_DIR` | `data/uploads` | Logo、多图原图和标注结果目录 |
| `SNAPSHOT_DIR` | `data/snapshots` | 事件抓拍目录 |
| `BATCH_RETENTION_DAYS` | `30` | 多图任务和文件保留天数 |
| `BATCH_WORKER_COUNT` | `1` | 当前固定为 1，其他值会拒绝启动 |
| `WORKER_COUNT` | `1` | 共享推理线程数 |
| `FRAME_QUEUE_SIZE` | `8` | 跨摄像头共享帧队列容量 |
| `ENABLE_RUNTIME` | `true` | 是否启用摄像头与实时推理 |
| `FEISHU_WEBHOOK` | 空 | 飞书机器人 Webhook；为空时不通知 |
| `TRAINING_ENABLED` | `true` | 是否启用本地训练调度器 |
| `TRAINING_DATASET_ROOTS` | 空 | 控制台可访问的训练集授权根目录 |
| `TRAINING_RUN_DIR` | `data/training-runs` | 训练日志、权重和评估制品目录 |
| `TRAINING_MODEL_CACHE_DIR` | `data/training-models` | 可选基础 `.pt` 权重目录 |

训练调度器还支持 `TRAINING_HEARTBEAT_SECONDS`、`TRAINING_LEASE_SECONDS` 和 `TRAINING_RECOVERY_GRACE_SECONDS`。默认值及完整注释见 [.env.example](.env.example)。

RTSP URL、Webhook、数据库凭据和管理员密码属于敏感配置，应通过密钥管理系统或受保护的环境文件注入。不要把真实凭据提交到仓库或写入公开日志。

## 开发与测试

### 前后端热更新

后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

另开终端启动前端：

```powershell
cd frontend
npm run dev
```

开发页面默认位于 <http://127.0.0.1:5173>，Vite 会将 `/api` 和 `/ws` 代理到后端的 8000 端口。

### 自动化检查

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm run test:run
npm run typecheck
npm run build
```

CI 配置位于 [.github/workflows/ci.yml](.github/workflows/ci.yml)，执行 Python 测试、前端测试、类型检查和生产构建。需要真实 MySQL 或真实模型的测试通过显式环境变量启用，不会默认连接外部服务。

## 架构概览

```text
REST 配置 ──> SQLAlchemy / Alembic ──> reconciler ──> 每路 RTSP Capture
                                                  │
                                                  v
                                        DropOldestQueue
                                                  │
                                                  v
                                         inference workers
                                                  │
                              ┌───────────────────┼───────────────────┐
                              v                   v                   v
                         规则与去重           实时 JPEG           检测事件
                                                                      │
                                                   抓拍 / DB / 飞书 / WebSocket
```

训练调度器使用数据库租约、执行代次和心跳保护任务所有权。服务异常退出后不会复用旧 PID；失联任务在宽限期后标记为中断，有 `last.pt` 时可创建续训任务。

## 目录结构

```text
backend/       # FastAPI、鉴权、数据库、实时与批量推理
frontend/      # React/Vite 管理控制台及前端测试
training/      # 数据转换、训练、续训、验证和模型导出 CLI
tests/         # Python 自动化测试
migrations/    # Alembic 迁移环境和版本脚本
deploy/        # Dockerfile、Compose 和容器启动脚本
docs/          # 使用指南、设计规格和实施记录
model_repo/    # 本地导出模型目录
```

`data/`、`datasets/`、`runs/`、`.venv/`、`frontend/node_modules/` 和 `frontend/dist/` 是本地生成目录，不应提交到 Git。

## API 与更多文档

服务启动后访问 <http://127.0.0.1:8000/docs> 查看完整 OpenAPI 文档。常用接口包括：

- `/api/cameras`、`/api/persons`、`/api/alarm-groups`
- `/api/events`、`/api/models`
- `/api/batch-jobs`
- `/api/training/catalog`、`/api/training-jobs`
- `/ws/events`、`/ws/live/{camera_id}`

完整文档索引见 [docs/README.md](docs/README.md)。

## 常见问题

### Node.js 版本不符合要求

启动器要求 Node.js 24.x。安装或切换版本后重新打开终端，确认 `node --version` 输出 `v24.*`。

### 8000 端口被占用

停止旧服务，或改用其他端口：

```powershell
.\start.cmd --port 8080
```

### 页面能打开，但无法识别图片

检查 `.env` 中的 `MODEL_PATH` 是否存在且与当前 Ultralytics 环境兼容。修改后需重启服务。

### 新建训练时基础模型不可选

将 `.pt` 权重放到项目根目录或 `TRAINING_MODEL_CACHE_DIR`，然后重启服务刷新模型目录。

### SQLite 报 `database is locked`

确认只启动了一个本地应用实例，并保持 `BATCH_WORKER_COUNT=1`。停止重复实例后再启动；不要通过增加工作线程规避 SQLite 写锁限制。

### CUDA 显存不足或不可用

训练时先降低 batch，再降低输入尺寸；CPU 模式可以运行但会明显更慢。不要使用 RAM cache 解决 GPU 显存不足。完整排查步骤见[训练指南](docs/training-guide.md)。

## 当前限制

- SQLite 仅支持本地单实例和单批量工作线程；生产环境应使用完成实机验收的 MySQL。
- WebSocket 实时画面采用 JPEG 帧，不适合大规模或高帧率观看；规模化可改用 WebRTC/HLS。
- 当前事件去重以摄像头和事件类型为粒度，尚未接入目标轨迹关联。
- 批量任务使用本地磁盘和单工作线程，多实例调度与对象存储尚未验收。
- 模型兼容性由 Ultralytics 运行时校验；生产激活前仍应独立预热和验收。
- 当前默认部署面向可信内网，不应在缺少 HTTPS、网络隔离和密钥管理时直接暴露公网。

## License

Copyright (C) 2026 [ThinkMo](https://github.com/ThinkMo).

本项目仅依据 [GNU Affero General Public License v3.0](LICENSE)（SPDX：`AGPL-3.0-only`）授权。项目源代码地址为 <https://github.com/ThinkMo/detravision>。如果修改本项目并通过网络向用户提供服务，请同时向这些用户提供对应版本的完整源代码。

本项目使用 [Ultralytics YOLO](https://www.ultralytics.com/license)。部署和再分发时还应确认你的 Ultralytics 使用方式及其他依赖项符合各自许可证；如需闭源或专有商业使用，请自行取得所需的商业授权。
