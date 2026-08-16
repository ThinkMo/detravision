import { render, screen } from "@testing-library/react";
import { App } from "./App";

it("renders the Safety Detect application root", () => {
  render(<App />);
  expect(screen.getByText("正在加载站点配置")).toBeInTheDocument();
});
