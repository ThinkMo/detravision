import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { TrainingCatalog } from "../../api/types";
import { TrainingJobDialog } from "./TrainingJobDialog";

const catalog: TrainingCatalog = {
  models: [{ id: "yolo11n.pt", series: "11", size: "n", available: true }],
  executors: [{ id: "local", enabled: true }],
  dataset: { id: "configured", available: false, has_test_split: false, classes: [] },
  datasets: [], default_dataset_key: null,
  base_defaults: { base_model: "yolo11n.pt", imgsz: 640, epochs: 200, batch: "auto", device: "auto", patience: 60 },
  training_profiles: [{ id: "balanced", label: "均衡", description: "通用基线", recommended: { imgsz: 640, epochs: 100, patience: 50 }, overrides: {} }],
  advanced_config_schema: { groups: [{ id: "optimizer", label: "优化器与学习率", fields: ["optimizer", "lr0", "momentum", "warmup_bias_lr"] }], fields: {
    optimizer: { label: "优化器", type: "string", default: "auto", choices: ["auto", "AdamW"], help: "" },
    lr0: { label: "初始学习率", type: "number", default: 0.01, help: "" },
    momentum: { label: "动量", type: "number", default: 0.937, help: "" },
    warmup_bias_lr: { label: "偏置预热学习率", type: "number", default: 0.1, help: "" },
  } },
};

afterEach(cleanup);

it("uses server defaults and disables ignored auto parameters until manual optimization is selected", async () => {
  render(<TrainingJobDialog catalog={catalog} pending={false} error="" onClose={vi.fn()} onSubmit={vi.fn()} />);
  expect(screen.getByLabelText("训练轮数")).toHaveValue(200);
  expect(screen.getByLabelText("早停耐心值")).toHaveValue(60);
  await userEvent.click(screen.getByRole("tab", { name: "高级模式" }));
  expect(screen.getByLabelText("初始学习率")).toBeDisabled();
  expect(screen.getByLabelText("动量")).toBeDisabled();
  expect(screen.getByLabelText("偏置预热学习率")).toBeDisabled();
  await userEvent.selectOptions(screen.getByLabelText("优化器"), "AdamW");
  expect(screen.getByLabelText("初始学习率")).toBeEnabled();
  expect(screen.getByLabelText("偏置预热学习率")).toHaveValue(0);
});
