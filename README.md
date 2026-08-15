# Safety Detect — 安全帽 / 明火 / 烟雾实时检测系统

基于 FastAPI 与 Ultralytics YOLO11 的中心化视频检测服务。系统接入多路 RTSP，按配置抽帧推理，将检测结果转换为可去重、可确认的业务事件，并通过 REST、WebSocket 和飞书机器人对外提供能力。

当前仓库已实现可运行的后端 MVP；管理前端不在本阶段范围内。没有模型和摄像头时，API 仍可独立启动和配置。

## 已实现能力

- 摄像头、人员、告警组、事件和模型版本 REST API
- SQLite 开发数据库及自动建表，仓储层与 API 解耦
- 配置收敛器：摄像头启停或参数变化后自动启停 RTSP 线程
- 每路独立解码、全局有界帧队列、满队列丢弃最旧帧
- Ultralytics 推理适配器；未配置模型时使用空检测器
- ROI 过滤、连续帧确认、事件冷却去重
- 安全帽规则按人员头部区域关联 `no_helmet`，避免整个人框 IoU 失真
- 事件落库、抓拍保存、飞书 Webhook 通知
- `/ws/events` 事件推送和 `/ws/live/{camera_id}` JPEG 二进制帧推送
- CPU FP32 ONNX / GPU FP16 TensorRT 分目标导出及模型版本元数据

## 架构

```text
REST 配置 ──> SQLite ──> reconciler ──> 每路 RTSP Capture
                                            │
                                            v
                                  DropOldestQueue（有界）
                                            │
                                            v
                                  共享 inference workers
                                            │
                              ┌─────────────┼─────────────┐
                              v             v             v
                         规则与去重      实时 JPEG      检测事件
                              │                           │
                              └────────> 抓拍 / DB / 飞书 / WS
```

关键约束：

- 实时视频允许丢帧，不允许因消费变慢而无限积压。
- 摄像头配置的期望状态保存在数据库，运行状态由收敛器维护。
- `fire`、`smoke` 直接形成候选事件；`no_helmet` 必须与人员框上方 40% 的头部区域关联。
- 候选事件默认需要 5 秒内连续命中 3 次，同摄像头同类型事件 30 秒冷却。
- ROI 使用检测框中心点是否落入多边形判断；未配置 ROI 时视为全画面。

## 目录结构

```text
backend/
├── api/routes.py          # REST API
├── core/                  # 配置与飞书通知
├── db/database.py         # SQLite 仓储和建表
├── engine/detector.py     # 空检测器 / Ultralytics 检测器
├── pipeline/runtime.py    # 收敛、推理、事件流水线
├── rules/engine.py        # ROI、语义关联、时序去重
├── stream/                # RTSP 解码和有界队列
├── ws/hub.py              # WebSocket 广播
└── main.py                # FastAPI 入口
training/
├── configs/
│  ├── construction-ppe-helmet.yaml
│  └── safety.yaml
├── prepare_construction_ppe.py
├── train.py                 # train / resume / validate
└── export.py
deploy/                    # Dockerfile 与 Compose
tests/                     # 数据库、队列、规则测试
model_repo/                # 模型文件与元数据
```

## 快速开始

推荐使用 Python 3.11 或 3.12。创建虚拟环境并安装依赖：

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

复制 `.env.example` 为 `.env` 后，通过当前终端设置所需环境变量。程序不自动读取 `.env`；Docker Compose 会读取它。

启动服务：

```bash
uvicorn backend.main:app --reload
```

访问：

- OpenAPI：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>
- 事件流：`ws://127.0.0.1:8000/ws/events`
- 实时 JPEG：`ws://127.0.0.1:8000/ws/live/{camera_id}`

运行测试：

```bash
pytest -q
```

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `DATABASE_PATH` | `data/safety_detect.db` | SQLite 文件 |
| `SNAPSHOT_DIR` | `data/snapshots` | 告警抓拍目录 |
| `MODEL_PATH` | 空 | 启动时加载的 PT/ONNX/Engine 模型 |
| `WORKER_COUNT` | `1` | 推理线程数；单 GPU 建议从 1 开始压测 |
| `FRAME_QUEUE_SIZE` | `8` | 跨摄像头共享队列容量 |
| `RECONCILE_INTERVAL` | `3` | 配置收敛间隔（秒） |
| `ENABLE_RUNTIME` | `true` | 是否启动摄像头和推理运行时 |
| `FEISHU_WEBHOOK` | 空 | 飞书机器人 Webhook；为空则不通知 |

`rtsp_url` 和 Webhook 属于敏感配置。生产环境应通过密钥管理系统注入，API 响应和日志也应增加脱敏与鉴权；当前 MVP 默认部署在可信内网，不应直接暴露到公网。

## 数据与训练

类别定义：

```yaml
names:
  0: person
  1: helmet
  2: no_helmet
  3: fire
  4: smoke
```

当前 Construction-PPE 模型的类别为 `person`、`helmet`、`no_helmet`；未来合并的 safety 模型才会使用 `fire`、`smoke`。安全帽数据应包含完整人员框以及头部 `helmet` / `no_helmet` 框。烟火公开数据泛化有限，必须补充目标现场在不同光照、距离下的实拍，并加入反光、水汽、焊接等困难负样本。

以下命令必须在 `D:\free` 仓库根目录执行，并且已安装虚拟环境：

```powershell
# Start a new 50-epoch run
.\.venv\Scripts\python.exe -m training.train train `
  --data training/configs/construction-ppe-helmet.yaml `
  --model yolo11n.pt --epochs 50 --imgsz 512 --batch 4 --device 0

# Resume an interrupted run; --epochs is the new total target
.\.venv\Scripts\python.exe -m training.train resume `
  runs/detect/train-3/weights/last.pt --epochs 80 --device 0

# Evaluate best.pt on the held-out test split
.\.venv\Scripts\python.exe -m training.train validate `
  runs/detect/train-3/weights/best.pt `
  --data training/configs/construction-ppe-helmet.yaml --split test --device 0
```

Standalone validation creates an auto-incremented output directory selected by
Ultralytics, such as `runs/detect/val`, `runs/detect/val2`, or
`runs/detect/val-3`. The console prints a compact overall and per-class metric
summary; the selected validation directory contains plots and other validation
artifacts. Do not assume a fixed suffix.

省略 `--device` 时，如果 CUDA 可用则选择 GPU `0`，否则使用 CPU。默认值仍为 `workers=0` 和 `cache=false`，以规避 Windows 上已观察到的 page-file / multiprocessing failure。

| Need | Option or file | Guidance |
| --- | --- | --- |
| Smaller GPU memory use | `--batch 2` or `--batch 1` | Lower batch before lowering image size |
| Force CPU | `--device cpu` | Correct but much slower |
| Faster repeated reads | `--cache disk` | Prefer disk over RAM on this Windows host |
| Label a run | `--name helmet-v2` | Creates a recognizable run directory |
| Deploy/evaluate best quality | `runs/detect/train-3/weights/best.pt` | Best validation score during training |
| Recover exact run state | `runs/detect/train-3/weights/last.pt` | Optimizer and final epoch state for resume |
| Review curves | `runs/detect/train-3/results.csv`, `runs/detect/train-3/results.png` | Per-epoch losses and validation metrics |
| Review errors | `runs/detect/train-3/confusion_matrix.png`, `runs/detect/train-3/val_batch*_pred.jpg` | Class confusion and prediction examples |

These artifacts live inside the individual run directory (for example, `runs/detect/train-3/`); checkpoint weights are under its `weights/` subdirectory.

故障排查：

- `WinError 1455`：保持 `--workers 0 --cache false`，关闭孤立的 Python 进程；如果系统仍受内存限制，增大 Windows page file。
- CUDA out of memory：先降低 `--batch`，再降低 `--imgsz`；不要把 RAM cache 当作 GPU 显存修复方案。
- CUDA unavailable：运行 `.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.version.cuda)"` 检查 PyTorch/CUDA；在修正安装前使用 `--device cpu`。

自动选择 CPU/GPU 训练（请使用上面的 `train` 子命令）：

```bash
python -m training.train train --model yolo11n.pt --epochs 80 --imgsz 640
```

CPU 部署导出 FP32 ONNX：

```bash
python -m training.export runs/detect/train-3/weights/best.pt --target cpu --data-version v1
```

NVIDIA GPU 部署导出 FP16 TensorRT：

```bash
python -m training.export runs/detect/train-3/weights/best.pt --target gpu --data-version v1
```

Substitute your actual training run directory when it differs from
`runs/detect/train-3`.

导出产物写入 `model_repo/`。通过 `POST /api/models` 登记，再调用 `POST /api/models/{id}/activate` 激活；运行时会在下一次收敛时加载新模型，失败则保留旧检测器。

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| GET / POST | `/api/cameras` | 查询、新增摄像头 |
| PATCH / DELETE | `/api/cameras/{id}` | 修改、启停、删除摄像头 |
| GET / POST | `/api/persons` | 人员管理 |
| PATCH / DELETE | `/api/persons/{id}` | 修改、删除人员 |
| GET / POST | `/api/alarm-groups` | 告警组及成员 |
| GET | `/api/events` | 按摄像头、类型、状态查询事件 |
| PATCH | `/api/events/{id}` | 确认或关闭事件 |
| GET / POST | `/api/models` | 模型版本管理 |
| POST | `/api/models/{id}/activate` | 原子激活单一模型版本 |

## 生产化路线

1. 增加 JWT/OIDC、角色权限、RTSP URL 加密和审计日志。
2. 将 SQLite 仓储替换为 PostgreSQL/MySQL + SQLAlchemy/Alembic，并增加分页。
3. 增加轨迹关联，使去重键从“摄像头 + 类型”细化为“摄像头 + 目标轨迹 + 类型”。
4. 增加 Prometheus 指标、结构化日志、摄像头离线告警和推理延迟监控。
5. 管理前端、对象存储、消息队列及多实例任务归属。

## 当前限制

- SQLite 适合单实例 MVP，不支持多实例并发部署。
- WebSocket 实时画面为 JPEG 帧，不适合高帧率或大规模观看端；规模化时应换 WebRTC/HLS。
- 当前去重以摄像头和事件类型为粒度，尚未接入目标追踪器。
- 模型兼容性由 Ultralytics 运行时校验；生产部署应在激活前增加独立的模型预热和验收流程。
