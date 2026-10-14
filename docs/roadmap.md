# DetraVision Roadmap / 路线图

Updated / 更新日期: 2026-10-15

[English README](../README.md) · [简体中文 README](../README.zh-CN.md) · [简体中文](#中文)

## English

DetraVision remains a general-purpose YOLO annotation, training, and detection platform. Industrial safety inspection is the initial focus, starting with helmets and reflective vests. Priorities are not delivery dates or accuracy guarantees.

### Available foundation

Manual bounding-box annotation, dataset versions, local training, configuration snapshots, stop/resume, evaluation, experiment comparison, batch/live inference, model activation, and ONNX/TensorRT export are available. The workspace uses an electric-blue theme with task shortcuts and bilingual README files.

### Automatic annotation — research

A [literature and tooling survey (Chinese)](research/2026-10-15-automatic-annotation-survey.md) is complete; on-site model comparisons and a product annotation-job workflow are not yet available.

- [ ] Compare domain YOLO teachers and text-prompted bootstrapping; evaluate additional candidates as needed.
- [ ] Define PPE box semantics, person associations, and independently reviewed reference labels.
- [ ] Measure missing/incorrect labels, box corrections, generation time, and total labor at equal quality.
- [ ] Validate provenance, sampling, and difficult-case review before selecting an implementation.

Acceptance requires evidence on isolated site data and subsequent model quality. Confidence alone does not establish completeness. Lightly is a research reference, not an integration commitment.

### Cloud training — planned

Alibaba Cloud PAI-DLC, Volcengine custom training, Tencent Cloud TI-ONE, and Huawei Cloud ModelArts are target providers; no cloud connector is currently available. Provider documentation is linked in the Chinese section below.

- [ ] Establish consistent submission, status, logs, cancellation, recovery, and artifact contracts.
- [ ] Address authorized data transfer, credentials, cost visibility, and resource cleanup.
- [ ] Validate one provider end to end before expanding; select the first from actual pilot requirements.
- [ ] Return models for local evaluation/deployment with traceable datasets, settings, and weights.
- [ ] Validate Huawei GPU and Ascend environments separately.

### Evaluation and workflows — planned

- [ ] Add sample-level error review, dataset feedback, and scenario-based evaluation.
- [ ] Support configurable live alert rules and model-switch compatibility checks.
- [ ] Evaluate collaboration, identity integration, and multi-machine resource scheduling.

## 中文

定位：基于 YOLO 的标注、训练、识别一体化平台。以工业安全巡检为重点实践方向，先围绕安全帽、反光衣打磨数据质量与现场识别效果，保留通用矩形框类别底座。

本页区分已有能力、调研与未来规划；优先级表达投入顺序，不构成发布日期或效果保证。

### 当前基础

- [x] 矩形框人工标注、数据集版本与发布校验。
- [x] 本地训练、配置快照、停止续训、评估与实验对比。
- [x] 图片批量识别、多路 RTSP 与示例事件处理。
- [x] 模型注册激活、ONNX / TensorRT 导出。
- [x] 电蓝工作台、任务快捷入口与中英文 README。

### 近期：自动标注研究与验证

状态：已完成[文档调研](research/2026-10-15-automatic-annotation-survey.md)，尚未进行候选模型现场对照实验，平台尚无自动标注任务入口。

- [ ] 比较领域 YOLO 教师模型与文本提示检测的冷启动路线；按资源与效果加入其他候选。
- [ ] 建立安全帽、反光衣的标签定义、人员关联规则与独立人工参照集。
- [ ] 比较漏标、错标、框修订量、生成时间与同质量总工时。
- [ ] 验证候选标签、来源追溯、抽检和疑难样本复核流程后，再决定产品化方案。

阶段验收：在隔离的现场样本上证明质量与净节省工时，并检查后续模型效果。高置信度不等于无漏标，空预测不直接认定无目标。不预设“零人工”或准确率提升比例。Lightly 仅作调研参考，不是集成计划。

### 中期：云上训练

状态：当前仅支持本地训练；云平台连接器均为规划项。

| 目标平台 | 参考训练服务 | 状态 |
|---|---|---|
| 阿里云 | [PAI-DLC](https://www.alibabacloud.com/help/en/pai/create-a-training-task) | 规划中 |
| 火山引擎 | [机器学习平台自定义训练](https://docs.volcengine.com/docs/ml-platform/single-node-distributed-training-task?lang=zh) | 规划中 |
| 腾讯云 | [TI-ONE 训练任务](https://cloud.tencent.com/document/product/851/75083) | 规划中 |
| 华为云 | [ModelArts 自定义训练](https://support.huaweicloud.com/develop-modelarts/develop-modelarts-0006.html) | 规划中 |

- [ ] 定义统一的训练提交、查询、日志、取消、失败恢复和产物回收合同。
- [ ] 支持经授权的数据同步、凭据保护、费用提示和资源释放。
- [ ] 先打通一家云的完整流程，再以同一验收方法增加连接器；第一家依实际试点环境决定。
- [ ] 将云端模型带回本地评估、注册和部署，保留数据/配置/权重溯源。
- [ ] 华为云 GPU 与昇腾运行环境分别验证；云账号可用不代表训练框架与硬件已适配。

阶段验收：在目标账号、地域和硬件上完成提交、取消、异常恢复、费用/资源检查及模型回收验证。云训练是训练执行方式，现场效果和成本仍是判断标准。

### 后续候选

- [ ] 误检漏检样本级复核、回流与场景分组评估。
- [ ] 通用类别实时告警规则、目标关联及模型切换兼容检查。
- [ ] 评估团队协作、身份集成和多机资源调度。
