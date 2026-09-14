import type { UrnaCandidato } from "@/lib/urnaApi";

// La barra de cada candidato mide su SHARE DE MENCIONES (cuánto se habla de él),
// y se pinta segmentada por sentimiento: a favor (verde) / neutro (gris) / en
// contra (rojo). Así se ven TODOS los candidatos detectados con su composición,
// en vez de colapsar a uno solo como pasaba con el sentimiento neto.
const COLORS = {
  favor: "var(--smata-green-light, #4CAF50)",
  neutro: "rgba(148,163,184,0.7)",
  contra: "#E5534B",
};

function Legend() {
  const item = (color: string, label: string) => (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}>
      <span style={{ width: "10px", height: "10px", borderRadius: "2px", background: color, display: "inline-block" }} />
      {label}
    </span>
  );
  return (
    <div style={{ display: "flex", gap: "14px", fontSize: "11px", color: "var(--text-secondary)", marginBottom: "10px" }}>
      {item(COLORS.favor, "A favor")}
      {item(COLORS.neutro, "Neutro")}
      {item(COLORS.contra, "En contra")}
    </div>
  );
}

export default function SentimentBarChart({ candidatos }: { candidatos: UrnaCandidato[] }) {
  if (!candidatos.length) return <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>Sin candidatos detectados.</p>;
  const max = Math.max(...candidatos.map((c) => c.pct), 1);
  return (
    <div>
      <Legend />
      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        {candidatos.map((c) => (
          <div key={c.nombre} title={`A favor: ${c.pos} · En contra: ${c.neg} · Neutro: ${c.neu} · Menciones: ${c.menciones}`}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "13px", marginBottom: "3px" }}>
              <span style={{ fontWeight: 600 }}>{c.nombre}</span>
              <span style={{ color: "var(--text-secondary)" }}>{c.pct}% menciones</span>
            </div>
            <div style={{ height: "14px", background: "rgba(127,127,127,0.15)", borderRadius: "7px", overflow: "hidden" }}>
              <div style={{ width: `${(c.pct / max) * 100}%`, height: "100%", display: "flex", borderRadius: "7px", overflow: "hidden" }}>
                {c.pos > 0 && <div style={{ flexGrow: c.pos, background: COLORS.favor }} />}
                {c.neu > 0 && <div style={{ flexGrow: c.neu, background: COLORS.neutro }} />}
                {c.neg > 0 && <div style={{ flexGrow: c.neg, background: COLORS.contra }} />}
              </div>
            </div>
            <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "2px" }}>
              {c.pos} a favor · {c.neg} en contra · {c.neu} neutro · {c.menciones} menciones
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
