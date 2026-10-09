"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import ResolutionWorkspace from "@/components/ResolutionWorkspace";
import { clearActiveImport, setActiveImportUser } from "@/lib/activeImport";
import { getMe, logout, setCsrfToken, type AuthUser } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    void getMe().then((found) => {
      setCsrfToken(found.csrf_token);
      setActiveImportUser(found.id);
      setUser(found);
    }).catch(() => router.replace("/login")).finally(() => setLoading(false));
  }, [router]);

  if (loading || !user) return <main className="auth-page"><div className="auth-card auth-loading" role="status"><span className="loading-symbol">M</span><span className="loading-line" /><span className="loading-line short" /><p>Đang kiểm tra phiên đăng nhập…</p></div></main>;
  return <ResolutionWorkspace user={user} onLogout={async () => {
    await logout();
    clearActiveImport();
    window.localStorage.removeItem(`masterone.resolution-run.v1.${user.id}`);
    setActiveImportUser("");
    setCsrfToken("");
    router.replace("/login");
  }} />;
}
