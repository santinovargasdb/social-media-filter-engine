export default function DisclaimerBanner({ texto }: { texto: string }) {
  return (
    <div style={{
      margin: "0 0 16px", padding: "10px 14px", fontSize: "12px", lineHeight: 1.5,
      borderRadius: "var(--radius-sm)", fontStyle: "italic",
      background: "rgba(255,193,7,0.12)", border: "1px solid rgba(255,193,7,0.4)",
      color: "var(--smata-gold, #b78a00)",
    }}>
      ⚠ {texto}
    </div>
  );
}
