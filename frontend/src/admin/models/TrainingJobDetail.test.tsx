import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TrainingJobDetail } from "./TrainingJobDetail";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("shows observed optimizer and AutoBatch separately from requested configuration", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input) => {
    if (String(input).endsWith("/effective_config")) return Response.json({ runtime: {
      batch: 4, device: "cuda:0", epoch: 1, versions: { ultralytics: "8.4.120" },
      advanced_config: { lr0: 0.001429 },
      optimizer: { name: "AdamW", param_groups: [{ initial_lr: 0.001429, lr: 0.0001 }] },
    } });
    return Response.json({ id: 1, base_model: "yolo11n.pt", epochs: 100, imgsz: 640,
      batch: "auto", device: "auto", patience: 50, status: "completed",
      advanced_config: { optimizer: "auto", lr0: 0.01 },
      effective_config_url: "/api/training-jobs/1/artifacts/effective_config" });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><TrainingJobDetail jobId={1} onClose={vi.fn()} /></QueryClientProvider>);
  expect(await screen.findByText("AdamW")).toBeInTheDocument();
  expect(screen.getByText("实际初始学习率")).toBeInTheDocument();
  expect(screen.getByText("0.001429")).toBeInTheDocument();
  expect(screen.getByText("0.01")).toBeInTheDocument();
  expect(screen.getByText("cuda:0")).toBeInTheDocument();
});
