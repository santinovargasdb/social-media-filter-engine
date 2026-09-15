import type { UrnaComparacion } from "@/lib/urnaApi";

function gapColor(gap: number | null): string {
  if (gap === null) return "var(--text-secondary)";
  const a = Math.abs(gap);
  if (a <= 3) return "#4CAF50";
  if (a <= 8) return "#FFC107";
  return "#F87171";
}
const fmt = (n: number | null) => (n === null ? "—" : `${n > 0 ? "+" : ""}${n}`);

export default function ComparisonTable({ comparacion }: { comparacion: UrnaComparacion[] }) {
  if (!comparacion.length) return <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>Cargá un CSV de consultoras para ver la comparación.</p>;
  const consultoras = Array.from(new Set(comparacion.flatMap((c) => c.consultoras.map((x) => x.consultora)))).sort();
  const th: React.CSSProperties = { textAlign: "left", padding: "6px 8px", fontSize: "11px",
    textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--text-secondary)", borderBottom: "1px solid var(--border-color)" };
  const td: React.CSSProperties = { padding: "6px 8px", fontSize: "13px", borderBottom: "1px solid var(--border-color)" };

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", minWidth: "480px" }}>
        <thead>
          <tr>
            <th style={th}>Candidato</th>
            <th style={th}>Redes</th>
            {consultoras.map((c) => <th key={c} style={th}>{c}</th>)}
            <th style={th}>Prom. consult.</th>
            <th style={th}>Brecha prom.</th>
          </tr>
        </thead>
        <tbody>
          {comparacion.map((row) => (
            <tr key={row.candidato}>
              <td style={{ ...td, fontWeight: 600 }}>{row.candidato}</td>
              <td style={td}>{row.redes_pct}%</td>
              {consultoras.map((name) => {
                const cell = row.consultoras.find((x) => x.consultora === name);
                return (
                  <td key={name} style={td}>
                    {cell ? (
                      <>
                        {cell.pct}% <span style={{ color: gapColor(cell.gap), fontSize: "11px" }}>({fmt(cell.gap)})</span>
                        {cell.fuente_url && (
                          <a href={cell.fuente_url} target="_blank" rel="noopener noreferrer"
                             title={`Ver fuente: ${cell.fuente_titulo || "fuente"}${cell.fecha ? " · " + cell.fecha : ""}`}
                             style={{ marginLeft: "5px", fontSize: "12px", fontWeight: 700,
                                      color: "var(--smata-green-light, #4CAF50)", textDecoration: "none",
                                      cursor: "pointer" }}>↗</a>
                        )}
                      </>
                    ) : "—"}
                  </td>
                );
              })}
              <td style={td}>{row.promedio_consultoras === null ? "—" : `${row.promedio_consultoras}%`}</td>
              <td style={{ ...td, color: gapColor(row.gap_promedio), fontWeight: 600 }}>{fmt(row.gap_promedio)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "8px" }}>
        Datos extraídos automáticamente de fuentes públicas. Tocá el{" "}
        <span style={{ color: "var(--smata-green-light, #4CAF50)", fontWeight: 700 }}>↗</span>{" "}
        que aparece junto a cada porcentaje para abrir la nota de origen y verificarlo.
      </p>
    </div>
  );
}
