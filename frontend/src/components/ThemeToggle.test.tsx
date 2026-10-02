import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import ThemeToggle from "./ThemeToggle";

afterEach(() => {
  cleanup();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

it("shows the saved theme and persists a manual change", () => {
  document.documentElement.dataset.theme = "dark";
  localStorage.setItem("masterone.theme", "dark");
  render(<ThemeToggle />);
  const toggle = screen.getByRole("button", { name: "Bật giao diện sáng" });
  fireEvent.click(toggle);
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(localStorage.getItem("masterone.theme")).toBe("light");
  expect(toggle).toHaveAccessibleName("Bật giao diện tối");
});
