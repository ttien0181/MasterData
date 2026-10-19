"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, getBackendHealth, login, register, setCsrfToken } from "@/lib/api";
import { setActiveImportUser } from "@/lib/activeImport";
import MoMark from "./MoMark";
import ThemeToggle from "./ThemeToggle";

type HealthState = "checking" | "online" | "unavailable";
type AuthAction = "form" | "demo";

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [activeAction, setActiveAction] = useState<AuthAction | null>(null);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [health, setHealth] = useState<HealthState>("checking");
  const isRegister = mode === "register";

  useEffect(() => {
    let active = true;
    void getBackendHealth().then((result) => {
      if (active) setHealth(result.status === "ok" ? "online" : "unavailable");
    }).catch(() => {
      if (active) setHealth("unavailable");
    });
    return () => { active = false; };
  }, []);

  async function authenticate(action: AuthAction) {
    if (busyRef.current) return;
    busyRef.current = true;
    setActiveAction(action);
    setError("");
    try {
      const loginEmail = action === "demo" ? "demo@gmail.com" : email;
      const loginPassword = action === "demo" ? "demopassword" : password;
      if (action === "form" && isRegister) await register(loginEmail, loginPassword);
      const user = await login(loginEmail, loginPassword);
      setCsrfToken(user.csrf_token);
      setActiveImportUser(user.id);
      router.replace("/app");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Không thể kết nối máy chủ. Hãy thử lại.");
    } finally {
      busyRef.current = false;
      setActiveAction(null);
    }
  }

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void authenticate("form");
  }

  const healthLabel = health === "online" ? "Hệ thống hoạt động" : health === "checking" ? "Đang kiểm tra hệ thống" : "Không thể kết nối hệ thống";

  return <main className="auth-page">
    <div className="auth-frame">
      <section className="auth-story" aria-labelledby="auth-story-title">
        <Link className="auth-brand" href="/" aria-label="MasterOne — trang chủ">
          <MoMark size={42} /><strong>Master<span>One</span></strong>
        </Link>
        <div className="auth-story-copy">
          <p className="auth-story-eyebrow">PHÂN GIẢI DỮ LIỆU CÓ KIỂM SOÁT</p>
          <h1 id="auth-story-title">Một nguồn dữ liệu.<br /><span>Một sự thật.</span></h1>
          <p>Đối chiếu dữ liệu khách hàng từ nhiều nguồn, xem bằng chứng và kiểm soát từng quyết định.</p>
        </div>
        <div className="auth-illustration" role="img" aria-label="Mo kết nối các nguồn CRM, ERP và Billing với Golden Record">
          <div className="auth-source-stack" aria-hidden="true">
            <span><i className="source-dot crm" />CRM</span>
            <span><i className="source-dot erp" />ERP</span>
            <span><i className="source-dot bill" />Billing</span>
          </div>
          <svg className="auth-connectors" viewBox="0 0 440 220" fill="none" aria-hidden="true">
            <path d="M94 48C150 48 150 108 200 108M94 110H198M94 172C150 172 150 112 200 112M252 110C300 110 303 76 350 76" stroke="currentColor" strokeWidth="2" strokeDasharray="5 6" />
            <circle cx="200" cy="110" r="5" fill="currentColor" /><circle cx="350" cy="76" r="5" fill="currentColor" />
          </svg>
          <MoMark size={150} />
          <div className="auth-golden-card"><span>GOLDEN RECORD</span><strong>Hồ sơ đã đối chiếu</strong><i aria-hidden="true">✓</i></div>
        </div>
        <div className="auth-trust-list">
          <div><span aria-hidden="true">◇</span><p><strong>Phân quyền</strong><small>Theo vai trò và năng lực</small></p></div>
          <div><span aria-hidden="true">✓</span><p><strong>Kiểm soát</strong><small>Duyệt trước khi xuất dữ liệu</small></p></div>
          <div><span aria-hidden="true">⌁</span><p><strong>Truy vết</strong><small>Ghi nhận quyết định xử lý</small></p></div>
        </div>
      </section>

      <section className="auth-panel" aria-labelledby="auth-title">
        <div className="auth-panel-top">
          <div className={`auth-health is-${health}`} role="status" aria-live="polite">
            <i aria-hidden="true" />{healthLabel}
          </div>
          <ThemeToggle className="auth-theme-toggle" />
        </div>
        <div className="auth-form-content">
          <p className="auth-eyebrow">MASTERONE WORKSPACE</p>
          <h2 id="auth-title">{isRegister ? "Tạo tài khoản" : "Đăng nhập"}</h2>
          <p className="auth-description">{isRegister ? "Tạo tài khoản để bắt đầu làm việc với dữ liệu của bạn." : "Truy cập không gian xử lý dữ liệu của bạn."}</p>
          <form onSubmit={submit}>
            <label htmlFor="email">Email</label>
            <input className="auth-input" id="email" type="email" autoComplete="email" required value={email}
              onChange={(event) => setEmail(event.target.value)} placeholder="ten.ban@congty.com" />
            <label htmlFor="password">Mật khẩu</label>
            <div className="auth-password-field">
              <input className="auth-input" id="password" type={passwordVisible ? "text" : "password"}
                autoComplete={isRegister ? "new-password" : "current-password"} minLength={12} maxLength={128} required
                value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Ít nhất 12 ký tự" />
              <button className="auth-password-toggle" type="button" aria-label={passwordVisible ? "Ẩn mật khẩu" : "Hiện mật khẩu"}
                aria-pressed={passwordVisible} onClick={() => setPasswordVisible((visible) => !visible)}>
                {passwordVisible ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.2A10.8 10.8 0 0 1 12 5c5 0 8.5 4.5 9.5 7-.4 1-1.2 2.1-2.3 3.1M6.2 6.3C3.9 7.8 2.8 10 2.5 12c1 2.5 4.5 7 9.5 7 1.1 0 2.1-.2 3-.6" /></svg>
                  : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12s3.5-7 9.5-7 9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7Z" /><circle cx="12" cy="12" r="2.5" /></svg>}
              </button>
            </div>
            {error && <p className="auth-error" role="alert">{error}</p>}
            {!isRegister && <button className="auth-secondary-link" type="button" disabled title="Chức năng khôi phục mật khẩu chưa được cấu hình">
              Quên mật khẩu?
            </button>}
            <button className="button primary auth-submit" type="submit" disabled={activeAction !== null}>
              {activeAction === "form" ? "Đang xác thực…" : isRegister ? "Tạo tài khoản" : "Đăng nhập"}
              <span aria-hidden="true">→</span>
            </button>
          </form>
          <div className="auth-divider" aria-hidden="true">Hoặc</div>
          <button className="auth-demo-button" type="button" disabled={activeAction !== null}
            onClick={() => void authenticate("demo")}>
            {activeAction === "demo" ? "Đang vào tài khoản demo…" : "Dùng tài khoản demo"}
          </button>
          <p className="auth-switch">{isRegister ? "Đã có tài khoản?" : "Chưa có tài khoản?"} <Link href={isRegister ? "/login" : "/register"}>{isRegister ? "Đăng nhập" : "Đăng ký"}</Link></p>
        </div>
        <footer className="auth-footer">© 2026 MasterOne <span>·</span> Đối chiếu và gộp dữ liệu</footer>
      </section>
    </div>
  </main>;
}
