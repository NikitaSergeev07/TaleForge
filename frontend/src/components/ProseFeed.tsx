import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useRef } from "react";
import type { TurnResult } from "../types";

interface Props {
  history: TurnResult[];
  pending: string | null;
  initialDescription: string;
}

export function ProseFeed({ history, pending, initialDescription }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [history.length, pending]);

  return (
    <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
      {/* Opening: location description as the first "scene" */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.6 }}
        className="font-serif text-parchment/85 leading-relaxed text-lg italic max-w-3xl">
        {initialDescription}
      </motion.div>

      <AnimatePresence>
        {history.map((t, i) => (
          <motion.div key={i}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45, delay: i === history.length - 1 ? 0 : 0 }}
            className="space-y-2 max-w-3xl">
            {/* User input */}
            <div className="font-mono text-sm text-ember/90">
              <span className="text-parchment/40">&gt; </span>{t.raw_input}
            </div>
            {/* Narrator prose */}
            <div className="font-serif text-parchment/95 leading-relaxed text-lg whitespace-pre-line">
              {t.prose || <span className="italic text-parchment/40">(no prose)</span>}
            </div>
            {/* Inline metadata */}
            <div className="flex gap-4 text-xs text-parchment/40 font-mono">
              <span>turn {t.turn}</span>
              <span>{t.intent}</span>
              <span>${t.turn_cost_usd.toFixed(4)}</span>
              {t.applied_mutations.length > 0 && (
                <span className="text-forest/80">{t.applied_mutations.length} ops</span>
              )}
              {t.rejected_mutations.length > 0 && (
                <span className="text-ember/80">{t.rejected_mutations.length} rejected</span>
              )}
            </div>
          </motion.div>
        ))}
      </AnimatePresence>

      {pending && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-2 max-w-3xl">
          <div className="font-mono text-sm text-ember/90">
            <span className="text-parchment/40">&gt; </span>{pending}
          </div>
          <div className="text-parchment/50 italic font-serif">
            <span className="inline-block animate-pulse">…the world considers your action…</span>
          </div>
        </motion.div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
