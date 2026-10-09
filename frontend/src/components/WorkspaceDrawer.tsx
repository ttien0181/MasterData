"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { ShellIcon } from "./ShellIcons";

export default function WorkspaceDrawer({ title, subtitle, side = "left", open = true, expanded, onToggleExpanded, onClose, children }: {
  title: string;
  subtitle?: string;
  side?: "left" | "right";
  open?: boolean;
  expanded: boolean;
  onToggleExpanded: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function closeOnEscape(event: KeyboardEvent) { if (event.key === "Escape") onClose(); }
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [open, onClose]);

  return <div className="workspace-drawer-layer" hidden={!open}>
    <button className="workspace-drawer-backdrop" type="button" aria-label="Đóng bảng bên" onClick={onClose} />
    <section className={`workspace-drawer from-${side}${expanded ? " is-expanded" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
      <header className="workspace-drawer-heading">
        <div><h2>{title}</h2>{subtitle && <p className="workspace-drawer-subtitle">{subtitle}</p>}</div>
        <div className="workspace-drawer-actions">
          <button type="button" className="workspace-drawer-icon" aria-label={expanded ? "Thu nhỏ bảng bên" : "Phóng to toàn màn hình"}
            aria-pressed={expanded} title={expanded ? "Thu nhỏ bảng bên" : "Phóng to toàn màn hình"} onClick={onToggleExpanded}>
            <ShellIcon name={expanded ? "collapse" : "expand"} size={19} />
          </button>
          <button ref={closeRef} type="button" className="workspace-drawer-icon" aria-label="Đóng bảng bên" onClick={onClose}>
            <ShellIcon name="close" size={19} />
          </button>
        </div>
      </header>
      <div className="workspace-drawer-content">{children}</div>
    </section>
  </div>;
}
