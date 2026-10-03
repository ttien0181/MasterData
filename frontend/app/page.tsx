"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import ResolutionWorkspace from "@/components/ResolutionWorkspace";
import { getMe, logout, setCsrfToken, type AuthUser } from "@/lib/api";
import { clearActiveImport, setActiveImportUser } from "@/lib/activeImport";

function resolutionRunKey(userId: string) {
  return `masterone.resolution-run.v1.${userId}`;
}

export default function Home() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    void getMe()
      .then((found) => {
        if (!active) return;
        setCsrfToken(found.csrf_token);
        setActiveImportUser(found.id);
        setUser(found);
      })
      .catch(() => {
        if (active) router.replace("/login");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [router]);

  async function handleLogout() {
    await logout();
    clearActiveImport();
    if (user) {
      window.localStorage.removeItem(resolutionRunKey(user.id));
    }
    setActiveImportUser("");
    setCsrfToken("");
    router.replace("/login");
  }

  if (loading || !user) {
    return (
      <main className="auth-page">
        <div className="auth-card auth-loading" role="status">
          <span className="loading-symbol">M</span>
          <span className="loading-line" />
          <span className="loading-line short" />
          <p>Đang kiểm tra phiên đăng nhập…</p>
        </div>
      </main>
    );
  }

  return <ResolutionWorkspace user={user} onLogout={handleLogout} />;
}
