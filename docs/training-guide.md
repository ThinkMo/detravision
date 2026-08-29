# 训练集构建与模型训练指南

本文说明如何在本项目中构建、标注和发布通用 YOLO 矩形框目标检测数据集，并通过管理控制台或命令行完成训练、评估、实验对比、续训和模型启用。Construction-PPE 三类安全帽数据集是完整示例，不是平台支持类别的限制。

所有 PowerShell 命令默认在仓库根目录执行。示例路径为 `D:\free`，实际使用时请替换为自己的仓库路径。

## 1. 通用目标检测与安全帽模板

平台支持任意数量的目标检测类别，例如 `fire`、`smoke`、`vehicle`、`vest`。类别名称和顺序来自已发布训练集版本；训练、测试评估和模型注册始终使用同一份类别快照。当前阶段只支持矩形框目标检测，不支持分类、分割、姿态或旋转框。

安全帽业务模板保留三个类别：

| 目标类别 | 训练类别 ID | Construction-PPE 原类别 ID | 说明 |
| --- | ---: | ---: | --- |
| `person` | 0 | 6 | 完整人员框，用于建立人员与头部区域的关系 |
| `helmet` | 1 | 0 | 已佩戴安全帽的头部 |
| `no_helmet` | 2 | 7 | 未佩戴安全帽的头部，是系统主要关注的违规类别 |

`person`、`helmet`、`no_helmet` 都参与训练；业务结果以模型明确识别到的 `helmet` 和 `no_helmet` 为准，不能把“检测到 person 且没有检测到 helmet”直接推导为未佩戴安全帽。

Construction-PPE 转换脚本只服务于上述安全帽模板，会丢弃其他 PPE 类别。自建通用数据集不经过该转换脚本，可在“模型管理 → 训练集”中定义自己的类别并人工标注。

## 2. 环境准备

推荐使用 Python 3.12。先创建并安装虚拟环境：

```powershell
cd D:\free
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

检查 PyTorch 是否识别 NVIDIA GPU：

```powershell
.\.venv\Scripts\python.exe -c "import torch; print('torch=', torch.__version__); print('cuda=', torch.cuda.is_available()); print('cuda_version=', torch.version.cuda)"
```

`cuda=True` 时可使用 `--device 0`。没有可用 GPU 时使用 `--device cpu`，但训练会明显变慢。

## 3. 准备原始 Construction-PPE 数据

下载 Ultralytics Construction-PPE 数据集 ZIP，并解压到一个独立的原始目录。例如：

```powershell
Expand-Archive `
  -LiteralPath "C:\Users\xiao\Downloads\construction-ppe.zip" `
  -DestinationPath "D:\free\datasets\construction-ppe"
```

解压后的有效数据根目录必须直接包含以下结构：

```text
datasets/construction-ppe/
├── images/
│   ├── train/
│   ├── val/
│   └── test/
└── labels/
    ├── train/
    ├── val/
    └── test/
```

如果 ZIP 内额外嵌套了一层目录，请把下一步命令的源路径指向真正包含 `images` 和 `labels` 的那一层。

## 4. 转换为三类训练集

目标目录必须不存在。首次转换执行：

```powershell
.\.venv\Scripts\python.exe -m training.prepare_construction_ppe `
  "datasets/construction-ppe" `
  "datasets/construction-ppe-helmet"
```

脚本会：

1. 处理 `train`、`val`、`test` 三个 split；
2. 将原类别映射为 `person=0`、`helmet=1`、`no_helmet=2`；
3. 复制有目标标注的图片；
4. 生成新的 YOLO 标签；
5. 输出每个 split 的图片数和各类别实例数。

输出示例结构：

```text
datasets/construction-ppe-helmet/
├── images/{train,val,test}/
└── labels/{train,val,test}/
```

需要重新转换时，不要覆盖旧目录。先将旧目录改名备份，或者明确删除旧目录后再执行转换命令。

## 5. 校验训练集

项目的数据配置为 [construction-ppe-helmet.yaml](../training/configs/construction-ppe-helmet.yaml)：

```yaml
path: datasets/construction-ppe-helmet
train: images/train
val: images/val
test: images/test
names:
  0: person
  1: helmet
  2: no_helmet
```

先运行转换和配置相关单元测试：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_prepare_construction_ppe.py `
  tests/test_train_args.py -q
```

开始训练前还应人工抽查：

- 图片与标签文件名一一对应；
- YOLO 标签每行都是 `class x_center y_center width height`；
- 坐标均归一化到 0～1；
- `no_helmet` 框住未戴安全帽的头部，而不是整个人；
- `person` 框覆盖完整人员；
- train/val/test 没有同一视频的相邻帧泄漏；
- 各 split 都包含足够的 `no_helmet` 实例和困难负样本。

## 6. 在管理控制台创建本地训练

### 6.1 准备训练配置

在 `.env` 中启用本地训练，并授权服务器数据目录：

```env
TRAINING_ENABLED=true
# Windows
TRAINING_DATASET_ROOTS=default=D:\free\datasets
TRAINING_RUN_DIR=data/training-runs
TRAINING_MODEL_CACHE_DIR=data/training-models
```

Linux 将根目录改为服务器绝对路径，例如：

```env
TRAINING_DATASET_ROOTS=default=/opt/safety-detect/datasets
```

修改配置后重启服务。进入“模型管理 → 训练集”，定义或选择服务器训练集；只有校验通过且状态为“已发布”的版本可以创建训练任务。编辑中的版本必须先完成待标注图片、校验并发布。

基础模型权重必须实际存在于项目根目录或 `TRAINING_MODEL_CACHE_DIR`。页面固定展示以下十个候选，但缺少对应 `.pt` 文件时该选项会显示“权重缺失”且不可选择：

| 系列 | 可选规格 | 说明 |
| --- | --- | --- |
| YOLO11 | `n`、`s`、`m`、`l`、`x` | 成熟的通用选择；页面优先默认 `yolo11m.pt` |
| YOLO26 | `n`、`s`、`m`、`l`、`x` | 仅在服务器已经放置对应兼容权重时可用 |

规格从 `n` 到 `x` 参数量和计算量逐步增加：`n` 最快、资源占用最低，`s` 偏轻量，`m` 适合作为精度/速度平衡起点，`l` 和 `x` 有更高精度潜力但训练与部署明显更慢、显存需求更高。模型更大不保证实际业务指标更高，最终必须用同一独立测试集比较。

### 6.2 新建训练任务

进入“模型管理 → 训练任务”，点击“新建训练”：

1. 选择一个已发布且可用的训练集版本；任务会保存数据配置与类别快照，后续修改不会改变该任务。
2. 选择基础模型。默认优先使用 `yolo11m.pt`；资源有限或先验证流程时建议使用 `yolo11n.pt`/`yolo11s.pt`。
3. 选择输入尺寸：
   - `640`：默认推荐，训练更快、显存占用较低，适合先建立可靠基线；
   - `960`：保留更多远处小目标细节，但像素量约为 640 的 2.25 倍，训练和推理都会明显变慢，建议在 640 基线仍存在小目标漏检时再对比。
4. 设置训练轮数、batch、设备和 patience。`device=auto` 自动选择 GPU 0 或 CPU；Windows 显存或页文件紧张时优先减小 batch。
5. 本地执行器当前可用；“远程训练”只保留入口，尚未实现。

首次使用某种基础模型前，请先确认权重文件已经放在允许目录。系统不会因为页面列出了模型名称就自动下载缺失权重。

基础模式使用安全的均衡默认配置，适合首次基线和日常训练。高级模式提供五种可复现实验预设：

| 预设 | 建议配置 | 适用情况 |
| --- | --- | --- |
| 均衡 | 640 / 100 epochs / patience 50 | 通用默认基线 |
| 小目标 | 960 / 100 / 50 | 远处或尺寸较小的目标 |
| 高召回 | 640 / 150 / 75 | 优先减少重点类别漏检 |
| 稳定微调 | 640 / 50 / 20 | 从已有领域权重低学习率微调 |
| 自定义 | 640 / 100 / 50 | 从均衡参数开始逐项调整 |

高级模式按组开放优化器与学习率、颜色/几何增强、缓存与复现、损失权重。所有参数均由服务端白名单、类型和范围约束；页面不能提交任意 Ultralytics 参数、命令行、YAML 或路径。任务保存最终完整配置，而不是只保存预设名称，因此未来默认值变化不会改变历史实验。

验收规则可设置总体 precision、recall、mAP50、mAP50-95，也可对训练集中的任意重点类别单独设置门槛。通用默认只要求总体 `mAP50 >= 0.50`；安全帽模板默认要求总体 `mAP50 >= 0.85`、`no_helmet precision >= 0.85`、`no_helmet recall >= 0.90`。这些门槛都可在创建任务前调整。

RAM 缓存、CPU 上运行 m/l/x、960 输入或 l/x 模型会显示资源风险确认。高级参数不能弥补错误标注、类别失衡或训练/测试数据泄漏；结果不理想时应先抽查数据和逐类别指标。

### 6.3 复制与比较实验

任务配置提交后不可修改。需要改变参数时，在任务卡点击“复制任务”，系统会读取不包含服务器路径、PID 和日志位置的安全模板并打开新任务表单。续训则严格继承原配置；想改变学习率或增强策略时应复制任务，而不是续训。

可选择 2～4 个使用同一训练集发布快照的任务进行对比。对比页面展示模型、尺寸、预设、关键高级参数、总体指标和公共类别指标。训练集快照不同的任务不能直接比较，避免把测试样本变化误认为模型改进。

### 6.4 停止与继续训练

页面的“停止”是安全停止请求，不是把同一个进程冻结在显存中：

- 排队或运行中的任务会进入停止流程；
- 成功保存 `last.pt` 后，任务卡会出现“恢复”；
- 点击“恢复”会从受控的 `last.pt` 创建一个新的续训任务，保留原任务作为历史记录；
- 没有有效 `last.pt`、已经完成或测试评估结束后不满足续训条件的任务不能恢复，页面会显示明确错误。

因此可以先停止、以后继续，但续训表现为一条新的任务记录。不要直接复制或修改任务运行目录中的 checkpoint。

### 6.5 指标、注册与启用模型

训练详情动态显示总体和所有类别的 precision、recall、mAP50、mAP50-95，并逐条说明验收门槛是否通过。安全帽训练会自然出现 `person`、`helmet`、`no_helmet` 三行，其他数据集显示自己的类别，不使用安全帽硬编码指标。

这些阈值是项目验收门槛，不代表单张图片的置信度必须都达到 85%～95%。还应检查混淆矩阵、漏检图片和独立 test split，避免只优化总体分数。

任务完成且存在 `best.pt` 后：

1. 点击“注册候选模型”；
2. 切换到“模型版本”，确认来源任务、类别顺序、训练预设、配置摘要和评估指标；
3. 点击“激活”并确认；
4. 激活成功后，新的实时监控和多图识别任务使用该模型并展示全部检测类别，已经运行中的任务不会被中途替换。

任意类别模型均可注册和激活。包含 `person`、`helmet`、`no_helmet` 的模型继续使用现有安全帽关联规则；其他类别当前只检测和展示，通用实时告警规则编辑器属于后续功能。

远程训练与 Atlas OM 转换当前没有实现，不应将保留入口理解为已经可用。

## 7. 先执行冒烟训练

正式训练前先运行 1 个 epoch，确认数据路径、标签、显存和输出目录都正常：

```powershell
.\.venv\Scripts\python.exe -m training.train train `
  --data training/configs/construction-ppe-helmet.yaml `
  --model yolo11n.pt `
  --epochs 1 `
  --imgsz 512 `
  --batch 2 `
  --workers 0 `
  --cache false `
  --device 0 `
  --name helmet-smoke
```

没有 GPU 时将 `--device 0` 改为 `--device cpu`。

冒烟训练应至少确认：

- 能扫描 train/val 图片和标签；
- 没有 `corrupt image`、类别越界或空数据集错误；
- 能完成一个 epoch 和验证；
- `runs/detect/helmet-smoke/weights/last.pt` 已生成。

## 8. 开始正式训练

本项目在 Windows 上使用保守配置，避免多进程数据加载和 RAM 缓存导致页文件不足：

```powershell
.\.venv\Scripts\python.exe -m training.train train `
  --data training/configs/construction-ppe-helmet.yaml `
  --model yolo11n.pt `
  --epochs 50 `
  --imgsz 512 `
  --batch 4 `
  --workers 0 `
  --cache false `
  --device 0 `
  --name helmet-50
```

参数说明：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--model` | `yolo11n.pt` | 预训练权重或模型 YAML |
| `--epochs` | 50 | 总训练轮数 |
| `--imgsz` | 512 | 方形输入尺寸 |
| `--batch` | 4 | 每批图片数；显存不足时优先降到 2 或 1 |
| `--workers` | 0 | Windows 推荐值，避免多进程加载问题 |
| `--cache` | `false` | 可选 `false`、`disk`、`ram`；本机优先 `false` 或 `disk` |
| `--device` | `auto` | `auto` 自动选择 GPU 0 或 CPU，也可显式指定 `0`/`cpu` |
| `--name` | 自动编号 | 指定可识别的运行目录名 |

## 9. 中断后继续训练

使用对应运行目录下的 `last.pt` 恢复优化器和 epoch 状态。`--epochs` 表示新的总轮数目标：

```powershell
.\.venv\Scripts\python.exe -m training.train resume `
  runs/detect/helmet-50/weights/last.pt `
  --epochs 80 `
  --workers 0 `
  --cache false `
  --device 0
```

不要使用 `best.pt` 做精确续训；`best.pt` 用于验证和部署，`last.pt` 用于恢复训练状态。

## 10. 验证模型

在独立 test split 上评估最佳权重：

```powershell
.\.venv\Scripts\python.exe -m training.train validate `
  runs/detect/helmet-50/weights/best.pt `
  --data training/configs/construction-ppe-helmet.yaml `
  --split test `
  --imgsz 512 `
  --batch 4 `
  --workers 0 `
  --device 0
```

控制台会输出总体以及 `person`、`helmet`、`no_helmet` 的 mAP50 和 mAP50-95。验收时应重点观察 `no_helmet` 的 precision、recall、混淆矩阵及漏检样本，不能只看总体 mAP。

## 11. 训练产物

以 `runs/detect/helmet-50/` 为例：

| 文件 | 用途 |
| --- | --- |
| `weights/best.pt` | 验证指标最优的模型，用于推理、验证和部署 |
| `weights/last.pt` | 最后一轮完整状态，用于续训 |
| `results.csv`、`results.png` | 每轮 loss 和验证指标曲线 |
| `confusion_matrix.png` | 类别混淆情况 |
| `val_batch*_pred.jpg` | 验证集预测可视化 |
| `args.yaml` | 本次训练的完整参数 |

把模型用于本地后端时：

```powershell
$env:MODEL_PATH = (Resolve-Path "runs/detect/helmet-50/weights/best.pt").Path
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

## 12. 常见问题

### `WinError 1455` 或 `cufft64_11.dll` 加载失败

这是 Windows 页文件或并发加载造成的资源不足，不应忽略。保持：

```text
--workers 0 --cache false
```

关闭其他占用 GPU/RAM 的进程，必要时增大 Windows 虚拟内存，然后重新运行冒烟训练。

### CUDA out of memory

按以下顺序调整：

1. 将 `--batch 4` 降到 2 或 1；
2. 再将 `--imgsz 512` 降低；
3. 不要使用 `--cache ram` 作为显存问题的解决方案。

### 找不到数据集

确认命令从仓库根目录执行，并检查：

```powershell
Test-Path datasets/construction-ppe-helmet/images/train
Test-Path datasets/construction-ppe-helmet/labels/train
Get-Content training/configs/construction-ppe-helmet.yaml
```

### 训练结果没有 `no_helmet`

检查转换统计和标签内容，确认原类别 7 已映射为新类别 2；抽查 `labels/train/*.txt`，并确认数据中确实包含未戴安全帽场景。若类别数量严重不平衡，应优先补充真实现场违规样本，而不是只调高训练轮数。

## 13. 自建或追加训练集

自建数据也应使用相同三类 ID 和 YOLO 目录格式。建议：

1. 按现场、摄像头或视频划分 train/val/test，避免相邻帧跨 split；
2. 包含远近尺度、遮挡、逆光、夜间、不同安全帽颜色；
3. 加入帽子拿在手里、普通帽子、反光、水汽等困难负样本；
4. 对 `no_helmet` 漏标做重点复核；
5. 新数据版本使用新的目录和 YAML，不覆盖已用于历史模型的数据快照；
6. 保存数据来源、许可、标注规范、转换脚本版本和类别统计。

若数据集同时包含 `fire`、`smoke`，请使用 [safety.yaml](../training/configs/safety.yaml) 的五类定义，并确保所有标签 ID 与该配置完全一致；不要把三类安全帽标签直接与五类标签混用。
