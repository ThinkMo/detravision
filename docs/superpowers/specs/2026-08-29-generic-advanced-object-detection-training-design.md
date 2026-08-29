# 通用目标检测高级训练设计

**日期：** 2026-08-29

**状态：** 待用户最终审阅

**范围：** 通用矩形框目标检测训练、评估、模型注册与激活

**后续项目：** 通用实时告警规则编辑器

## 1. 背景

当前 Web 训练只开放训练集、基础模型、输入尺寸、epochs、batch、device 和 patience。任务可完成训练、测试集评估、模型注册和激活，但无法安全地控制优化器、学习率、数据增强、损失权重及可复现参数；验收逻辑也偏向安全帽场景。

当前本地任务的实际结果进一步说明了问题：总体 mAP50 可达到中等水平，但不同类别的效果差异很大。高级训练能力必须帮助用户创建可比较、可复现的实验，但不能把任意 Ultralytics 参数、命令行或路径直接交给前端。

系统应成为通用目标检测训练平台。安全帽只是其中一个业务模板，不是数据结构、训练合同或评估逻辑的硬编码前提。

## 2. 目标与非目标

### 2.1 目标

- 支持任意数量、任意合法名称的矩形框目标检测类别。
- 保留简单的基础模式，并增加受控高级模式。
- 提供均衡、小目标增强、高召回、稳定微调、自定义五种训练预设。
- 支持安全白名单内的优化器、学习率、增强、运行和损失权重参数。
- 将最终生效配置保存为不可变、可版本化任务快照。
- 按训练集实际类别动态展示和验收总体/逐类别指标。
- 支持复制任务、调整配置和比较同数据集实验。
- 任意类别模型均可注册、预热、激活并用于检测结果展示。
- 保持历史任务、续训、SQLite 与 MySQL 8.0.13 迁移兼容。

### 2.2 非目标

- 不支持分类、实例分割、语义分割、姿态估计或旋转框。
- 不允许前端提交原始命令行、任意 YAML 或任意 Ultralytics 键值。
- 第一阶段不实现自动超参数搜索或并行训练矩阵。
- 第一阶段不实现任意类别的实时告警规则编辑器。
- 不改变当前单机单训练进程的 GPU 所有权边界。
- 不在本阶段执行 Atlas OM 转换或远程训练。

## 3. 类别模型

训练集版本的 `classes` 快照是类别事实来源。训练、评估、任务详情和模型版本均使用该快照，不依赖固定类名。

安全帽模板使用三个类别：

```yaml
names:
  0: person
  1: helmet
  2: no_helmet
```

- `person` 标注完整人员框，用于人员定位和上下文关联。
- `helmet` 标注已佩戴安全帽的头部/安全帽区域。
- `no_helmet` 标注未佩戴安全帽的头部区域。
- 系统不得用“检测到 person 但没有检测到 helmet”直接推导 `no_helmet`。

其他训练集可以使用车辆、火焰、烟雾、反光衣、机械设备等完全不同的类别。高级训练和通用验收不得要求 `person`、`helmet` 或 `no_helmet` 存在。

## 4. 用户体验

### 4.1 新建训练

新建训练弹窗默认进入基础模式，继续显示：

- 已发布训练集版本；
- 基础模型；
- 640/960 输入尺寸；
- epochs、batch、device、patience。

用户打开高级模式后，依次看到：

1. 训练预设；
2. 优化器与学习率；
3. 数据增强；
4. 运行与复现；
5. 损失权重；
6. 通用验收规则；
7. 最终配置摘要。

前端使用 catalog 返回的 schema、枚举、范围、默认值和说明渲染表单，不复制一套独立参数规则。前端校验用于即时反馈，后端仍执行完整校验。

### 4.2 任务详情

任务详情新增：

- 基础配置和完整高级配置快照；
- 预设名称与 schema 版本；
- 总体指标卡；
- 按训练集类别动态生成的指标表；
- 验收规则及逐项通过/失败原因；
- 混淆矩阵和训练曲线；
- “复制为新任务”和“加入对比”。

旧任务没有高级配置时显示“传统默认配置”，仍可查看、注册、激活和按原规则解释。

### 4.3 复制与对比

“复制为新任务”读取原任务的安全请求模板，打开新建弹窗。用户可以修改基础/高级参数和验收规则，提交后创建全新任务与幂等键；原任务保持不变。

用户可选择 2～4 个具有相同训练集 manifest 摘要的任务进行对比。对比视图显示：

- 基础模型、输入尺寸、训练轮数和预设；
- 不同的高级参数；
- 总体指标；
- 所有公共类别的逐类指标；
- 验收结果和最佳权重摘要。

不同 manifest 摘要的任务不能进入直接指标对比，避免把不同测试集的结果当作同一基准。

## 5. 训练预设

预设由后端定义并带版本号。前端只展示 catalog 返回的定义。预设展开后形成完整配置，任务不依赖未来可能变化的预设默认值。

### 5.1 核心建议

| 预设 | 推荐尺寸 | 推荐 epochs | 推荐 patience | 用途 |
|---|---:|---:|---:|---|
| `balanced` | 640 | 100 | 50 | 通用默认基线 |
| `small_object` | 960 | 100 | 50 | 远处/小尺寸目标 |
| `high_recall` | 640 | 150 | 75 | 优先降低重点类别漏检 |
| `stable_finetune` | 640 | 50 | 20 | 从已有领域模型低学习率微调 |
| `custom` | 640 | 100 | 50 | 从均衡配置开始完全自定义 |

预设不会强制替换用户明确选择的模型、尺寸、epochs 或 patience，而是在表单中填入建议值并显示被用户覆盖的字段。

### 5.2 高级参数默认值

`balanced` 与 `custom` 使用以下基线；其他预设只覆盖表中列出的差异字段：

```json
{
  "optimizer": "auto",
  "lr0": 0.01,
  "lrf": 0.01,
  "momentum": 0.937,
  "weight_decay": 0.0005,
  "warmup_epochs": 3.0,
  "warmup_momentum": 0.8,
  "warmup_bias_lr": 0.1,
  "cos_lr": false,
  "freeze": 0,
  "cache": "false",
  "workers": 0,
  "seed": 0,
  "deterministic": true,
  "amp": true,
  "hsv_h": 0.015,
  "hsv_s": 0.7,
  "hsv_v": 0.4,
  "degrees": 0.0,
  "translate": 0.1,
  "scale": 0.5,
  "shear": 0.0,
  "perspective": 0.0,
  "flipud": 0.0,
  "fliplr": 0.5,
  "mosaic": 1.0,
  "mixup": 0.0,
  "close_mosaic": 10,
  "box": 7.5,
  "cls": 0.5,
  "dfl": 1.5
}
```

差异配置：

| 预设 | 差异字段 |
|---|---|
| `small_object` | `translate=0.15`、`scale=0.7`、`mixup=0.05` |
| `high_recall` | `optimizer=AdamW`、`lr0=0.001`、`cls=0.75`、`mixup=0.1` |
| `stable_finetune` | `optimizer=AdamW`、`lr0=0.001`、`warmup_epochs=1.0`、`freeze=10`、`mosaic=0.2`、`scale=0.25` |

## 6. 高级配置合同

所有字段均为严格类型；布尔值不能作为整数，数值必须有限，未知字段一律拒绝。

| 字段 | 类型 | 允许值/范围 |
|---|---|---|
| `optimizer` | string | `auto`、`SGD`、`Adam`、`AdamW` |
| `lr0` | number | 0.000001～0.1 |
| `lrf` | number | 0.01～1.0 |
| `momentum` | number | 0.6～0.98 |
| `weight_decay` | number | 0～0.1 |
| `warmup_epochs` | number | 0～20 |
| `warmup_momentum` | number | 0～0.95 |
| `warmup_bias_lr` | number | 0～0.5 |
| `cos_lr` | boolean | `true`/`false` |
| `freeze` | integer | 0～100，表示冻结前 N 层 |
| `cache` | string | `false`、`disk`、`ram` |
| `workers` | integer | 0～16 |
| `seed` | integer | 0～2147483647 |
| `deterministic` | boolean | `true`/`false` |
| `amp` | boolean | `true`/`false` |
| `hsv_h` | number | 0～0.1 |
| `hsv_s`、`hsv_v` | number | 0～1 |
| `degrees` | number | 0～45 |
| `translate` | number | 0～0.5 |
| `scale` | number | 0～0.9 |
| `shear` | number | 0～10 |
| `perspective` | number | 0～0.001 |
| `flipud`、`fliplr` | number | 0～1 |
| `mosaic`、`mixup` | number | 0～1 |
| `close_mosaic` | integer | 0～任务 epochs |
| `box` | number | 0.02～20 |
| `cls` | number | 0.01～10 |
| `dfl` | number | 0.1～10 |

组合校验：

- `close_mosaic` 不能大于任务 epochs。
- `cache=ram` 必须展示内存风险确认，但后端不依赖确认字段决定安全性。
- 960 与 l/x 模型必须展示显存/速度风险确认。
- CPU 与 m/l/x 模型继续要求二次确认。
- `stable_finetune` 要求基础模型是实际存在的 `.pt` 权重。
- `workers>0` 在 Windows 显示页文件与多进程风险；服务端仍限制最大值 16。

## 7. 通用验收合同

验收配置使用严格 JSON：

```json
{
  "schema_version": 1,
  "overall": {
    "precision": null,
    "recall": null,
    "map50": 0.5,
    "map50_95": null
  },
  "classes": {
    "no_helmet": {
      "precision": 0.85,
      "recall": 0.9,
      "map50": null,
      "map50_95": null
    }
  }
}
```

规则：

- 每个门槛为 `null` 或 0～1 的有限数值。
- 至少配置一个非空门槛。
- `classes` 的键必须精确匹配任务数据集类别快照。
- 任务只有在所有非空门槛均满足时 `accepted=true`。
- 指标缺失时对应门槛失败，并记录稳定原因 `metric_missing`。
- 通用默认只启用总体 `map50=0.5`，用户可以修改。
- 安全帽验收模板要求数据集包含 `person`、`helmet`、`no_helmet`，默认总体 `map50=0.85`，并设置 `no_helmet precision=0.85`、`no_helmet recall=0.90`；`helmet` 可由用户添加独立门槛。
- 安全帽模板只是可选填充器；核心验收算法不识别特殊类名。

结果中保留总体和所有类别的 precision、recall、mAP50、mAP50-95。现有 `no_helmet_precision` 和 `no_helmet_recall` 兼容字段暂不删除；新逻辑以动态 `metrics_json` 为事实来源。

## 8. API 设计

### 8.1 Catalog

`GET /api/training/catalog` 增加：

- `config_schema_version`；
- 五种预设及版本；
- 高级字段的类型、范围、枚举、默认值和分组；
- 验收字段定义；
- 与数据集类别兼容的可选验收模板。

catalog 不返回本地绝对路径。

### 8.2 创建任务

`POST /api/training-jobs` 增加：

```json
{
  "mode": "advanced",
  "profile": "high_recall",
  "advanced_config": {},
  "acceptance_config": {}
}
```

- `mode` 仅允许 `basic` 或 `advanced`。
- 基础模式由后端生成均衡高级快照，不能绕过快照。
- 高级模式将所选预设与用户覆盖合并，再严格校验并保存完整值。
- 请求保持 `Idempotency-Key` 语义；同一个 key 的任何配置差异均返回 409。

### 8.3 复制模板

新增 `GET /api/training-jobs/{job_id}/clone-template`，仅管理员可用。响应为安全、已归一化的创建请求模板，不包含：

- 运行目录；
- checkpoint 绝对路径；
- PID；
- 本地日志路径；
- 内部存储 key。

用户编辑模板后仍调用普通创建任务接口。

### 8.4 续训

续训接口不接受高级或验收覆盖。新续训任务继承：

- 数据集与类别快照；
- 基础参数；
- profile；
- 完整高级配置；
- 完整验收配置。

需要改变任何配置时必须使用复制任务。

### 8.5 任务响应

任务详情增加解析后的 `training_profile`、`advanced_config`、`acceptance_config` 和逐项验收结果。列表响应只返回 profile 和关键配置摘要，避免传输过大的指标对象。

## 9. 数据库与迁移

在当前 0008 之后新增迁移，`training_job` 增加：

- `config_schema_version`：Integer，非空；
- `training_profile`：String(30)，非空；
- `advanced_config`：Text，非空；
- `acceptance_config`：Text，非空。

迁移步骤：

1. 以可空字段加入表；
2. 将历史任务回填为 schema 1、profile `legacy`；
3. 根据现有字段生成严格、完整的兼容高级配置；
4. 根据旧验收语义生成兼容验收配置；
5. 改为非空；
6. SQLite 使用 Alembic batch alter；
7. MySQL 离线 DDL 必须可生成完整升级/降级链。

JSON 继续以 Text 保存，序列化使用稳定排序和紧凑格式。ORM/API/worker 解析后均执行同一个严格合同，不能信任数据库中的任意 JSON。

## 10. Worker 与产物

`TrainingManifest` 增加 schema 版本和完整高级配置。worker：

1. 严格解析 manifest；
2. 将白名单字段逐一映射到 `YOLO.train`；
3. 固定 `project`、`name`、`save_dir`、data、model、resume checkpoint 和输出目录，前端配置不能覆盖这些安全字段；
4. 将解析后的最终参数写入任务目录的安全配置产物；
5. 保持现有 progress/result 原子写、1 MiB 上限、路径隔离和稳定错误码；
6. 在 test split 上收集动态逐类指标并执行任务验收合同。

恢复训练继续使用受控 `last.pt`，并继承原配置。复制任务从用户选择的基础模型或注册模型开始新实验，不伪装成 optimizer 状态续训。

## 11. 模型注册与激活

- 任意类别的 completed + best.pt 任务均可注册候选模型。
- 模型版本保存训练任务 ID、数据集版本/摘要、类别快照、输入尺寸、配置摘要、动态指标和验收结果。
- 注册时校验 checkpoint 类别名称/顺序与任务类别快照一致。
- 激活继续执行加载、合成图 warmup、数据库事务和 provider 回滚。
- 未达标模型允许管理员二次确认后激活，并显示失败门槛。
- 激活后多图识别和实时画面动态展示全部模型类别。
- 包含 `person`、`helmet`、`no_helmet` 时继续启用现有安全帽关联规则。
- 其他类别在第一阶段只检测和展示；通用实时告警规则由后续子项目实现。

## 12. 安全与错误处理

- 未知字段、NaN、Infinity、错误类型和越界数值在排队前返回 422。
- 参数组合冲突返回稳定错误 `training_config_invalid`，响应只含字段级安全原因。
- 预设不存在或版本不支持返回 `training_profile_invalid`。
- 验收类名不存在返回 `acceptance_class_unknown`。
- checkpoint、数据集和输出路径仍由服务端生成并执行 containment 校验。
- 日志和 API 不返回绝对路径、命令行、traceback、数据库诊断或敏感环境变量。
- 配置 JSON、结果 JSON 和日志 tail 均保持字节上限。
- 任务并发继续使用现有单机锁和数据库 scheduler 锁，不因高级模式增加第二个训练进程。

## 13. 向后兼容

- 原创建请求缺少新字段时按 `mode=basic` 处理。
- 历史任务读取为 `profile=legacy`，使用迁移生成的快照。
- 历史任务的 accepted 值不重新计算，避免指标展示随升级变化。
- 新任务只使用通用验收合同。
- 现有安全帽专用列保留一个兼容周期。
- 前端遇到未知未来 schema 版本时只读展示原始摘要，不允许复制或续训。

## 14. 测试与验收

### 14.1 后端

- SQLite migration upgrade/downgrade/re-upgrade；
- MySQL 8.0.13 离线完整 DDL；
- 预设精确展开；
- 每个字段的类型、边界、非有限数值和未知字段；
- `close_mosaic <= epochs` 等组合规则；
- 任意类别、安全帽模板、缺失指标和未知类别验收；
- 创建幂等、复制模板脱敏、续训不可覆盖；
- worker 精确 Ultralytics 参数映射；
- 动态 sparse class ID 指标提取；
- 注册类别一致性和激活回滚；
- 历史任务兼容读取。

### 14.2 前端

- 基础/高级模式切换；
- 五种预设及用户覆盖；
- 参数分组、范围和组合错误；
- 640/960、大模型、CPU、RAM cache 风险确认；
- 动态类别验收编辑；
- 动态指标表；
- 复制任务；
- 同 manifest 2～4 任务对比；
- API 错误的可访问提示。

### 14.3 集成门禁

- SQLite 后端全量；
- 前端全量、typecheck、production build；
- 真实已有模型和两张图片的推理回归；
- 一个 1 epoch 通用两/三类别训练冒烟；
- 一个任意非安全帽类别的 fake worker 合同用例；
- live MySQL、长时间正式训练和 Atlas 部署单独验收，不阻塞本地第一阶段实现。

## 15. 文档与交付

更新：

- 根目录 README；
- `docs/training-guide.md`；
- `.env.example`（仅新增必要配置时）；
- API schema/OpenAPI；
- 高级参数、预设、验收、复制/对比和常见问题说明。

交付不自动修改现有已发布训练集、不启动正式训练、不自动激活新模型。

## 16. 后续子项目：通用实时告警规则

高级训练完成后单独设计：

- 从当前激活模型类别中选择告警类别；
- 配置置信度、连续命中、时间窗口、冷却时间、ROI 和告警名称；
- 保留安全帽头部关联作为一种可选规则模板；
- 模型切换时校验规则类名兼容性；
- 不把该规则编辑器塞入本次训练实施计划。
