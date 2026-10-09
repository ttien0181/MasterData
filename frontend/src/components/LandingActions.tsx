"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getMe } from "@/lib/api";

export default function LandingActions({ compact = false }: { compact?: boolean }) {
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => { let active = true; void getMe().then(() => { if (active) setSignedIn(true); }).catch(() => undefined); return () => { active = false; }; }, []);
  if (compact) return signedIn ? <Link className="landing-nav-cta" href="/app">Vào ứng dụng ↗</Link> : <><Link className="landing-nav-login" href="/login">Đăng nhập</Link><Link className="landing-nav-cta" href="/register">Bắt đầu ↗</Link></>;
  return <div className="landing-actions"><Link className="landing-primary" href={signedIn ? "/app" : "/register"}>{signedIn ? "Vào không gian làm việc" : "Bắt đầu sử dụng"}<span>↗</span></Link><a className="landing-secondary" href="#quy-trinh">Khám phá quy trình <span>↓</span></a></div>;
}
