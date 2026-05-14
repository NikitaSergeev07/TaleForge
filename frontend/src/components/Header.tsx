import type { Scene } from "../types";
import { LANGS, useI18n } from "../i18n";

interface Props {
  sessionId: string;
  scene: Scene | null;
  cumulativeCost: number;
  onSave: () => void;
  onUndo: () => void;
}

export function Header({ sessionId, scene, cumulativeCost, onSave, onUndo }: Props) {
  const { lang, setLang, t } = useI18n();

  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-parchment/10 bg-black/30 backdrop-blur">
      <div>
        <div className="font-serif italic text-2xl text-parchment">{t("header.title")}</div>
        <div className="text-xs text-parchment/50 font-mono">{sessionId}</div>
      </div>
      <div className="flex items-center gap-6 text-sm font-mono text-parchment/70">
        {scene && (
          <>
            <span>{t("header.turn", { n: scene.turn })}</span>
            <span>{t("header.day", { d: scene.in_game_time.day, h: String(scene.in_game_time.hour).padStart(2, "0") })}</span>
            <span>${cumulativeCost.toFixed(4)}</span>
          </>
        )}

        {/* Language switcher */}
        <div className="flex items-center gap-1 border border-parchment/15 rounded overflow-hidden">
          {LANGS.map((l) => (
            <button
              key={l.code}
              onClick={() => setLang(l.code)}
              title={l.label}
              className={`px-2 py-1 text-xs transition ${
                lang === l.code
                  ? "bg-parchment/15 text-parchment"
                  : "text-parchment/45 hover:text-parchment/80 hover:bg-parchment/5"
              }`}>
              {l.native}
            </button>
          ))}
        </div>

        <button onClick={onUndo}
          className="px-3 py-1 rounded border border-parchment/20 hover:bg-parchment/10 transition">
          {t("header.undo")}
        </button>
        <button onClick={onSave}
          className="px-3 py-1 rounded border border-parchment/20 hover:bg-parchment/10 transition">
          {t("header.save")}
        </button>
      </div>
    </header>
  );
}
