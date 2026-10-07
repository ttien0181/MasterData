import type { ReactNode } from "react";

export type ShellIconName = "menu" | "close" | "expand" | "collapse" | "overview" | "import" | "review" | "auto" | "no_merge" | "history" | "audit" | "chevron" | "logout" | "file" | "check" | "warning" | "replace" | "search";

export function ShellIcon({ name, size = 18 }: { name: ShellIconName; size?: number }) {
  const paths: Record<ShellIconName, ReactNode> = {
    menu: <path d="M4 7h16M4 12h16M4 17h16" />,
    close: <path d="M5 5l14 14M19 5L5 19" />,
    no_merge: <><path d="M8 4v16M16 4v16M3 12h5m8 0h5" /><path d="m5 9-3 3 3 3m14-6 3 3-3 3" /></>,
    expand: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M3 8l6-6m12 6-6-6M3 16l6 6m12-6-6 6" />,
    collapse: <path d="M8 3v5H3m13-5v5h5M3 16h5v5m13-5h-5v5" />,
    overview: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    import: <path d="M12 16V4m-5 5 5-5 5 5M4 20h16" />,
    file: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6" /></>,
    check: <path d="m5 12 5 5L19 7" />,
    warning: <path d="m12 3 10 18H2zM12 9v5m0 3h.01" />,
    replace: <path d="M20 7h-5m5 0V2M4 17h5m-5 0v5M5 8a8 8 0 0 1 13-3l2 2M4 17l2 2a8 8 0 0 0 13-3" />,
    search: <><circle cx="11" cy="11" r="7" /><path d="m16 16 5 5" /></>,
    review: <><path d="M4 8V5a1 1 0 0 1 1-1h3m8 0h3a1 1 0 0 1 1 1v3M4 16v3a1 1 0 0 0 1 1h3m8 0h3a1 1 0 0 0 1-1v-3" /><circle cx="12" cy="12" r="3" /></>,
    auto: <><path d="M3 6h7l4 6 4-6h3M3 18h7l4-6 4 6h3" /><path d="m18 3 3 3-3 3m0 6 3 3-3 3" /></>,
    history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5m4-1v5l3 2" /></>,
    audit: <><path d="M8 4h10a2 2 0 0 1 2 2v14H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><path d="M8 3h6v4H8zM9 12h7m-7 4h7" /><path d="m7 12 1 1 2-2m-3 5 1 1 2-2" /></>,
    chevron: <path d="m9 18 6-6-6-6" />,
    logout: <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4m4 4 5 5-5 5m5-5H9" />,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
