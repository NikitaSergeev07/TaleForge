// Translation dictionary. Add a new top-level key per language.
// All UI strings live here so translators only ever touch this file.

export type Lang = "en" | "ru";

export const LANGS: { code: Lang; label: string; native: string }[] = [
  { code: "en", label: "English", native: "EN" },
  { code: "ru", label: "Русский", native: "RU" },
];

export const STRINGS = {
  // headings + chrome
  "header.title": { en: "TaleForge", ru: "TaleForge" },
  "header.turn": { en: "turn {n}", ru: "ход {n}" },
  "header.day": { en: "day {d} · {h}:00", ru: "день {d} · {h}:00" },
  "header.undo": { en: "undo", ru: "отменить" },
  "header.save": { en: "save", ru: "сохранить" },
  "header.newSession": { en: "+ new session", ru: "+ новая сессия" },
  "header.language": { en: "language", ru: "язык" },

  // panels
  "panel.worldMap": { en: "world map", ru: "карта мира" },
  "panel.npcs": { en: "npcs", ru: "персонажи" },
  "panel.player": { en: "player", ru: "игрок" },

  // input
  "input.placeholder": {
    en: "type any action — or pick one below",
    ru: "введи любое действие — или выбери из списка",
  },
  "input.placeholderPending": { en: "…", ru: "…" },
  "input.send": { en: "send", ru: "отправить" },
  "input.suggestions": { en: "try", ru: "попробуй" },

  // suggestion chips (one per language to keep them idiomatic)
  "suggestions": {
    en: [
      "look around",
      "go north",
      "say hi to Maren",
      "ask Tibor about the woods",
      "search for tracks",
      "attack the dire wolf",
    ],
    ru: [
      "осмотрись",
      "иди на север",
      "поздоровайся с Марен",
      "спроси Тибора о лесах",
      "найди следы",
      "атакуй жуткого волка",
    ],
  } as const,

  // prose feed
  "prose.thinking": {
    en: "…the world considers your action…",
    ru: "…мир обдумывает твоё действие…",
  },
  "prose.empty": { en: "(no prose)", ru: "(пусто)" },
  "prose.opsApplied": { en: "{n} ops", ru: "{n} операций" },
  "prose.opsRejected": { en: "{n} rejected", ru: "{n} отклонено" },

  // npc card
  "npc.here": { en: "here", ru: "тут" },
  "npc.remembersYou": { en: "remembers you", ru: "помнит тебя" },

  // npc disposition labels
  "disposition.loathing": { en: "loathing", ru: "ненавидит" },
  "disposition.hostile": { en: "hostile", ru: "враждебен" },
  "disposition.wary": { en: "wary", ru: "настороже" },
  "disposition.neutral": { en: "neutral", ru: "нейтрален" },
  "disposition.friendly": { en: "friendly", ru: "дружелюбен" },
  "disposition.warm": { en: "warm", ru: "тепло относится" },
  "disposition.devoted": { en: "devoted", ru: "предан" },

  // hp labels
  "hp.uninjured": { en: "uninjured", ru: "невредим" },
  "hp.scratched": { en: "scratched", ru: "слегка задет" },
  "hp.wounded": { en: "wounded", ru: "ранен" },
  "hp.bloodied": { en: "bloodied", ru: "истекает кровью" },
  "hp.near death": { en: "near death", ru: "при смерти" },
  "hp.down": { en: "down", ru: "повержен" },
  "hp.unknown": { en: "unknown", ru: "неизвестно" },

  // inventory panel
  "inventory.hp": { en: "HP", ru: "ОЗ" },
  "inventory.gold": { en: "gold", ru: "золото" },
  "inventory.gp": { en: "{n} gp", ru: "{n} зм" },
  "inventory.title": { en: "inventory", ru: "инвентарь" },
  "inventory.empty": { en: "empty", ru: "пусто" },

  // dice footer
  "dice.thisTurn": { en: "{n} dice this turn", ru: "{n} костей за ход" },
  "dice.lastTurnCost": { en: "last turn cost ${c}", ru: "цена хода ${c}" },

  // combat overlay
  "combat.crit": { en: "critical strike", ru: "критический удар" },
  "combat.fumble": { en: "fumble", ru: "промах" },
  "combat.hit": { en: "hit", ru: "попадание" },
  "combat.miss": { en: "miss", ru: "мимо" },
  "combat.dismiss": {
    en: "press any key to continue",
    ru: "нажми любую клавишу, чтобы продолжить",
  },

  // dismiss / errors
  "error.dismiss": { en: "dismiss", ru: "скрыть" },
} as const;

export type StringKey = keyof typeof STRINGS;
