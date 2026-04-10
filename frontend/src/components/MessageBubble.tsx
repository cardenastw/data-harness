import type { Message } from "../types";
import ChartRenderer from "./ChartRenderer";
import DataTable from "./DataTable";
import DocsBlock from "./DocsBlock";
import LineageBlock from "./LineageBlock";
import SqlBlock from "./SqlBlock";

interface Props {
  message: Message;
}

export default function MessageBubble({ message }: Props) {
  const isUser = message.role === "user";

  return (
    <div className={`message ${isUser ? "message-user" : "message-assistant"}`}>
      <div className="message-role">{isUser ? "You" : "Assistant"}</div>
      <div className="message-content">{message.content}</div>
      {message.artifacts?.map((artifact, i) => (
        <div key={i} className="artifact">
          {artifact.type === "sql" && artifact.query && (
            <SqlBlock
              query={artifact.query}
              executionTimeMs={artifact.result?.execution_time_ms}
            />
          )}
          {artifact.type === "sql" && artifact.result && (
            <DataTable result={artifact.result} />
          )}
          {artifact.type === "chart" && artifact.config && (
            <ChartRenderer config={artifact.config} />
          )}
          {artifact.type === "docs" && artifact.docs && (
            <DocsBlock docs={artifact.docs} />
          )}
          {artifact.type === "lineage" && artifact.lineage && (
            <LineageBlock lineage={artifact.lineage} />
          )}
        </div>
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
