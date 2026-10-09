import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AuthForm from "./AuthForm";
import { getBackendHealth, login, setCsrfToken } from "@/lib/api";
import { setActiveImportUser } from "@/lib/activeImport";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/link", () => ({ default: ({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => <a href={href} {...props}>{children}</a> }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error { status = 0; },
  getBackendHealth: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  setCsrfToken: vi.fn(),
}));
vi.mock("@/lib/activeImport", () => ({ setActiveImportUser: vi.fn() }));

describe("authentication presentation", () => {
  beforeEach(() => {
    vi.mocked(getBackendHealth).mockResolvedValue({ status: "ok" });
    vi.mocked(login).mockResolvedValue({ id: "user-1", email: "person@example.test", csrf_token: "csrf", role: "STEWARD", capabilities: ["read"] });
    replace.mockReset();
  });
  afterEach(() => { cleanup(); vi.clearAllMocks(); localStorage.clear(); });

  it("keeps login available when the informational health probe fails", async () => {
    vi.mocked(getBackendHealth).mockRejectedValue(new Error("health unavailable"));
    render(<AuthForm mode="login" />);

    expect(await screen.findByRole("status")).toHaveTextContent("Không thể kết nối hệ thống");
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "person@example.test" } });
    fireEvent.change(screen.getByLabelText("Mật khẩu"), { target: { value: "a-secure-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Đăng nhập" }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("person@example.test", "a-secure-password"));
    expect(setCsrfToken).toHaveBeenCalledWith("csrf");
    expect(setActiveImportUser).toHaveBeenCalledWith("user-1");
    expect(replace).toHaveBeenCalledWith("/app");
  });

  it("provides an accessible password visibility control and labels unavailable auth options", () => {
    render(<AuthForm mode="login" />);
    const password = screen.getByLabelText("Mật khẩu");
    expect(password).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Hiện mật khẩu" }));
    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Quên mật khẩu?" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Đăng nhập bằng SSO/ })).toBeDisabled();
  });
});
