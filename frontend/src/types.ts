// Mirrors src/taleforge/web/schemas.py — keep in sync.

export interface SceneEntity {
  id: string;
  name: string;
  kind: "player" | "npc" | "creature" | "item";
  alive: boolean;
  hp_label: string;
}

export interface Location {
  id: string;
  name: string;
  description: string;
  exits: Record<string, string>;
}

export interface Player {
  id: string;
  name: string;
  hp: number | null;
  max_hp: number | null;
  hp_label: string;
  inventory: string[];
  gp: number;
}

export interface Scene {
  location: Location | null;
  entities: SceneEntity[];
  player: Player;
  turn: number;
  in_game_time: { day: number; hour: number };
}

export interface WorldMapNode { id: string; name: string }
export interface WorldMapEdge { from: string; to: string; direction: string }
export interface WorldMap {
  nodes: WorldMapNode[];
  edges: WorldMapEdge[];
  current: string | null;
}

export interface NpcCard {
  id: string;
  name: string;
  hp_label: string;
  alive: boolean;
  disposition_norm: number;       // -1..1
  disposition_label: string;
  has_interacted: boolean;
  location_id: string | null;
}

export interface Roll {
  kind: string;
  d20?: number;
  modifier?: number;
  dc?: number;
  total?: number;
  success?: boolean;
  crit?: boolean;
  fumble?: boolean;
  ability?: string;
  weapon?: string;
  dice?: string;
}

export interface TurnResult {
  turn: number;
  intent: string;
  raw_input: string;
  prose: string;
  rolls: Roll[];
  applied_mutations: any[];
  rejected_mutations: any[];
  turn_cost_usd: number;
  cumulative_cost_usd: number;
}

export interface SessionSummary {
  session_id: string;
  turn: number;
  cumulative_cost_usd: number;
  location_name: string | null;
}
