import type { UrnaCandidato } from "@/lib/urnaApi";

export default function SentimentBarChart({ candidatos }: { candidatos: UrnaCandidato[] }) {
  if (!candidatos.length) return <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>Sin candidatos detectados.</p>;
  const max = Math.max(...candidatos.map((c) => c.pct), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {candidatos.map((c) => (
        <div key={c.nombre} title={`A favor: ${c.pos} · En contra: ${c.neg} · Neutro: ${c.neu} · Menciones: ${c.menciones}`}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "13px", marginBottom: "3px" }}>
            <span style={{ fontWeight: 600 }}>{c.nombre}</span>
            <span style={{ color: "var(--text-secondary)" }}>{c.pct}%</span>
          </div>
          <div style={{ height: "14px", background: "rgba(127,127,127,0.15)", borderRadius: "7px", overflow: "hidden" }}>
            <div style={{ width: `${(c.pct / max) * 100}%`, height: "100%",
              background: "linear-gradient(90deg, var(--smata-green-mid, #2E7D32), var(--smata-green-light, #4CAF50))" }} />
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "2px" }}>
            {c.pos} a favor · {c.neg} en contra · {c.neu} neutro · {c.menciones} menciones
          </div>
        </div>
      ))}
    </div>
  );
}
