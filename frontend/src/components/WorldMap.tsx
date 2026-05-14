import type { WorldMap as WorldMapData } from "../types";

interface Props {
  map: WorldMapData | null;
}

// Hand-laid layout for Brackenhollow (pixel coords on 320×360 viewBox).
// Falls back to a circular layout for unknown locations.
const LAYOUT: Record<string, { x: number; y: number }> = {
  village_square: { x: 160, y: 200 },
  tavern:         { x: 160, y: 100 },
  smithy:         { x: 270, y: 200 },
  edge_of_woods:  { x:  60, y: 200 },
  deep_woods:     { x:  60, y: 290 },
  wolf_den:       { x: 160, y: 320 },
};

const W = 320, H = 360;

function pos(id: string, fallbackIdx: number, total: number) {
  if (LAYOUT[id]) return LAYOUT[id];
  // simple circle fallback
  const angle = (fallbackIdx / total) * Math.PI * 2;
  return { x: W / 2 + 110 * Math.cos(angle), y: H / 2 + 110 * Math.sin(angle) };
}

export function WorldMap({ map }: Props) {
  if (!map) return <div className="text-parchment/40 text-sm">loading map…</div>;

  const positions: Record<string, { x: number; y: number }> = {};
  map.nodes.forEach((n, i) => { positions[n.id] = pos(n.id, i, map.nodes.length); });

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
      {/* edges */}
      {map.edges.map((e, i) => {
        const a = positions[e.from], b = positions[e.to];
        if (!a || !b) return null;
        return (
          <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                stroke="rgba(245,239,225,0.18)" strokeWidth={1.5} />
        );
      })}
      {/* nodes */}
      {map.nodes.map((n) => {
        const p = positions[n.id];
        const here = n.id === map.current;
        return (
          <g key={n.id} transform={`translate(${p.x}, ${p.y})`}>
            <circle r={here ? 14 : 10}
              fill={here ? "#c2410c" : "rgba(245,239,225,0.10)"}
              stroke={here ? "#fed7aa" : "rgba(245,239,225,0.35)"}
              strokeWidth={here ? 2 : 1}
              className={here ? "glow-current" : ""}
            />
            <text y={here ? 32 : 26} textAnchor="middle"
              className={here ? "font-serif fill-parchment text-[11px]" : "font-serif fill-parchment/60 text-[10px]"}>
              {n.name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
