import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useRef } from "react";
import type { TurnResult } from "../types";
import { useI18n } from "../i18n";

interface Props {
  history: TurnResult[];
  pending: string | null;
  initialDescription: string;
}

export function ProseFeed({ history, pending, initialDescription }: Props) {
  const { t } = useI18n();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [history.length, pending]);

  return (
    <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.6 }}
        className="font-serif text-parchment/85 leading-relaxed text-lg italic max-w-3xl">
        {initialDescription}
      </motion.div>

      <AnimatePresence>
        {history.map((turn, i) => (
          <motion.div key={i}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45 }}
            className="space-y-2 max-w-3xl">
            <div className="font-mono text-sm text-ember/90">
              <span className="text-parchment/40">&gt; </span>{turn.raw_input}
            </div>
            <div className="font-serif text-parchment/95 leading-relaxed text-lg whitespace-pre-line">
              {turn.prose || <span className="italic text-parchment/40">{t("prose.empty")}</span>}
            </div>
            <div className="flex gap-4 text-xs text-parchment/40 font-mono">
              <span>{t("header.turn", { n: turn.turn })}</span>
              <span>{turn.intent}</span>
              <span>${turn.turn_cost_usd.toFixed(4)}</span>
              {turn.applied_mutations.length > 0 && (
                <span className="text-forest/80">{t("prose.opsApplied", { n: turn.applied_mutations.length })}</span>
              )}
              {turn.rejected_mutations.length > 0 && (
                <span className="text-ember/80">{t("prose.opsRejected", { n: turn.rejected_mutations.length })}</span>
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
            <span className="inline-block animate-pulse">{t("prose.thinking")}</span>
          </div>
        </motion.div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
