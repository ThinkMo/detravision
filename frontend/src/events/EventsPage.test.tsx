import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { EventsPage } from "./EventsPage";

const camera = {
  id: 3,
  name: "装卸区",
  rtsp_configured: true,
  enabled: true,
  sample_fps: 1,
  roi: [],
  detect_classes: ["no_helmet"],
  alarm_group_id: null,
  created_at: "2026-08-16T08:00:00Z",
};

const event = {
  id: 9,
  camera_id: 3,
  event_type: "no_helmet",
  confidence: 0.92,
  bbox: [1, 2, 3, 4],
  snapshot_url: "/api/events/9/snapshot",
  status: "new",
  ack_by: null,
  ack_user_id: null,
  created_at: "2026-08-16T08:30:00Z",
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EventsPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("filters events without rendering private paths and acknowledges an event", async () => {
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    requests.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/cameras") {
      return new Response(JSON.stringify([camera]), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (path.startsWith("/api/events") && init?.method === "PATCH") {
      return new Response(JSON.stringify({ ...event, status: "ack", ack_user_id: 7 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (path.startsWith("/api/events")) {
      return new Response(JSON.stringify([event]), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  }));

  renderPage();
  expect(await screen.findByRole("heading", { name: "事件中心" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "装卸区" })).toBeInTheDocument();
  expect(screen.queryByText("private/local/snapshot.jpg")).not.toBeInTheDocument();

  await userEvent.selectOptions(screen.getByLabelText("状态"), "new");
  await userEvent.click(screen.getByRole("button", { name: "筛选" }));
  await waitFor(() => expect(requests.some((request) => request.includes("status=new"))).toBe(true));

  await userEvent.click(screen.getByRole("button", { name: "查看事件 #9" }));
  expect(screen.getByRole("img", { name: "事件 #9 抓拍" })).toHaveAttribute("src", "/api/events/9/snapshot");
  await userEvent.click(screen.getByRole("button", { name: "确认事件" }));
  expect(await within(screen.getByLabelText("事件 #9 详情")).findByText("已确认")).toBeInTheDocument();
  expect(requests).toContain("PATCH /api/events/9");
});

it("rolls back an optimistic close when the API rejects it", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/cameras") {
      return new Response(JSON.stringify([camera]), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (init?.method === "PATCH") {
      return new Response(JSON.stringify({ message: "事件状态更新失败" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify([event]), { status: 200, headers: { "Content-Type": "application/json" } });
  }));

  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "查看事件 #9" }));
  await userEvent.click(screen.getByRole("button", { name: "关闭事件" }));
  expect(await screen.findByRole("status")).toHaveTextContent("事件状态更新失败");
  expect(within(screen.getByLabelText("事件 #9 详情")).getByText("待处理")).toBeInTheDocument();
});
