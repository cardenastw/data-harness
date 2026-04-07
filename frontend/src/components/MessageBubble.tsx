import type { Message } from "../types";
import ChartRenderer from "./ChartRenderer";
import DataTable from "./DataTable";
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
        </div>
      ))}
    </div>
  );
}
