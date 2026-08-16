import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { App } from "./App";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it("renders the Safety Detect application root", () => {
  render(<App />);
  expect(screen.getByText("正在加载站点配置")).toBeInTheDocument();
});
