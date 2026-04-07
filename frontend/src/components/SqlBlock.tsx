import { useState } from "react";

interface Props {
  query: string;
  executionTimeMs?: number;
}

export default function SqlBlock({ query, executionTimeMs }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <div className="sql-block">
      <button className="sql-toggle" onClick={() => setOpen(!open)}>
        {open ? "Hide" : "Show"} SQL
        {executionTimeMs !== undefined && (
          <span className="sql-time"> ({executionTimeMs.toFixed(1)}ms)</span>
        )}
      </button>
      {open && (
        <pre className="sql-code">
          <code>{query}</code>
        </pre>
      )}
    </div>
  );
}
