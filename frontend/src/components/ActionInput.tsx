import { useState, KeyboardEvent } from "react";

interface Props {
  disabled: boolean;
  onSubmit: (text: string) => void;
}

const SUGGESTIONS = [
  "look around",
  "go north",
  "say hi to Maren",
  "ask Tibor about the woods",
  "search for tracks",
  "attack the dire wolf",
];

export function ActionInput({ disabled, onSubmit }: Props) {
  const [text, setText] = useState("");

  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSubmit(t);
    setText("");
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") submit();
  };

  return (
    <div className="border-t border-parchment/10 bg-black/30 px-8 py-4 space-y-2">
      <div className="flex items-center gap-3">
        <span className="font-mono text-ember/80 text-lg">&gt;</span>
        <input
          type="text" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
          disabled={disabled}
          placeholder={disabled ? "…" : "type any action — or pick one below"}
          className="flex-1 bg-transparent outline-none font-serif text-lg text-parchment placeholder:text-parchment/30"
          autoFocus
        />
        <button onClick={submit} disabled={disabled || !text.trim()}
          className="px-4 py-1 rounded font-mono text-sm border border-parchment/20 disabled:opacity-30
                     hover:bg-parchment/10 transition">
          send
        </button>
      </div>
      <div className="flex flex-wrap gap-2 text-xs">
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => !disabled && onSubmit(s)} disabled={disabled}
            className="px-2 py-0.5 rounded font-mono text-parchment/50 border border-parchment/10
                       hover:text-parchment hover:border-parchment/30 transition disabled:opacity-30">
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
