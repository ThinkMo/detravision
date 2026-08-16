import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LivePage } from "./LivePage";

const cameras = [
  {
    id: 1,
    name: "东门",
    rtsp_configured: true,
    enabled: true,
    sample_fps: 1,
    roi: [],
    detect_classes: ["no_helmet"],
    alarm_group_id: null,
    created_at: "2026-08-16T08:00:00Z",
  },
  {
    id: 2,
    name: "西门",
    rtsp_configured: true,
    enabled: false,
    sample_fps: 1,
    roi: [],
    detect_classes: ["no_helmet"],
    alarm_group_id: null,
    created_at: "2026-08-16T08:00:00Z",
  },
];

class FakeWebSocket {
  static readonly OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readonly url: string;
  binaryType = "";
  readyState = FakeWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn(() => {
    this.readyState = 3;
  });

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LivePage />
    </QueryClientProvider>,
  );
}

describe("LivePage", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(cameras), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => `blob:frame-${Math.random()}`),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("streams the selected enabled camera and releases every object URL", async () => {
    const view = renderPage();

    expect(await screen.findByRole("heading", { name: "实时监控" })).toBeInTheDocument();
    expect(screen.getByText("西门")).toBeInTheDocument();
    expect(screen.getByText("未启用")).toBeInTheDocument();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(FakeWebSocket.instances[0].url).toMatch(/^ws:\/\/.*\/ws\/live\/1$/);

    act(() => {
      FakeWebSocket.instances[0].onopen?.();
      FakeWebSocket.instances[0].onmessage?.(new MessageEvent("message", { data: new Blob(["first"]) }));
    });
    expect(await screen.findByRole("img", { name: "东门实时画面" })).toBeInTheDocument();

    act(() => {
      FakeWebSocket.instances[0].onmessage?.(new MessageEvent("message", { data: new Blob(["second"]) }));
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: /西门/ }));
    expect(screen.getByText("该摄像头尚未启用")).toBeInTheDocument();
    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
    expect(FakeWebSocket.instances[0].close).toHaveBeenCalled();
  });

  it("reconnects a disconnected selected camera with backoff", async () => {
    renderPage();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));

    act(() => FakeWebSocket.instances[0].onclose?.());
    expect(within(screen.getByLabelText("东门监控画面")).getAllByText("正在重连")).toHaveLength(2);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2), { timeout: 1_500 });
  });
});
