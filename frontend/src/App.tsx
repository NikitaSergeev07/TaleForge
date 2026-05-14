import { useEffect, useState } from "react";
import { api } from "./api";
import type { NpcCard, Roll, Scene, TurnResult, WorldMap } from "./types";
import { useI18n } from "./i18n";
import { Header } from "./components/Header";
import { ProseFeed } from "./components/ProseFeed";
import { ActionInput } from "./components/ActionInput";
import { WorldMap as WorldMapView } from "./components/WorldMap";
import { NpcPanel } from "./components/NpcPanel";
import { InventoryPanel } from "./components/InventoryPanel";
import { DiceFooter } from "./components/DiceFooter";
import { CombatOverlay } from "./components/CombatOverlay";

export default function App() {
  const { lang, t } = useI18n();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [scene, setScene] = useState<Scene | null>(null);
  const [map, setMap] = useState<WorldMap | null>(null);
  const [npcs, setNpcs] = useState<NpcCard[]>([]);
  const [history, setHistory] = useState<TurnResult[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [combat, setCombat] = useState<{ intent: string; rolls: Roll[]; targetId?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Boot: pick latest session or create one in the player's current language.
  useEffect(() => {
    (async () => {
      try {
        const sessions = await api.listSessions();
        const sid = sessions.length
          ? sessions[sessions.length - 1].session_id
          : (await api.createSession("starter_village", undefined, lang)).session_id;
        setSessionId(sid);
      } catch (e: any) { setError(e.message); }
    })();
    // intentionally only on mount; switching lang afterward only affects new sessions + future turns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    refreshViews(sessionId);
  }, [sessionId]);

  async function refreshViews(sid: string) {
    try {
      const [s, m, n] = await Promise.all([api.scene(sid), api.map(sid), api.npcs(sid)]);
      setScene(s); setMap(m); setNpcs(n);
    } catch (e: any) { setError(e.message); }
  }

  async function takeTurn(input: string) {
    if (!sessionId) return;
    setPending(input);
    setError(null);
    try {
      // Pass lang on every turn so a mid-session language switch takes effect immediately.
      const result = await api.takeTurn(sessionId, input, lang);
      setHistory(h => [...h, result]);
      await refreshViews(sessionId);
      if (result.intent === "attack" && result.rolls.length > 0) {
        const targetMut = result.applied_mutations.find((m: any) => m.op === "apply_damage");
        setCombat({ intent: result.intent, rolls: result.rolls, targetId: targetMut?.args?.entity_id });
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setPending(null);
    }
  }

  async function newSession() {
    try {
      const r = await api.createSession("starter_village", undefined, lang);
      setSessionId(r.session_id);
      setHistory([]); setCombat(null);
    } catch (e: any) { setError(e.message); }
  }

  async function undo() {
    if (!sessionId) return;
    try {
      await api.undo(sessionId);
      setHistory(h => h.slice(0, -1));
      await refreshViews(sessionId);
    } catch (e: any) { setError(e.message); }
  }

  const lastTurn = history[history.length - 1];
  const cumulativeCost = lastTurn?.cumulative_cost_usd ?? 0;
  const lastRolls = lastTurn?.rolls ?? [];
  const lastTurnCost = lastTurn?.turn_cost_usd ?? 0;

  return (
    <div className="h-screen flex flex-col text-parchment relative">
      <Header
        sessionId={sessionId ?? "…"}
        scene={scene}
        cumulativeCost={cumulativeCost}
        onSave={() => {/* every turn auto-saves */}}
        onUndo={undo}
      />

      {error && (
        <div className="px-8 py-2 text-sm font-mono bg-ember/30 text-parchment border-b border-ember/50">
          {error}  <button onClick={() => setError(null)} className="underline ml-2">{t("error.dismiss")}</button>
        </div>
      )}

      <div className="flex-1 grid grid-cols-12 min-h-0 overflow-hidden">
        <aside className="col-span-3 border-r border-parchment/10 bg-black/20 p-5 overflow-y-auto space-y-5">
          <div>
            <div className="text-xs font-mono uppercase tracking-widest text-parchment/40 mb-2">{t("panel.worldMap")}</div>
            <WorldMapView map={map} />
          </div>
          <button onClick={newSession}
            className="w-full px-3 py-2 text-xs font-mono border border-parchment/20 rounded
                       hover:bg-parchment/10 transition">
            {t("header.newSession")}
          </button>
        </aside>

        <main className="col-span-6 flex flex-col min-h-0 bg-gradient-to-b from-ink to-black">
          <ProseFeed
            history={history}
            pending={pending}
            initialDescription={scene?.location?.description ?? ""}
          />
          <DiceFooter rolls={lastRolls} cost={lastTurnCost} />
          <ActionInput disabled={!!pending || !sessionId} onSubmit={takeTurn} />
        </main>

        <aside className="col-span-3 border-l border-parchment/10 bg-black/20 p-5 overflow-y-auto space-y-5">
          <div>
            <div className="text-xs font-mono uppercase tracking-widest text-parchment/40 mb-2">{t("panel.npcs")}</div>
            <NpcPanel npcs={npcs} scene={scene} />
          </div>
          <div>
            <div className="text-xs font-mono uppercase tracking-widest text-parchment/40 mb-2">{t("panel.player")}</div>
            <InventoryPanel player={scene?.player ?? null} />
          </div>
        </aside>
      </div>

      <CombatOverlay active={combat} scene={scene} onDismiss={() => setCombat(null)} />
    </div>
  );
}
