import type { LineageNode } from "../types";

interface Props {
  lineage: LineageNode;
}

export default function LineageBlock({ lineage }: Props) {
  const upstreamTables = lineage.upstream_tables ?? [];
  const upstreamColumns = lineage.upstream_columns ?? [];
  const derivedFrom = lineage.derived_from ?? [];

  return (
    <div className="lineage-block">
      <div className="lineage-header">
        <span className="lineage-kind">{lineage.kind}</span>
        <span className="lineage-name">{lineage.name}</span>
      </div>

      {lineage.formula && (
        <div className="lineage-section">
          <div className="lineage-label">Formula</div>
          <pre className="lineage-formula">
            <code>{lineage.formula}</code>
          </pre>
        </div>
      )}

      {upstreamTables.length > 0 && (
        <div className="lineage-section">
          <div className="lineage-label">Upstream tables</div>
          <div className="lineage-pills">
            {upstreamTables.map((t) => (
              <span key={t} className="lineage-pill">
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      {upstreamColumns.length > 0 && (
        <div className="lineage-section">
          <div className="lineage-label">Upstream columns</div>
          <div className="lineage-pills">
            {upstreamColumns.map((c) => (
              <span key={c} className="lineage-pill lineage-pill-col">
                {c}
              </span>
            ))}
          </div>
        </div>
      )}

      {derivedFrom.length > 0 && (
        <div className="lineage-section">
          <div className="lineage-label">Derived from</div>
          <div className="lineage-pills">
            {derivedFrom.map((c) => (
              <span key={c} className="lineage-pill lineage-pill-col">
                {c}
              </span>
            ))}
          </div>
        </div>
      )}

      {lineage.source_system && (
        <div className="lineage-meta">
          <span className="lineage-label">Source:</span> {lineage.source_system}
          {lineage.refresh_cadence && ` · refreshes ${lineage.refresh_cadence}`}
        </div>
      )}

      {lineage.notes && <p className="lineage-notes">{lineage.notes}</p>}
    </div>
  );
}
