import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("API client", () => {
  it("sends JSON mutations with same-origin credentials", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.request<{ ok: boolean }>("/api/example", {
      method: "POST",
      body: JSON.stringify({ value: 1 }),
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/example");
    if (!init) throw new Error("fetch init was omitted");
    expect(init.credentials).toBe("same-origin");
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  });

  it("maps an unauthorized response to AuthExpiredError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "authentication required" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(api.request("/api/auth/me")).rejects.toEqual(
      expect.objectContaining({
        name: "AuthExpiredError",
        message: "authentication required",
        status: 401,
      }),
    );
  });
});
