import type { Player } from "../types";
import { useI18n } from "../i18n";

interface Props { player: Player | null }

export function InventoryPanel({ player }: Props) {
  const { t } = useI18n();
  if (!player) return null;

  const hpPct = player.hp != null && player.max_hp ? (player.hp / player.max_hp) * 100 : 0;
  const hpColor = hpPct > 70 ? "bg-forest" : hpPct > 40 ? "bg-yellow-600" : hpPct > 15 ? "bg-orange-600" : "bg-ember";

  return (
    <div className="bg-parchment/5 border border-parchment/10 rounded-lg p-3 space-y-3">
      <div>
        <div className="flex justify-between text-xs font-mono text-parchment/60 mb-1">
          <span>{t("inventory.hp")}</span>
          <span>{player.hp}/{player.max_hp}</span>
        </div>
        <div className="h-2 rounded-full bg-parchment/10 overflow-hidden">
          <div className={`h-full ${hpColor} transition-all duration-700`} style={{ width: `${hpPct}%` }} />
        </div>
      </div>

      <div>
        <div className="flex justify-between text-xs font-mono text-parchment/60 mb-1">
          <span>{t("inventory.gold")}</span>
          <span>{t("inventory.gp", { n: player.gp })}</span>
        </div>
        <div className="flex flex-wrap gap-1">
          {Array.from({ length: Math.min(player.gp, 30) }).map((_, i) => (
            <div key={i} className="w-2 h-2 rounded-full bg-yellow-500/80" />
          ))}
          {player.gp > 30 && <span className="text-xs font-mono text-parchment/40 ml-1">+{player.gp - 30}</span>}
        </div>
      </div>

      <div>
        <div className="text-xs font-mono text-parchment/60 mb-1">{t("inventory.title")}</div>
        {player.inventory.length === 0 ? (
          <div className="text-xs text-parchment/30 italic">{t("inventory.empty")}</div>
        ) : (
          <ul className="space-y-1">
            {player.inventory.map((item, i) => (
              <li key={i} className="font-serif text-parchment/85 text-sm">{item}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
