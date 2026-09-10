"use client";

import { useCallback, useState } from "react";
import UrnaParamsBar from "@/components/urna/UrnaParamsBar";
import DisclaimerBanner from "@/components/urna/DisclaimerBanner";
import { runBocaDeUrna, UrnaRequest, UrnaResponse, UrnaStatus } from "@/lib/urnaApi";

const DEFAULT_DISCLAIMER =
  "Este indicador refleja el clima de conversación en redes sociales sobre publicaciones públicas indexadas. No es una muestra representativa del electorado ni una proyección de resultado electoral. Sirve como termómetro direccional, complementario a las encuestas de consultoras.";

export default function BocaDeUrnaPage() {
  const [data, setData] = useState<UrnaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const run = useCallback(async (req: UrnaRequest) => {
    setLoading(true); setError(null); setStatus(null); setData(null);
    try {
      const res = await runBocaDeUrna(req, (s: UrnaStatus) => setStatus(
        s === "waking" ? "El servidor estaba en reposo. Despertándolo… puede tardar ~40s." : "Conectando…"));
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al conectar con el servidor");
    } finally { setLoading(false); setStatus(null); }
  }, []);

  return (
    <main style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 60px)" }}>
      <UrnaParamsBar loading={loading} onRun={run} />
      <div style={{ flex: 1, overflow: "auto", padding: "20px 24px" }}>
        <DisclaimerBanner texto={data?.meta.disclaimer || DEFAULT_DISCLAIMER} />
        {loading && status && (
          <div style={{ padding: "12px 16px", borderRadius: "var(--radius-sm)",
            background: "rgba(59,130,246,0.1)", border: "1px solid rgba(59,130,246,0.3)", color: "#93C5FD", fontSize: "13px" }}>
            ⏳ {status}
          </div>
        )}
        {error && (
          <div style={{ padding: "12px 16px", borderRadius: "var(--radius-sm)",
            background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", color: "#F87171", fontSize: "13px" }}>
            ⚠️ {error}
          </div>
        )}
        {data && <pre style={{ fontSize: "12px", overflow: "auto" }}>{JSON.stringify(data, null, 2)}</pre>}
      </div>
    </main>
  );
}
