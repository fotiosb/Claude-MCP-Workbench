export function Brief({ text, source }: { text: string; source?: string }) {
  return (
    <div>
      {source ? <p style={{ color: "#8b90a0", fontSize: 12 }}>source: {source}</p> : null}
      <div className="brief">{text}</div>
    </div>
  );
}
