"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, login, register, setCsrfToken } from "@/lib/api";
import { setActiveImportUser } from "@/lib/activeImport";
import ThemeToggle from "./ThemeToggle";

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const isRegister = mode === "register";

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (isRegister) await register(email, password);
      const user = await login(email, password);
      setCsrfToken(user.csrf_token);
      setActiveImportUser(user.id);
      router.replace("/");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Không thể kết nối máy chủ. Hãy thử lại.");
    } finally {
      setBusy(false);
    }
  }

  return <main className="auth-page">
    <ThemeToggle className="auth-theme-toggle" />
    <div className="auth-card">
      <div className="auth-brand"><span className="navbar-logo">M</span><strong>Master<span>One</span></strong></div>
      <p className="auth-eyebrow">QUẢN LÝ DỮ LIỆU CHỦ</p>
      <h1>{isRegister ? "Tạo tài khoản" : "Chào mừng trở lại"}</h1>
      <p className="auth-description">{isRegister ? "Tạo không gian làm việc riêng cho dữ liệu khách hàng của bạn." : "Đăng nhập để tiếp tục duyệt và chuẩn hóa hồ sơ."}</p>
      <form onSubmit={(event) => void submit(event)}>
        <label htmlFor="email">Email</label>
        <input id="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="ban@congty.vn" />
        <label htmlFor="password">Mật khẩu</label>
        <input id="password" type="password" autoComplete={isRegister ? "new-password" : "current-password"} minLength={12} maxLength={128} required value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Ít nhất 12 ký tự" />
        {error && <p className="auth-error" role="alert">{error}</p>}
        <button className="button primary auth-submit" type="submit" disabled={busy}>{busy ? "Đang xử lý…" : isRegister ? "Tạo tài khoản" : "Đăng nhập"}</button>
      </form>
      <p className="auth-switch">{isRegister ? "Đã có tài khoản?" : "Chưa có tài khoản?"} <Link href={isRegister ? "/login" : "/register"}>{isRegister ? "Đăng nhập" : "Đăng ký"}</Link></p>
    </div>
  </main>;
}
