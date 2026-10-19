import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AuthForm from "./AuthForm";
import { getBackendHealth, login, register, setCsrfToken } from "@/lib/api";
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

  it("provides an accessible password visibility control", () => {
    render(<AuthForm mode="login" />);
    const password = screen.getByLabelText("Mật khẩu");
    expect(password).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Hiện mật khẩu" }));
    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Quên mật khẩu?" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /SSO/ })).not.toBeInTheDocument();
  });

  it.each(["login", "register"] as const)("logs in with the demo account from the %s page", async (mode) => {
    render(<AuthForm mode={mode} />);

    fireEvent.click(screen.getByRole("button", { name: "Dùng tài khoản demo" }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("demo@gmail.com", "demopassword"));
    expect(register).not.toHaveBeenCalled();
    expect(setCsrfToken).toHaveBeenCalledWith("csrf");
    expect(setActiveImportUser).toHaveBeenCalledWith("user-1");
    expect(replace).toHaveBeenCalledWith("/app");
  });

  it("prevents a repeated demo login while the first request is pending", async () => {
    let finishLogin: ((user: Awaited<ReturnType<typeof login>>) => void) | undefined;
    vi.mocked(login).mockImplementation(() => new Promise((resolve) => { finishLogin = resolve; }));
    render(<AuthForm mode="login" />);

    fireEvent.click(screen.getByRole("button", { name: "Dùng tài khoản demo" }));
    expect(screen.getByRole("button", { name: "Đang vào tài khoản demo…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Đăng nhập" })).toBeDisabled();
    expect(login).toHaveBeenCalledTimes(1);

    finishLogin?.({ id: "user-1", email: "demo@gmail.com", csrf_token: "csrf", role: "STEWARD", capabilities: ["read"] });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/app"));
  });
});
