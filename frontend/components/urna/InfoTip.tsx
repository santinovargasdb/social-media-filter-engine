"use client";

import { useEffect, useRef, useState } from "react";

/** ⓘ circulado que abre un popover flotante con `children`. Se cierra con
 *  click afuera o Escape. Sin librerías: posicionamiento absoluto simple. */
export default function InfoTip({ children, label = "Más información" }:
  { children: React.ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span ref={wrap} style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "16px", height: "16px", borderRadius: "50%", flexShrink: 0,
          border: "1px solid var(--border-color)", background: "transparent",
          color: "var(--text-secondary)", fontSize: "11px", fontWeight: 700,
          lineHeight: 1, cursor: "pointer", padding: 0,
          display: "inline-flex", alignItems: "center", justifyContent: "center",
        }}
      >
        i
      </button>
      {open && (
        <div
          role="dialog"
          style={{
            position: "absolute", top: "22px", left: 0, zIndex: 20, width: "280px",
            padding: "12px 14px", borderRadius: "var(--radius-md)",
            background: "var(--bg-card)", border: "1px solid var(--border-color)",
            boxShadow: "0 6px 24px rgba(0,0,0,0.18)",
            fontSize: "12px", lineHeight: 1.5, color: "var(--text-primary)",
            fontWeight: 400, textTransform: "none", letterSpacing: "normal",
          }}
        >
          {children}
        </div>
      )}
    </span>
  );
}
