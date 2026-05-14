import type { NpcCard, Scene } from "../types";
import { api } from "../api";

interface Props {
  npcs: NpcCard[];
  scene: Scene | null;
}

const HP_COLOR: Record<string, string> = {
  uninjured: "bg-forest",
  scratched: "bg-forest/80",
  wounded: "bg-yellow-600",
  bloodied: "bg-orange-600",
  "near death": "bg-ember",
  down: "bg-steel",
};

function DispositionBar({ norm, label }: { norm: number; label: string }) {
  // norm in [-1, 1]; map to bar width with center.
  const pct = Math.round(((norm + 1) / 2) * 100);
  const color = norm >= 0.4 ? "bg-forest" : norm >= -0.1 ? "bg-yellow-600" : "bg-ember";
  return (
    <div>
      <div className="flex justify-between text-[10px] font-mono text-parchment/50 mb-0.5">
        <span>{label}</span>
        <span>{Math.round(norm * 100)}</span>
      </div>
      <div className="h-1.5 rounded-full bg-parchment/10 overflow-hidden">
        <div className={`h-full ${color} transition-all duration-700`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function NpcPanel({ npcs, scene }: Props) {
  // Visible NPCs first (co-located with player), then others by location.
  const here = new Set(scene?.entities.map(e => e.id) ?? []);
  const sorted = [...npcs].sort((a, b) => {
    const ah = here.has(a.id) ? 0 : 1;
    const bh = here.has(b.id) ? 0 : 1;
    return ah - bh;
  });

  return (
    <div className="space-y-3">
      {sorted.map((n) => {
        const isHere = here.has(n.id);
        return (
          <div key={n.id}
            className={`p-3 rounded-lg border transition-all ${
              isHere
                ? "bg-parchment/5 border-ember/40 shadow-lg shadow-ember/10"
                : "bg-black/20 border-parchment/10"
            } ${!n.alive ? "opacity-40" : ""}`}>
            <div className="flex items-start gap-3">
              <img src={api.portraitUrl(n.id)} alt=""
                className="w-12 h-12 rounded-full flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline justify-between">
                  <div className="font-serif text-parchment text-base truncate">{n.name}</div>
                  {isHere && (
                    <span className="text-[10px] font-mono text-ember/90 ml-2 uppercase tracking-wider">here</span>
                  )}
                </div>
                <div className="text-xs text-parchment/50 mt-0.5">
                  <span className={`inline-block w-2 h-2 rounded-full ${HP_COLOR[n.hp_label] ?? "bg-steel"} mr-1.5 align-middle`} />
                  {n.hp_label}
                  {n.has_interacted && <span className="ml-2 text-parchment/30">· remembers you</span>}
                </div>
                <div className="mt-2">
                  <DispositionBar norm={n.disposition_norm} label={n.disposition_label} />
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
