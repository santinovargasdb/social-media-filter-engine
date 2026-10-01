import type { UrnaHistorialPunto } from "@/lib/urnaApi";

// Evolución del share de menciones (pct) por candidato a través de las corridas
// del scraper local (2-3 por día). Hecho a mano en SVG como el resto de los
// gráficos de la urna: sin librerías.
const PALETA = ["#4CAF50", "#3B82F6", "#E5534B", "#F59E0B", "#A855F7", "#14B8A6"];
const MAX_LINEAS = PALETA.length;

const W = 640, H = 240, M = { top: 10, right: 12, bottom: 28, left: 36 };

function fmtPunto(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getDate()}/${d.getMonth() + 1} ${String(d.getHours()).padStart(2, "0")}h`;
}

export default function EvolutionChart({ historial }: { historial: UrnaHistorialPunto[] }) {
  if (historial.length < 2) return null;

  // Top candidatos según la última corrida (dibujar los 17 detectados sería ruido).
  const ultimo = historial[historial.length - 1];
  const top = [...ultimo.candidatos]
    .sort((a, b) => b.pct - a.pct)
    .slice(0, MAX_LINEAS)
    .map((c) => c.nombre);

  const series = top.map((nombre, i) => ({
    nombre,
    color: PALETA[i],
    puntos: historial.map((p) => {
      const c = p.candidatos.find((x) => x.nombre === nombre);
      return { pct: c?.pct ?? 0, menciones: c?.menciones ?? 0, fecha: p.generado_en };
    }),
  }));

  const maxPct = Math.max(5, ...series.flatMap((s) => s.puntos.map((p) => p.pct)));
  const yMax = Math.ceil(maxPct / 5) * 5;
  const x = (i: number) => M.left + (i * (W - M.left - M.right)) / (historial.length - 1);
  const y = (pct: number) => M.top + (1 - pct / yMax) * (H - M.top - M.bottom);

  // Eje X: primera, última y algunas intermedias (para no encimar etiquetas).
  const cada = Math.max(1, Math.ceil((historial.length - 1) / 4));
  const ticksX = historial
    .map((p, i) => ({ i, label: fmtPunto(p.generado_en) }))
    .filter((t, _, arr) => t.i % cada === 0 || t.i === arr.length - 1);
  const ticksY = [0, yMax / 2, yMax];

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
        {ticksY.map((v) => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)}
                  stroke="rgba(127,127,127,0.25)" strokeDasharray="3 3" />
            <text x={M.left - 6} y={y(v) + 3} textAnchor="end"
                  fontSize="10" fill="var(--text-secondary)">{v}%</text>
          </g>
        ))}
        {ticksX.map((t) => (
          <text key={t.i} x={x(t.i)} y={H - 8} textAnchor="middle"
                fontSize="10" fill="var(--text-secondary)">{t.label}</text>
        ))}
        {series.map((s) => (
          <g key={s.nombre}>
            <polyline fill="none" stroke={s.color} strokeWidth="2"
                      points={s.puntos.map((p, i) => `${x(i)},${y(p.pct)}`).join(" ")} />
            {s.puntos.map((p, i) => (
              <circle key={i} cx={x(i)} cy={y(p.pct)} r="3" fill={s.color}>
                <title>{`${s.nombre} · ${p.pct}% · ${p.menciones} menciones · ${fmtPunto(p.fecha)}`}</title>
              </circle>
            ))}
          </g>
        ))}
      </svg>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", fontSize: "11px",
                    color: "var(--text-secondary)", marginTop: "8px" }}>
        {series.map((s) => (
          <span key={s.nombre} style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}>
            <span style={{ width: "10px", height: "3px", background: s.color, display: "inline-block" }} />
            {s.nombre}
          </span>
        ))}
      </div>
    </div>
  );
}
