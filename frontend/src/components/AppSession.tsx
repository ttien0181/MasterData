"use client";
// Adapted from IamNTN AppSession; retain the existing authenticated shell/theme.
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { getMe, logout, setCsrfToken, type AuthUser } from '@/lib/api';
import { clearActiveImport, setActiveImportUser } from '@/lib/activeImport';
import { clearSessionReviewQueues } from '@/lib/pairReviewQueue';
import ShellTopBar from './ShellTopBar';
import { SessionCacheProvider } from './SessionCache';
const UserContext = createContext<AuthUser | null>(null);
export function useAppUser() { const user = useContext(UserContext); if (!user) throw new Error('Authenticated app context required'); return user; }
export default function AppSession({ children }: { children: ReactNode }) {
  const router = useRouter(); const pathname = usePathname();
  const [user, setUser] = useState<AuthUser | null>(null); const [error, setError] = useState(''); const [attempt, setAttempt] = useState(0);
  useEffect(() => { let active = true;
    void getMe().then(found => { if (active) { setCsrfToken(found.csrf_token); setActiveImportUser(found.id); setUser(found); setError(''); } })
      .catch(cause => { if (!active) return; if (cause?.status === 401) router.replace('/login'); else setError('Chưa kiểm tra được phiên đăng nhập.'); });
    return () => { active = false; };
  }, [router, attempt]);
  async function signOut() { try { await logout(); if (user) clearSessionReviewQueues(user.id); clearActiveImport(); setActiveImportUser(''); setCsrfToken(''); setUser(null); router.replace('/login'); } catch { setError('Chưa đăng xuất được. Hãy thử lại.'); } }
  if (!user) return <main className="auth-page"><div className="auth-card" role="status">{error || 'Đang kiểm tra phiên đăng nhập…'}{error && <button onClick={() => setAttempt(attempt + 1)}>Thử lại</button>}</div></main>;
  return <UserContext.Provider value={user}><SessionCacheProvider userId={user.id} onAuthLost={() => { setUser(null); setCsrfToken(''); setActiveImportUser(''); router.replace('/login'); }}><div className="workflow-app">
    {!pathname.startsWith('/app/legacy') && <ShellTopBar currentLabel="Tổng quan" user={user} onLogout={() => void signOut()} onOpenSessions={() => router.push('/app')} />}
    {error && <p role="alert">{error}</p>}{children}
  </div></SessionCacheProvider></UserContext.Provider>;
}
