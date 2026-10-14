# DetraVision

**基于 YOLO 的数据标注、模型训练与目标识别一体化平台。**

[English](README.md) · 简体中文 · [文档](docs/README.md) · [路线图](docs/roadmap.md)

从图片标注到模型上线，在工作台完成数据集版本管理、本地训练、效果评估、批量图片识别和实时视频检测。支持自定义矩形框类别，重点打磨工业安全巡检场景。

```text
准备图片 → 标注与发布 → YOLO 训练 → 评估与对比 → 激活模型 → 图片 / 视频识别
```

![DetraVision 电蓝工作台，展示任务入口和运行状态（演示数据）](docs/images/workspace.png)

## 核心能力

| 能力 | 你可以做什么 |
|---|---|
| 标注与数据集 | 框选自定义目标，校验并发布可追溯的训练集版本 |
| 训练与评估 | 本地 YOLO 训练、基础/高级配置、停止与续训、逐类指标及实验对比 |
| 识别与事件 | 批量图片识别、多路 RTSP、ROI、事件确认与关闭 |
| 模型与部署 | 候选模型注册、预热激活、ONNX / TensorRT 导出，本地启动与容器部署 |

安全帽违规、明火和烟雾是内置示例；通用训练与识别支持其他类别。自定义类别的实时告警规则仍在规划中。

## 快速开始

需要 **Python 3.12+、Node.js 24.x**，支持 Windows / Linux。

```bash
git clone https://github.com/ThinkMo/detravision.git
cd detravision
```

Windows：

```powershell
.\start.cmd
```

Linux：

```bash
chmod +x start.sh
./start.sh
```

打开 **http://127.0.0.1:8000/**。空数据库且未配置初始密码的开发环境使用 `admin / admin123456`，首次登录必须改密。无模型也可先体验工作台；识别与训练需要另外准备权重和数据集。

安装、环境变量、GPU、Docker 与故障排查见[部署与使用指南](docs/operations.md)和[启动说明](docs/local-start.md)。

## TODO

- **自动标注｜调研阶段**：比较领域模型预标注、文本提示冷启动与质量复核方案。
- **云上训练｜规划中**：逐步适配阿里云、火山引擎、腾讯云、华为云，统一任务与模型产物管理。

详细范围、阶段验收和当前状态见[路线图](docs/roadmap.md)。上述规划不代表现有可用功能或发布时间承诺。

## 文档与贡献

| 入口 | 内容 |
|---|---|
| [部署与使用](docs/operations.md) | 环境配置、Docker、权限、架构、开发测试与当前限制 |
| [标注指南](docs/dataset-annotation-guide.md) | 数据集版本、人工标注与发布 |
| [训练指南](docs/training-guide.md) | 参数、评估、实验对比、模型导出与常见问题 |
| [自动标注调研](docs/research/2026-10-15-automatic-annotation-survey.md) | 候选路线、场景约束与离线验证方法 |
| [开源许可](docs/licensing.md) | AGPLv3 与依赖授权说明 |
| [贡献指南](CONTRIBUTING.md) | 问题反馈、开发检查与提交约定 |

欢迎通过 [Issues](https://github.com/ThinkMo/detravision/issues) 反馈问题、提供场景建议或提交 Pull Request。

## License

[GNU AGPLv3](LICENSE)，SPDX：`AGPL-3.0-only`。修改版本通过网络向用户提供服务时，需按第 13 条提供对应源码获取机会。Ultralytics、模型权重及其他依赖的授权须分别核对，详见[授权说明](docs/licensing.md)。

Copyright © 2026 [ThinkMo](https://github.com/ThinkMo).
