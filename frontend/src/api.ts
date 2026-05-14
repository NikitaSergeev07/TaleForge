import type {
  NpcCard,
  Scene,
  SessionSummary,
  TurnResult,
  WorldMap,
} from "./types";

const J = { headers: { "Content-Type": "application/json" } };

async function check<T>(p: Promise<Response>): Promise<T> {
  const r = await p;
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`HTTP ${r.status}: ${text}`);
  }
  return r.json();
}

export const api = {
  listSessions: () => check<SessionSummary[]>(fetch("/api/sessions")),

  createSession: (scenario = "starter_village", session_id?: string, language: string = "en") =>
    check<{ session_id: string; db_path: string }>(
      fetch("/api/sessions", {
        method: "POST", ...J,
        body: JSON.stringify({ scenario, session_id, language }),
      })
    ),

  scene: (sid: string) => check<Scene>(fetch(`/api/sessions/${sid}/scene`)),
  map: (sid: string) => check<WorldMap>(fetch(`/api/sessions/${sid}/world-map`)),
  npcs: (sid: string) => check<NpcCard[]>(fetch(`/api/sessions/${sid}/npcs`)),

  takeTurn: (sid: string, input: string, language?: string) =>
    check<TurnResult>(fetch(`/api/sessions/${sid}/turn`, {
      method: "POST", ...J, body: JSON.stringify({ input, language }),
    })),

  undo: (sid: string) =>
    check<Scene>(fetch(`/api/sessions/${sid}/undo`, { method: "POST" })),

  portraitUrl: (npcId: string) => `/api/portraits/${npcId}.svg`,
};
