import type { Scene } from "../types";

interface Props {
  sessionId: string;
  scene: Scene | null;
  cumulativeCost: number;
  onSave: () => void;
  onUndo: () => void;
}

export function Header({ sessionId, scene, cumulativeCost, onSave, onUndo }: Props) {
  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-parchment/10 bg-black/30 backdrop-blur">
      <div>
        <div className="font-serif italic text-2xl text-parchment">TaleForge</div>
        <div className="text-xs text-parchment/50 font-mono">{sessionId}</div>
      </div>
      <div className="flex items-center gap-6 text-sm font-mono text-parchment/70">
        {scene && (
          <>
            <span>turn {scene.turn}</span>
            <span>day {scene.in_game_time.day} · {String(scene.in_game_time.hour).padStart(2, "0")}:00</span>
            <span>${cumulativeCost.toFixed(4)}</span>
          </>
        )}
        <button onClick={onUndo}
          className="px-3 py-1 rounded border border-parchment/20 hover:bg-parchment/10 transition">
          undo
        </button>
        <button onClick={onSave}
          className="px-3 py-1 rounded border border-parchment/20 hover:bg-parchment/10 transition">
          save
        </button>
      </div>
    </header>
  );
}
