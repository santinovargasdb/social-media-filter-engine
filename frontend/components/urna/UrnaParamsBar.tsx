"use client";

import { useState } from "react";
import type { UrnaRequest } from "@/lib/urnaApi";

interface Props {
  loading: boolean;
  onRun: (req: UrnaRequest) => void;
}

// Término de búsqueda FIJO por decisión institucional: este motor analiza la
// conversación electoral y nada más (el usuario no lo edita).
const KEYWORDS_FIJOS = ["elecciones presidenciales"];

// Hoy en fecha LOCAL (toISOString pelado es UTC: en Argentina mostraría "mañana"
// después de las 21:00).
function hoyISO(): string {
  return new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10);
}

export default function UrnaParamsBar({ loading, onRun }: Props) {
  const [country, setCountry] = useState("ar");
  const [date, setDate] = useState("");
  const hoy = hoyISO();
  const [csvText, setCsvText] = useState("");
  const [csvName, setCsvName] = useState("");
  const [csvRows, setCsvRows] = useState(0);
  const [autoConsultoras, setAutoConsultoras] = useState(false);
  // El selector de redes se quitó de la UI: en modo "stored" (el de producción) el
  // backend sirve el snapshot que dejó el scraper con TODAS las redes que corrió, e
  // ignora esta lista — tener checkboxes que no filtran nada confundía. Mandamos las
  // tres igual por si algún día se reactiva el modo en vivo (ahí sí se consultan).
  const ALL_NETWORKS = ["twitter", "instagram", "tiktok"];

  const handleCsv = (file: File | null) => {
    if (!file) { setCsvText(""); setCsvName(""); setCsvRows(0); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      setCsvText(text);
      setCsvName(file.name);
      // filas de datos = líneas no vacías menos el header
      setCsvRows(Math.max(0, text.split(/\r?\n/).filter((l) => l.trim()).length - 1));
    };
    reader.readAsText(file);
  };

  const submit = () => {
    onRun({
      keywords: KEYWORDS_FIJOS,
      networks: ALL_NETWORKS,
      // Doble cerrojo contra fechas futuras (el max del input se puede tipear por encima).
      date: date && date <= hoy ? date : null,
      country: country.trim().toLowerCase() || "ar",
      pollster_csv: csvText,
      auto_consultoras: autoConsultoras,
    });
  };

  const field: React.CSSProperties = {
    padding: "8px 10px", fontSize: "13px", borderRadius: "var(--radius-sm)",
    border: "1px solid var(--border-color)", background: "var(--bg-secondary)", color: "var(--text-primary)",
  };

  return (
    <div style={{
      display: "flex", flexWrap: "wrap", gap: "10px", alignItems: "center",
      padding: "12px 24px", borderBottom: "1px solid var(--border-color)", background: "var(--bg-secondary)",
    }}>
      <span style={{ ...field, flex: "1 1 260px", border: "1px dashed var(--border-color)",
                     color: "var(--text-secondary)", cursor: "default", userSelect: "none" }}
            title="Tema fijo del motor: solo analiza la conversación electoral">
        Buscando: &ldquo;elecciones presidenciales&rdquo;
      </span>
      <input style={{ ...field, width: "70px" }} value={country}
             onChange={(e) => setCountry(e.target.value)} placeholder="país" title="Código ISO (ar, br, ...)" />
      <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px", color: "var(--text-secondary)" }}
           title="Ver el análisis guardado de ese día (vacío = el último disponible)">
        <span>Día:</span>
        <input style={{ ...field, width: "150px" }} type="date" value={date} max={hoy}
               onChange={(e) => setDate(e.target.value && e.target.value > hoy ? hoy : e.target.value)} />
      </div>
      <label style={{ ...field, cursor: "pointer", color: "var(--smata-green-light, #4CAF50)" }}>
        ⬆ CSV consultoras
        <input type="file" accept=".csv" style={{ display: "none" }}
               onChange={(e) => handleCsv(e.target.files?.[0] || null)} />
      </label>
      {csvName && <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{csvName} · {csvRows} filas</span>}
      <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px",
                      color: "var(--text-secondary)", cursor: "pointer" }}>
        <input type="checkbox" checked={autoConsultoras}
               onChange={(e) => setAutoConsultoras(e.target.checked)} />
        ⟳ Traer consultoras automáticamente
      </label>
      <button className="btn" disabled={loading} onClick={submit}
              style={{ background: "var(--smata-green-mid, #2E7D32)", color: "#fff", padding: "8px 16px",
                       fontSize: "13px", opacity: loading ? 0.6 : 1 }}>
        {loading ? "Analizando…" : "Analizar"}
      </button>
    </div>
  );
}
