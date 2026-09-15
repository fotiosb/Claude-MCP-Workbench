export function ArchitectureSvg() {
  return (
    <svg className="arch" viewBox="0 0 920 260" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Architecture">
      <rect width="920" height="260" fill="#12151c" />
      {box(20, 90, 120, 70, "Browser")}
      {box(180, 90, 150, 70, "FastAPI host")}
      {box(370, 30, 160, 60, "/mcp Streamable HTTP")}
      {box(370, 150, 160, 60, "Task worker")}
      {box(570, 20, 140, 50, "git clone")}
      {box(570, 90, 140, 50, "static scans")}
      {box(570, 160, 140, 50, "SQLite WAL")}
      {box(750, 90, 150, 70, "audit:// tree://")}
      {arrow(140, 125, 180, 125)}
      {arrow(330, 125, 370, 60)}
      {arrow(330, 125, 370, 180)}
      {arrow(530, 60, 570, 45)}
      {arrow(530, 180, 570, 185)}
      {arrow(530, 180, 570, 115)}
      {arrow(710, 185, 750, 125)}
    </svg>
  );
}

function box(x: number, y: number, w: number, h: number, label: string) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx="8" fill="#171b24" stroke="#2a3040" />
      <text x={x + w / 2} y={y + h / 2 + 4} textAnchor="middle" fill="#e6e8ee" fontSize="12" fontFamily="system-ui">
        {label}
      </text>
    </g>
  );
}

function arrow(x1: number, y1: number, x2: number, y2: number) {
  return <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#4d6f99" strokeWidth="1.5" />;
}
