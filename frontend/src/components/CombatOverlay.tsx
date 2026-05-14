import { motion, AnimatePresence } from "framer-motion";
import type { Roll, Scene } from "../types";

interface Props {
  active: { intent: string; rolls: Roll[]; targetId?: string } | null;
  scene: Scene | null;
  onDismiss: () => void;
}

export function CombatOverlay({ active, scene, onDismiss }: Props) {
  const show = !!active && active.intent === "attack" && active.rolls.length > 0;
  const damage = active?.rolls.find(r => r.kind === "damage");
  const attack = active?.rolls.find(r => r.kind === "attack");
  const target = scene?.entities.find(e => e.id === active?.targetId);

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          transition={{ duration: 0.3 }}
          onClick={onDismiss}
          className="absolute inset-0 z-30 flex items-center justify-center bg-black/40 backdrop-blur-sm">
          <motion.div
            initial={{ scale: 0.85, y: 20 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.85 }}
            transition={{ type: "spring", stiffness: 240, damping: 20 }}
            className="bg-ink/95 border-2 border-ember/50 rounded-2xl p-8 max-w-md shadow-2xl shadow-ember/20">
            <div className="text-center space-y-3">
              <div className="text-xs font-mono uppercase tracking-widest text-ember/80">
                {attack?.crit ? "critical strike" : attack?.fumble ? "fumble" : attack?.success ? "hit" : "miss"}
              </div>
              <div className="font-serif text-2xl text-parchment">
                {attack?.weapon ?? "attack"} → {target?.name ?? "target"}
              </div>
              <div className="font-mono text-sm text-parchment/60">
                d20={attack?.d20} + {attack?.modifier} = <span className="text-parchment">{attack?.total}</span>
                <span className="mx-2">vs</span>
                AC <span className="text-parchment">{attack?.dc}</span>
              </div>
              {damage && (
                <motion.div
                  initial={{ scale: 0.5, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
                  transition={{ delay: 0.4, type: "spring", stiffness: 300 }}
                  className="mt-4">
                  <div className="text-5xl font-serif text-ember">−{damage.total}</div>
                  <div className="text-xs font-mono text-parchment/40">{damage.dice} + {damage.modifier}</div>
                </motion.div>
              )}
              <button onClick={onDismiss}
                className="mt-4 text-xs font-mono text-parchment/40 hover:text-parchment transition">
                press any key to continue
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
