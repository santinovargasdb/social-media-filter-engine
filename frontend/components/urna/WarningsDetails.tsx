"use client";

import { forwardRef } from "react";

/** Desplegable al fondo de la página con los avisos técnicos de la corrida.
 *  Controlado desde afuera (`open`/`onToggle`) para que el chip de arriba
 *  pueda abrirlo y scrollear hasta acá. Cerrado por defecto. */
const WarningsDetails = forwardRef<HTMLDetailsElement,
  { warnings: string[]; open: boolean; onToggle: (open: boolean) => void }>(
  function WarningsDetails({ warnings, open, onToggle }, ref) {
    if (!warnings.length) return null;
    return (
      <details
        ref={ref}
        open={open}
        onToggle={(e) => onToggle((e.currentTarget as HTMLDetailsElement).open)}
        style={{
          marginTop: "28px", padding: "10px 14px", borderRadius: "var(--radius-sm)",
          background: "rgba(127,127,127,0.06)", border: "1px solid var(--border-color)",
          fontSize: "12px", color: "var(--text-secondary)",
        }}
      >
        <summary style={{ cursor: "pointer", userSelect: "none", fontWeight: 600 }}>
          ⓘ Avisos de la corrida ({warnings.length})
        </summary>
        <ul style={{ margin: "10px 0 0", padding: "0 0 0 20px" }}>
          {warnings.map((w, i) => (
            <li key={i} style={{ marginBottom: "4px", whiteSpace: "pre-wrap" }}>{w}</li>
          ))}
        </ul>
        <p style={{ margin: "10px 0 0", fontStyle: "italic" }}>
          Son detalles técnicos del scraper (lecturas salteadas, límites de cuota del
          clasificador, etc.). No afectan la validez del termómetro: el análisis se
          generó con las publicaciones que sí se procesaron.
        </p>
      </details>
    );
  });

export default WarningsDetails;
