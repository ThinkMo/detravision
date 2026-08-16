# 训练集构建与模型训练指南

本文说明如何把 Ultralytics Construction-PPE 数据集转换为本项目使用的三类安全帽数据集，并使用仓库提供的命令完成冒烟训练、正式训练、续训和验证。

所有 PowerShell 命令默认在仓库根目录执行。示例路径为 `D:\free`，实际使用时请替换为自己的仓库路径。

## 1. 训练目标与类别

当前模型保留三个类别：

| 目标类别 | 训练类别 ID | Construction-PPE 原类别 ID | 说明 |
| --- | ---: | ---: | --- |
| `person` | 0 | 6 | 完整人员框，用于建立人员与头部区域的关系 |
| `helmet` | 1 | 0 | 已佩戴安全帽的头部 |
| `no_helmet` | 2 | 7 | 未佩戴安全帽的头部，是系统主要关注的违规类别 |

转换脚本会丢弃其他 PPE 类别。只有至少包含一个上述目标的图片才会进入转换后的训练集。

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

## 6. 先执行冒烟训练

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

## 7. 开始正式训练

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

## 8. 中断后继续训练

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

## 9. 验证模型

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

## 10. 训练产物

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

## 11. 常见问题

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

## 12. 自建或追加训练集

自建数据也应使用相同三类 ID 和 YOLO 目录格式。建议：

1. 按现场、摄像头或视频划分 train/val/test，避免相邻帧跨 split；
2. 包含远近尺度、遮挡、逆光、夜间、不同安全帽颜色；
3. 加入帽子拿在手里、普通帽子、反光、水汽等困难负样本；
4. 对 `no_helmet` 漏标做重点复核；
5. 新数据版本使用新的目录和 YAML，不覆盖已用于历史模型的数据快照；
6. 保存数据来源、许可、标注规范、转换脚本版本和类别统计。

若数据集同时包含 `fire`、`smoke`，请使用 [safety.yaml](../training/configs/safety.yaml) 的五类定义，并确保所有标签 ID 与该配置完全一致；不要把三类安全帽标签直接与五类标签混用。
