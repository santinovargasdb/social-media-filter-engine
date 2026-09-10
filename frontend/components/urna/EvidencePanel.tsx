import type { UrnaEvidencia, Postura } from "@/lib/urnaApi";

const COLOR: Record<Postura, string> = {
  a_favor: "#4CAF50", en_contra: "#F87171", neutro: "#9CA3AF",
};
const LABEL: Record<Postura, string> = {
  a_favor: "a favor", en_contra: "en contra", neutro: "neutro",
};

export default function EvidencePanel({ evidencia }: { evidencia: UrnaEvidencia[] }) {
  if (!evidencia.length) return <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>Sin citas de respaldo.</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {evidencia.map((e, i) => (
        <div key={i} style={{ borderLeft: `3px solid ${COLOR[e.postura]}`, padding: "6px 10px",
          background: "rgba(127,127,127,0.06)", borderRadius: "0 6px 6px 0" }}>
          <div style={{ fontSize: "12px", marginBottom: "4px" }}>
            <span style={{ fontWeight: 600 }}>{e.candidato}</span>
            <span style={{ color: COLOR[e.postura], marginLeft: "6px" }}>· {LABEL[e.postura]}</span>
            <span style={{ color: "var(--text-secondary)", marginLeft: "6px" }}>({e.post.network})</span>
          </div>
          <div style={{ fontSize: "13px", fontStyle: "italic" }}>&ldquo;{e.cita}&rdquo;</div>
          {e.post.post_url && (
            <a href={e.post.post_url} target="_blank" rel="noreferrer"
               style={{ fontSize: "11px", color: "var(--smata-green-light, #4CAF50)" }}>
              ver publicación ↗
            </a>
          )}
        </div>
      ))}
    </div>
  );
}
