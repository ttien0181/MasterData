"use client";

import { useEffect, useState } from "react";

export default function ThemeToggle({ className = "" }: { className?: string }) {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const update = () => setDark(document.documentElement.dataset.theme === "dark");
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const followSystem = () => {
      if (!window.localStorage.getItem("masterone.theme")) {
        document.documentElement.dataset.theme = media?.matches ? "dark" : "light";
        update();
      }
    };
    update();
    media?.addEventListener("change", followSystem);
    return () => media?.removeEventListener("change", followSystem);
  }, []);

  function toggle() {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    window.localStorage.setItem("masterone.theme", next);
    setDark(next === "dark");
  }

  return <button className={`icon-button theme-toggle ${className}`} type="button"
    aria-label={dark ? "Bật giao diện sáng" : "Bật giao diện tối"} aria-pressed={dark} onClick={toggle}>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {dark ? <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" /></>
        : <path d="M20.5 14.1A8.5 8.5 0 0 1 9.9 3.5 8.5 8.5 0 1 0 20.5 14.1Z" />}
    </svg>
  </button>;
}
