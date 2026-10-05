"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import UrnaParamsBar from "@/components/urna/UrnaParamsBar";
import DisclaimerBanner from "@/components/urna/DisclaimerBanner";
import {
  fetchUrnaHistorial, runBocaDeUrnaAsync,
  UrnaHistorialPunto, UrnaRequest, UrnaResponse, UrnaProgress,
} from "@/lib/urnaApi";
import SentimentBarChart from "@/components/urna/SentimentBarChart";
import EvidencePanel from "@/components/urna/EvidencePanel";
import ComparisonTable from "@/components/urna/ComparisonTable";
import EvolutionChart from "@/components/urna/EvolutionChart";
import InfoTip from "@/components/urna/InfoTip";
import WarningsDetails from "@/components/urna/WarningsDetails";

const DEFAULT_DISCLAIMER =
  "Este indicador refleja el clima de conversación en redes sociales sobre publicaciones públicas indexadas. No es una muestra representativa del electorado ni una proyección de resultado electoral. Sirve como termómetro direccional, complementario a las encuestas de consultoras.";

function fmtFecha(iso: string): string {
  const d = new Date(iso);
  // hour12 explícito: según el navegador, es-AR puede salir 12h sin "p.m."
  // y "16:00" se lee como "04:00" de la madrugada.
  return isNaN(d.getTime()) ? iso : d.toLocaleString("es-AR", { hour12: false });
}

export default function BocaDeUrnaPage() {
  const [data, setData] = useState<UrnaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<UrnaProgress | null>(null);
  const [historial, setHistorial] = useState<UrnaHistorialPunto[]>([]);
  const [avisosOpen, setAvisosOpen] = useState(false);
  const avisosRef = useRef<HTMLDetailsElement>(null);

  const abrirAvisos = useCallback(() => {
    setAvisosOpen(true);
    // Esperar el render del <details open> antes de scrollear.
    requestAnimationFrame(() =>
      avisosRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }));
  }, []);

  // La evolución sale de los snapshots guardados: se carga al entrar, sin
  // necesidad de correr un análisis (fetchUrnaHistorial devuelve [] si falla).
  useEffect(() => { fetchUrnaHistorial().then(setHistorial); }, []);

  const run = useCallback(async (req: UrnaRequest) => {
    setLoading(true); setError(null); setProgress({ phase: "Iniciando…", pct: 0 }); setData(null); setAvisosOpen(false);
    try {
      const res = await runBocaDeUrnaAsync(req, (p: UrnaProgress) => setProgress(p));
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al conectar con el servidor");
    } finally { setLoading(false); setProgress(null); }
  }, []);

  return (
    <main style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 60px)" }}>
      <UrnaParamsBar loading={loading} onRun={run} />
      <div style={{ flex: 1, overflow: "auto", padding: "20px 24px" }}>
        <DisclaimerBanner texto={data?.meta.disclaimer || DEFAULT_DISCLAIMER} />
        {loading && progress && (
          <div style={{ padding: "12px 16px", borderRadius: "var(--radius-sm)", marginBottom: "16px",
            background: "rgba(59,130,246,0.1)", border: "1px solid rgba(59,130,246,0.3)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "13px", color: "#93C5FD", marginBottom: "6px" }}>
              <span>⏳ {progress.phase}</span>
              <span>{Math.round(progress.pct)}%</span>
            </div>
            <div style={{ height: "8px", background: "rgba(148,163,184,0.25)", borderRadius: "4px", overflow: "hidden" }}>
              <div style={{ width: `${Math.max(3, progress.pct)}%`, height: "100%", background: "#3B82F6", transition: "width 0.4s ease" }} />
            </div>
            <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "6px" }}>
              El análisis corre en segundo plano y puede tardar 2-4 minutos. No cierres esta pestaña.
            </div>
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
            <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center", marginBottom: "12px" }}>
              {data.meta.ultima_actualizacion && (
                <span style={{ fontSize: "12px", color: "var(--text-secondary)",
                  display: "inline-flex", alignItems: "center", gap: "6px", padding: "4px 10px",
                  borderRadius: "var(--radius-sm)", background: "rgba(127,127,127,0.08)" }}>
                  🕒 Última actualización: {fmtFecha(data.meta.ultima_actualizacion)}
                </span>
              )}
              {data.meta.warnings.length > 0 && (
                <button type="button" onClick={abrirAvisos}
                  style={{ fontSize: "12px", color: "var(--text-secondary)", cursor: "pointer",
                    display: "inline-flex", alignItems: "center", gap: "6px", padding: "4px 10px",
                    borderRadius: "var(--radius-sm)", background: "rgba(127,127,127,0.08)",
                    border: "1px solid var(--border-color)" }}>
                  ⓘ {data.meta.warnings.length} aviso{data.meta.warnings.length === 1 ? "" : "s"}
                </button>
              )}
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "12px" }}>
              {data.meta.total_posts} publicaciones analizadas · {data.meta.posts_electorales} electorales
              {data.meta.bloques && data.meta.bloques.length > 0 && (
                <span>
                  {" · por bloque: "}
                  {data.meta.bloques
                    .map((b) => `${({ twitter: "X", instagram: "IG", tiktok: "TikTok" } as Record<string, string>)[b.red] || b.red} ${b.encontrados}`)
                    .join(" · ")}
                </span>
              )}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: "24px", alignItems: "start" }}>
              <section style={{ display: "flex", flexDirection: "column", gap: "20px", minWidth: 0 }}>
                <div>
                  <h3 style={{ fontSize: "14px", marginBottom: "10px" }}>Menciones y sentimiento en redes</h3>
                  <SentimentBarChart candidatos={data.candidatos} />
                </div>
                <div>
                  <h3 style={{ fontSize: "14px", marginBottom: "10px" }}>Evidencia (citas)</h3>
                  <EvidencePanel evidencia={data.evidencia} />
                </div>
              </section>
              <section style={{ minWidth: 0 }}>
                <h3 style={{ fontSize: "14px", marginBottom: "10px",
                  display: "flex", alignItems: "center", gap: "7px" }}>
                  Redes vs consultoras
                  <InfoTip label="Qué significan estos números">
                    <strong>Apoyo en redes:</strong> de cada 100 menciones positivas que
                    encontramos, cuántas se lleva cada candidato. Es lo comparable con la
                    intención de voto de una encuesta.
                    <br /><br />
                    <strong>Columnas de consultoras:</strong> lo que midió cada una. El número
                    entre paréntesis es la <em>brecha</em> con redes, en puntos.
                    <br /><br />
                    <strong>Colores de la brecha:</strong>{" "}
                    <span style={{ color: "#4CAF50" }}>verde</span> = coinciden (≤3 pts),{" "}
                    <span style={{ color: "#FFC107" }}>amarillo</span> = moderada (≤8),{" "}
                    <span style={{ color: "#F87171" }}>rojo</span> = grande (&gt;8).
                    <br /><br />
                    Es un termómetro de conversación en redes, <strong>no una encuesta</strong>.
                  </InfoTip>
                </h3>
                <ComparisonTable comparacion={data.comparacion}
                                 basePositivas={data.candidatos.reduce((s, c) => s + c.pos, 0)} />
              </section>
            </div>
          </>
        )}
        {historial.length >= 2 && (
          <div style={{ marginTop: "28px" }}>
            <h3 style={{ fontSize: "14px", marginBottom: "4px" }}>Evolución de la conversación</h3>
            <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginBottom: "10px" }}>
              Share de menciones por candidato en cada corrida del scraper (2-3 por día).
              Elegí un día arriba y «Analizar» para ver el detalle de esa fecha.
            </div>
            <EvolutionChart historial={historial} />
          </div>
        )}
        {data && (
          <WarningsDetails ref={avisosRef} warnings={data.meta.warnings}
                           open={avisosOpen} onToggle={setAvisosOpen} />
        )}
      </div>
    </main>
  );
}
