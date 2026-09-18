"use client";

import { useState } from "react";
import type { UrnaRequest } from "@/lib/urnaApi";

interface Props {
  loading: boolean;
  onRun: (req: UrnaRequest) => void;
}

export default function UrnaParamsBar({ loading, onRun }: Props) {
  const [keywords, setKeywords] = useState("elecciones presidenciales");
  const [country, setCountry] = useState("ar");
  const [date, setDate] = useState("");
  const [csvText, setCsvText] = useState("");
  const [csvName, setCsvName] = useState("");
  const [csvRows, setCsvRows] = useState(0);
  const [autoConsultoras, setAutoConsultoras] = useState(false);
  // X viene tildado por defecto (es la red con datos hoy vía scraping); IG/TikTok
  // quedan disponibles para cuando tengan su scraper.
  const [networks, setNetworks] = useState<string[]>(["twitter"]);

  const NETWORKS: { id: string; label: string }[] = [
    { id: "twitter", label: "X" },
    { id: "instagram", label: "IG" },
    { id: "tiktok", label: "TikTok" },
  ];
  const toggleNetwork = (id: string) =>
    setNetworks((cur) => (cur.includes(id) ? cur.filter((n) => n !== id) : [...cur, id]));

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
      keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean),
      networks: networks.length ? networks : ["twitter"],
      date: date || null,
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
      <input style={{ ...field, flex: "1 1 260px" }} value={keywords}
             onChange={(e) => setKeywords(e.target.value)} placeholder="Términos (coma-separados)" />
      <input style={{ ...field, width: "70px" }} value={country}
             onChange={(e) => setCountry(e.target.value)} placeholder="país" title="Código ISO (ar, br, ...)" />
      <input style={{ ...field, width: "150px" }} type="date" value={date}
             onChange={(e) => setDate(e.target.value)} title="Desde" />
      <div style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "13px", color: "var(--text-secondary)" }}
           title="Redes a analizar">
        <span>Redes:</span>
        {NETWORKS.map((n) => (
          <label key={n.id} style={{ display: "flex", alignItems: "center", gap: "4px", cursor: "pointer" }}>
            <input type="checkbox" checked={networks.includes(n.id)} onChange={() => toggleNetwork(n.id)} />
            {n.label}
          </label>
        ))}
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
