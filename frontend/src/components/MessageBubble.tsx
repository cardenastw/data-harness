import type { Artifact, Message } from "../types";
import ChartRenderer from "./ChartRenderer";
import DataTable from "./DataTable";
import DocsBlock from "./DocsBlock";
import LineageBlock from "./LineageBlock";
import SqlBlock from "./SqlBlock";

interface Props {
  message: Message;
}

function ArtifactBlock({ artifact, showHeader }: { artifact: Artifact; showHeader: boolean }) {
  return (
    <div className="artifact">
      {showHeader && artifact.question && (
        <div className="artifact-header">
          <span className="artifact-type-tag">{artifact.type}</span>
          <span className="artifact-question">{artifact.question}</span>
        </div>
      )}
      {artifact.error && (
        <div className="artifact-error">Failed: {artifact.error}</div>
      )}
      {artifact.type === "sql" && artifact.query && (
        <SqlBlock
          query={artifact.query}
          executionTimeMs={artifact.result?.execution_time_ms}
        />
      )}
      {artifact.type === "sql" && artifact.result && (
        <DataTable result={artifact.result} />
      )}
      {artifact.type === "sql" && artifact.chart && (
        <ChartRenderer config={artifact.chart} />
      )}
      {artifact.type === "docs" && artifact.docs && artifact.docs.length > 0 && (
        <DocsBlock docs={artifact.docs} />
      )}
      {artifact.type === "lineage" && artifact.lineage && (
        <LineageBlock lineage={artifact.lineage} />
      )}
    </div>
  );
}

export default function MessageBubble({ message }: Props) {
  const isUser = message.role === "user";
  const artifacts = message.artifacts ?? [];
  // Only show per-artifact question headers when there's more than one — keeps
  // the single-subtask UX visually identical to before.
  const showHeaders = artifacts.length > 1;

  return (
    <div className={`message ${isUser ? "message-user" : "message-assistant"}`}>
      <div className="message-role">{isUser ? "You" : "Assistant"}</div>
      <div className="message-content">{message.content}</div>
      {artifacts.map((artifact, i) => (
        <ArtifactBlock key={i} artifact={artifact} showHeader={showHeaders} />
      ))}
      {!isUser && message.suggestions && message.suggestions.length > 0 && (
        <div className="suggestions-footer">
          <span className="suggestions-label">Follow-up ideas:</span>
          <ul>
            {message.suggestions.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}
      {!isUser && message.usage && message.usage.total_tokens > 0 && (
        <div className="usage-footer">
          {message.usage.prompt_tokens.toLocaleString()} in / {message.usage.completion_tokens.toLocaleString()} out
          {message.usage.llm_calls > 1 && ` · ${message.usage.llm_calls} calls`}
        </div>
      )}
    </div>
  );
}
