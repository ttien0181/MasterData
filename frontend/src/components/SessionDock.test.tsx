import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SessionDock from "./SessionDock";

describe("SessionDock", () => {
  it("keeps all three decision views inside one step in the requested order", () => {
    const onNavigate = vi.fn();
    render(<SessionDock stage="review" pendingCount={500} mergeCount={20} canReadAudit canEditPolicy={false} onNavigate={onNavigate} />);

    const group = screen.getByRole("group", { name: "Bước 2: Xử lý đề xuất" });
    const options = within(group).getAllByRole("button");
    expect(options.map((button) => button.textContent)).toEqual(["Không gộp", "Cần xem xét", "Chờ gộp"]);
    expect(options[1]).toHaveAttribute("aria-current", "page");

    fireEvent.click(options[0]);
    fireEvent.click(options[2]);
    expect(onNavigate).toHaveBeenNthCalledWith(1, "no_merge");
    expect(onNavigate).toHaveBeenNthCalledWith(2, "auto");
    expect(screen.getByRole("button", { name: "Lịch sử xuất" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lịch sử thao tác" })).toBeInTheDocument();
  });
});
