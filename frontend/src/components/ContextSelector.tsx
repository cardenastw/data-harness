import { useEffect, useState } from "react";
import { fetchContexts } from "../api/client";
import type { Context } from "../types";

interface Props {
  selectedId: string;
  onChange: (id: string) => void;
}

export default function ContextSelector({ selectedId, onChange }: Props) {
  const [contexts, setContexts] = useState<Context[]>([]);

  useEffect(() => {
    fetchContexts()
      .then((res) => setContexts(res.contexts))
      .catch(console.error);
  }, []);

  return (
    <div className="context-selector">
      {contexts.map((ctx) => (
        <button
          key={ctx.id}
          className={`context-btn ${selectedId === ctx.id ? "active" : ""}`}
          onClick={() => onChange(ctx.id)}
          title={ctx.description}
        >
          {ctx.name}
        </button>
      ))}
    </div>
  );
}
