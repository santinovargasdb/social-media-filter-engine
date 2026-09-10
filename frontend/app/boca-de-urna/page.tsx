"use client";

import { useCallback, useState } from "react";
import UrnaParamsBar from "@/components/urna/UrnaParamsBar";
import DisclaimerBanner from "@/components/urna/DisclaimerBanner";
import { runBocaDeUrna, UrnaRequest, UrnaResponse, UrnaStatus } from "@/lib/urnaApi";
import SentimentBarChart from "@/components/urna/SentimentBarChart";
import EvidencePanel from "@/components/urna/EvidencePanel";
import ComparisonTable from "@/components/urna/ComparisonTable";

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
        {data && (
          <>
            {data.meta.warnings.length > 0 && (
              <ul style={{ margin: "0 0 16px", padding: "10px 14px 10px 30px", fontSize: "12px",
                borderRadius: "var(--radius-sm)", background: "rgba(127,127,127,0.08)", color: "var(--text-secondary)" }}>
                {data.meta.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            )}
            <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "12px" }}>
              {data.meta.total_posts} publicaciones analizadas · {data.meta.posts_electorales} electorales
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: "24px", alignItems: "start" }}>
              <section style={{ display: "flex", flexDirection: "column", gap: "20px", minWidth: 0 }}>
                <div>
                  <h3 style={{ fontSize: "14px", marginBottom: "10px" }}>Sentimiento neto en redes</h3>
                  <SentimentBarChart candidatos={data.candidatos} />
                </div>
                <div>
                  <h3 style={{ fontSize: "14px", marginBottom: "10px" }}>Evidencia (citas)</h3>
                  <EvidencePanel evidencia={data.evidencia} />
                </div>
              </section>
              <section style={{ minWidth: 0 }}>
                <h3 style={{ fontSize: "14px", marginBottom: "10px" }}>Redes vs consultoras</h3>
                <ComparisonTable comparacion={data.comparacion} />
              </section>
            </div>
          </>
        )}
      </div>
    </main>
  );
}
