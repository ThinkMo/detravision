import { FormEvent, useMemo, useState } from "react";
import type { TrainingAcceptanceConfig, TrainingAcceptanceMetric, TrainingAdvancedConfig, TrainingAdvancedFieldDescriptor, TrainingAdvancedValue, TrainingCatalog, TrainingJobCreate, TrainingProfileCatalogItem } from "../../api/types";
import { ConfirmDialog, ModalDialog } from "../shared";

const DEFAULTS = { executor: "local", imgsz: 640, epochs: 100, batch: "auto", device: "auto", patience: 50 } as const;
const METRIC_LABELS: Record<TrainingAcceptanceMetric, string> = { precision: "精确率", recall: "召回率", map50: "mAP50", map50_95: "mAP50-95" };
const FALLBACK_ACCEPTANCE: TrainingAcceptanceConfig = { schema_version: 1, overall: { map50: .5 }, classes: {} };

function cloneAcceptance(value?: TrainingAcceptanceConfig): TrainingAcceptanceConfig {
  return value ? JSON.parse(JSON.stringify(value)) as TrainingAcceptanceConfig : { ...FALLBACK_ACCEPTANCE, overall: { ...FALLBACK_ACCEPTANCE.overall }, classes: {} };
}

function catalogProfiles(catalog: TrainingCatalog): TrainingProfileCatalogItem[] {
  return catalog.training_profiles?.length ? catalog.training_profiles : [{ id: "balanced", label: "均衡", description: "通用默认基线", recommended: { imgsz: 640, epochs: 100, patience: 50 }, overrides: {} }];
}

function parseChoice(value: string, choices: TrainingAdvancedValue[]): TrainingAdvancedValue {
  return choices.find((choice) => String(choice) === value) ?? value;
}

function AdvancedField({ descriptor, value, onChange }: { descriptor: TrainingAdvancedFieldDescriptor; value: TrainingAdvancedValue | ""; onChange: (value: TrainingAdvancedValue | "") => void }) {
  if (descriptor.type === "string" && descriptor.choices?.length) {
    const choices = descriptor.choices ?? [];
    return <label>{descriptor.label}<select aria-label={descriptor.label} value={String(value)} onChange={(event) => onChange(parseChoice(event.target.value, choices))}>{choices.map((choice) => <option key={String(choice)} value={String(choice)}>{choice === false || choice === "false" ? "关闭" : choice === true || choice === "true" ? "开启" : String(choice)}</option>)}</select><small>{descriptor.help}</small></label>;
  }
  if (descriptor.type === "boolean") return <label className="admin-check"><input type="checkbox" aria-label={descriptor.label} checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />{descriptor.label}<small>{descriptor.help}</small></label>;
  return <label>{descriptor.label}<input aria-label={descriptor.label} type="number" min={descriptor.min} max={descriptor.max} step={descriptor.type === "integer" ? 1 : "any"} value={value as number | ""} onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))} /><small>{descriptor.help}</small></label>;
}

function AcceptanceFields({ catalog, datasetClasses, templateId, value, onTemplate, onChange }: { catalog: TrainingCatalog; datasetClasses: string[]; templateId: string; value: TrainingAcceptanceConfig; onTemplate: (id: string) => void; onChange: (value: TrainingAcceptanceConfig) => void }) {
  const schema = catalog.acceptance_schema;
  const metrics = schema?.metrics ?? (["precision", "recall", "map50", "map50_95"] as TrainingAcceptanceMetric[]);
  const setMetric = (scope: "overall" | string, metric: TrainingAcceptanceMetric, raw: string) => {
    const threshold = raw === "" ? null : Number(raw);
    if (scope === "overall") onChange({ ...value, overall: { ...value.overall, [metric]: threshold } });
    else onChange({ ...value, classes: { ...value.classes, [scope]: { ...value.classes[scope], [metric]: threshold } } });
  };
  const toggleClass = (className: string, enabled: boolean) => {
    const classes = { ...value.classes };
    if (enabled) classes[className] = {};
    else delete classes[className];
    onChange({ ...value, classes });
  };
  return <fieldset className="training-acceptance"><legend>通用验收规则</legend>
    {schema?.templates.length ? <label>验收模板<select aria-label="验收模板" value={templateId} onChange={(event) => onTemplate(event.target.value)}>{templateId === "" ? <option value="">复制的自定义验收</option> : null}{schema.templates.map((template) => { const compatible = template.required_classes.every((item) => datasetClasses.includes(item)); return <option key={template.id} value={template.id} disabled={!compatible}>{template.label}{compatible ? "" : "（类别不兼容）"}</option>; })}</select></label> : null}
    <div className="training-advanced-grid">{metrics.map((metric) => <label key={metric}>总体 {METRIC_LABELS[metric]}<input aria-label={`总体 ${METRIC_LABELS[metric]}`} type="number" min={0} max={1} step="0.01" value={value.overall[metric] ?? ""} onChange={(event) => setMetric("overall", metric, event.target.value)} /></label>)}</div>
    <div className="training-class-thresholds">{datasetClasses.map((className) => { const enabled = Object.hasOwn(value.classes, className); return <section key={className}><label className="admin-check"><input type="checkbox" aria-label={`启用 ${className} 类别验收`} checked={enabled} onChange={(event) => toggleClass(className, event.target.checked)} /><strong>{className}</strong></label>{enabled ? <div className="training-advanced-grid">{metrics.map((metric) => <label key={metric}>{METRIC_LABELS[metric]}<input aria-label={`${className} ${METRIC_LABELS[metric]}`} type="number" min={0} max={1} step="0.01" value={value.classes[className]?.[metric] ?? ""} onChange={(event) => setMetric(className, metric, event.target.value)} /></label>)}</div> : null}</section>; })}</div>
  </fieldset>;
}

function validateAdvanced(catalog: TrainingCatalog, values: Record<string, TrainingAdvancedValue | "">, epochs: number, acceptance: TrainingAcceptanceConfig): string[] {
  const errors: string[] = [];
  for (const [name, descriptor] of Object.entries(catalog.advanced_config_schema?.fields ?? {})) {
    const value = values[name];
    if ((descriptor.type === "number" || descriptor.type === "integer") && (value === "" || typeof value !== "number" || !Number.isFinite(value))) errors.push(`${descriptor.label}必须填写有效数字`);
    else if (typeof value === "number" && descriptor.min != null && value < descriptor.min) errors.push(`${descriptor.label}必须在 ${descriptor.min} 到 ${descriptor.max} 之间`);
    else if (typeof value === "number" && descriptor.max != null && value > descriptor.max) errors.push(`${descriptor.label}必须在 ${descriptor.min} 到 ${descriptor.max} 之间`);
  }
  if (typeof values.close_mosaic === "number" && values.close_mosaic > epochs) errors.push("关闭 Mosaic 轮次不能大于训练轮数");
  if ([acceptance.overall, ...Object.values(acceptance.classes)].some((scope) => Object.values(scope).some((item) => item != null && (item < 0 || item > 1)))) errors.push("验收门槛必须在 0 到 1 之间");
  return errors;
}

export function TrainingJobDialog({ catalog, initialRequest, pending, error, onClose, onSubmit }: { catalog: TrainingCatalog; initialRequest?: TrainingJobCreate | null; pending: boolean; error: string; onClose: () => void; onSubmit: (value: TrainingJobCreate) => void }) {
  const availableModels = catalog.models.filter((model) => model.available);
  const preferred = availableModels.some((model) => model.id === "yolo11m.pt") ? "yolo11m.pt" : availableModels[0]?.id ?? "";
  const profiles = catalogProfiles(catalog);
  const balanced = profiles.find((item) => item.id === "balanced") ?? profiles[0];
  const genericTemplate = catalog.acceptance_schema?.templates.find((item) => item.id === "generic") ?? catalog.acceptance_schema?.templates[0];
  const [form, setForm] = useState<Omit<TrainingJobCreate, "advanced_config" | "acceptance_config">>(() => initialRequest ? (({ advanced_config: _advanced, acceptance_config: _acceptance, ...base }) => base)(initialRequest) : { ...DEFAULTS, dataset_key: catalog.default_dataset_key ?? "", base_model: preferred, mode: "basic", profile: "balanced", acceptance_template: genericTemplate?.id ?? null });
  const [advancedOverrides, setAdvancedOverrides] = useState<Record<string, TrainingAdvancedValue | "">>(() => Object.fromEntries(Object.entries(initialRequest?.advanced_config ?? {}).filter((entry): entry is [string, TrainingAdvancedValue] => entry[1] !== undefined)));
  const [acceptance, setAcceptance] = useState(() => cloneAcceptance(initialRequest?.acceptance_config ?? genericTemplate?.config));
  const [confirmRisk, setConfirmRisk] = useState<"cpu" | "heavy" | "ram" | null>(null);
  const selectedProfile = profiles.find((item) => item.id === form.profile) ?? balanced;
  const selectedDataset = catalog.datasets.find((item) => item.key === form.dataset_key);
  const size = useMemo(() => form.base_model.replace(".pt", "").slice(-1), [form.base_model]);
  const resolvedAdvanced = useMemo(() => {
    const defaults = Object.fromEntries(Object.entries(catalog.advanced_config_schema?.fields ?? {}).map(([name, field]) => [name, field.default]));
    return { ...defaults, ...selectedProfile?.overrides, ...advancedOverrides };
  }, [advancedOverrides, catalog.advanced_config_schema, selectedProfile]);
  const validationErrors = form.mode === "advanced" ? validateAdvanced(catalog, resolvedAdvanced, form.epochs, acceptance) : [];
  const updateProfile = (profile: TrainingJobCreate["profile"]) => {
    const item = profiles.find((candidate) => candidate.id === profile);
    setAdvancedOverrides({});
    setForm({ ...form, profile, imgsz: item?.recommended.imgsz ?? form.imgsz, epochs: item?.recommended.epochs ?? form.epochs, patience: item?.recommended.patience ?? form.patience });
  };
  const updateTemplate = (id: string) => {
    const template = catalog.acceptance_schema?.templates.find((item) => item.id === id);
    setForm({ ...form, acceptance_template: id });
    setAcceptance(cloneAcceptance(template?.config));
  };
  const payload = (): TrainingJobCreate => ({ ...form, advanced_config: form.mode === "advanced" ? advancedOverrides as Partial<TrainingAdvancedConfig> : {}, acceptance_config: acceptance });
  const risk = () => form.device === "cpu" && ["m", "l", "x"].includes(size) ? "cpu" : resolvedAdvanced.cache === "ram" ? "ram" : form.imgsz === 960 || ["l", "x"].includes(size) ? "heavy" : null;
  const submit = (event?: FormEvent) => { event?.preventDefault(); if (validationErrors.length) return; const nextRisk = risk(); if (nextRisk && !confirmRisk) setConfirmRisk(nextRisk); else onSubmit(payload()); };

  if (confirmRisk) {
    const copy = confirmRisk === "cpu" ? { title: "确认使用 CPU 训练", label: "仍然开始", text: "中型及以上模型使用 CPU 训练可能需要很长时间，建议使用 GPU。" } : confirmRisk === "ram" ? { title: "确认使用内存缓存", label: "确认并开始", text: "RAM 图片缓存可能占用大量系统内存，请确认服务器资源充足。" } : { title: "确认高负载训练", label: "确认并开始", text: "960 输入或 l/x 模型会明显增加显存占用并降低训练和部署速度。" };
    return <ConfirmDialog title={copy.title} confirmLabel={copy.label} pending={pending} error={error} onCancel={() => setConfirmRisk(null)} onConfirm={() => onSubmit(payload())}><p>{copy.text}</p></ConfirmDialog>;
  }

  const advancedAvailable = Boolean(catalog.advanced_config_schema && catalog.training_profiles?.length);
  return <ModalDialog title="新建本地训练" className="training-form--wide" onClose={onClose}><form className="admin-form training-form" onSubmit={submit}>
    <div className="training-mode-tabs" role="tablist" aria-label="训练配置模式"><button type="button" role="tab" aria-selected={form.mode === "basic"} className={form.mode === "basic" ? "is-active" : ""} onClick={() => setForm({ ...form, mode: "basic", profile: "balanced" })}>基础模式</button><button type="button" role="tab" aria-selected={form.mode === "advanced"} className={form.mode === "advanced" ? "is-active" : ""} disabled={!advancedAvailable} onClick={() => setForm({ ...form, mode: "advanced" })}>高级模式</button></div>
    {!advancedAvailable ? <p className="training-form__status">当前服务端仅支持基础模式。</p> : null}
    <div className="training-basic-grid">
      <label>运行位置<select aria-label="运行位置" value="local" onChange={() => undefined}>{catalog.executors.map((executor) => <option key={executor.id} value={executor.id} disabled={!executor.enabled}>{executor.id === "local" ? "本地训练" : "远程训练（暂未开放）"}</option>)}</select></label>
      <label>训练集<select aria-label="训练集" required value={form.dataset_key} onChange={(event) => setForm({ ...form, dataset_key: event.target.value })}><option value="">请选择训练集</option>{catalog.datasets.map((dataset) => <option key={dataset.key} value={dataset.key} disabled={!dataset.available}>{dataset.name}{dataset.available ? "" : "（不可用）"}</option>)}</select></label>
      <label>基础模型<select aria-label="基础模型" required value={form.base_model} onChange={(event) => setForm({ ...form, base_model: event.target.value })}>{catalog.models.map((model) => <option key={model.id} value={model.id} disabled={!model.available}>{model.id}{model.available ? "" : "（权重缺失）"}</option>)}</select></label>
      <label>输入尺寸<select aria-label="输入尺寸" value={form.imgsz} onChange={(event) => setForm({ ...form, imgsz: Number(event.target.value) as 640 | 960 })}><option value={640}>640</option><option value={960}>960</option></select></label>
      <label>训练轮数<input aria-label="训练轮数" type="number" min={1} max={1000} value={form.epochs} onChange={(event) => setForm({ ...form, epochs: Number(event.target.value) })} /></label>
      <label>批次<select aria-label="批次" value={form.batch} onChange={(event) => setForm({ ...form, batch: event.target.value as TrainingJobCreate["batch"] })}>{["auto", "1", "2", "4", "8"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
      <label>设备<select aria-label="设备" value={form.device} onChange={(event) => setForm({ ...form, device: event.target.value as TrainingJobCreate["device"] })}><option value="auto">自动</option><option value="0">GPU 0</option><option value="cpu">CPU</option></select></label>
      <label>早停耐心值<input aria-label="早停耐心值" type="number" min={0} max={500} value={form.patience} onChange={(event) => setForm({ ...form, patience: Number(event.target.value) })} /></label>
    </div>
    {form.mode === "advanced" ? <div className="training-advanced">
      <fieldset><legend>训练预设</legend><label>训练预设<select aria-label="训练预设" value={form.profile} onChange={(event) => updateProfile(event.target.value as TrainingJobCreate["profile"])}>{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.label} — {profile.description}</option>)}</select></label><p>{selectedProfile?.description}</p></fieldset>
      {catalog.advanced_config_schema?.groups.map((group) => <fieldset key={group.id}><legend>{group.label}</legend><div className="training-advanced-grid">{group.fields.map((name) => { const descriptor = catalog.advanced_config_schema?.fields[name]; return descriptor ? <AdvancedField key={name} descriptor={descriptor} value={resolvedAdvanced[name] ?? ""} onChange={(value) => setAdvancedOverrides({ ...advancedOverrides, [name]: value })} /> : null; })}</div></fieldset>)}
      <AcceptanceFields catalog={catalog} datasetClasses={selectedDataset?.classes ?? []} templateId={form.acceptance_template ?? ""} value={acceptance} onTemplate={updateTemplate} onChange={setAcceptance} />
      <details className="training-config-summary"><summary>最终配置摘要</summary><dl><div><dt>预设</dt><dd>{selectedProfile?.label}</dd></div><div><dt>Schema</dt><dd>v{catalog.config_schema_version ?? 1}</dd></div><div><dt>自定义参数</dt><dd>{Object.keys(advancedOverrides).length} 项</dd></div><div><dt>类别门槛</dt><dd>{Object.keys(acceptance.classes).length} 类</dd></div>{Object.entries(resolvedAdvanced).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>)}</dl></details>
    </div> : null}
    {form.imgsz === 960 || ["l", "x"].includes(size) ? <p className="training-warning">此组合更重，显存占用、训练和部署时间会明显增加，提交前需要确认。</p> : null}
    {resolvedAdvanced.cache === "ram" && form.mode === "advanced" ? <p className="training-warning">RAM 缓存可能占用大量系统内存，提交前需要确认。</p> : null}
    {validationErrors.length ? <div className="admin-dialog__error" role="alert"><strong>请修正以下配置：</strong><ul>{validationErrors.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
    {validationErrors.length ? <p className="training-form__status">修正高级参数后才能开始训练。</p> : null}
    {error ? <p className="admin-dialog__error" role="alert">{error}</p> : null}
    <footer><button type="button" className="secondary-button" disabled={pending} onClick={onClose}>取消</button><button type="submit" disabled={pending || !form.dataset_key || !form.base_model || validationErrors.length > 0}>{pending ? "正在创建" : "开始训练"}</button></footer>
  </form></ModalDialog>;
}
