import { useState } from "react";
import type { Roll } from "../types";

interface Props {
  rolls: Roll[];
  cost: number;
}

function describe(r: Roll): string {
  if (r.kind === "attack") {
    const verdict = r.crit ? "CRIT" : r.success ? "hit" : (r.fumble ? "fumble" : "miss");
    return `attack: d20=${r.d20}+${r.modifier}=${r.total} vs AC${r.dc} → ${verdict}`;
  }
  if (r.kind === "damage") return `damage: ${r.dice}+${r.modifier}=${r.total}${r.crit ? " ✦" : ""}`;
  if (r.kind === "skill_check") {
    return `${r.ability}: d20=${r.d20}+${r.modifier}=${r.total} vs DC${r.dc} → ${r.success ? "pass" : "fail"}`;
  }
  return r.kind;
}

export function DiceFooter({ rolls, cost }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-t border-parchment/10 bg-black/40 px-8 py-2">
      <div className="flex items-center justify-between text-xs font-mono text-parchment/50">
        <button onClick={() => setOpen(!open)}
          className="flex items-center gap-2 hover:text-parchment transition">
          <span>{open ? "▾" : "▸"}</span>
          <span>{rolls.length} dice this turn</span>
        </button>
        <span>last turn cost ${cost.toFixed(4)}</span>
      </div>
      {open && rolls.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs font-mono text-parchment/70">
          {rolls.map((r, i) => (
            <li key={i} className="flex items-center gap-2">
              <span className="text-ember/60">⚀</span>
              <span>{describe(r)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
