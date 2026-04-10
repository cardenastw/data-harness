import type { Artifact, ContextsResponse, TokenUsage } from "../types";

const API_BASE = "/api";

export async function fetchContexts(): Promise<ContextsResponse> {
  const res = await fetch(`${API_BASE}/contexts`);
  if (!res.ok) throw new Error(`Failed to fetch contexts: ${res.statusText}`);
  return res.json();
}

interface ChatApiResponse {
  session_id: string;
  sql: string | null;
  raw_data: {
    columns: string[];
    rows: unknown[][];
    row_count: number;
    truncated: boolean;
    execution_time_ms: number;
  } | null;
  chart_json: Record<string, unknown> | null;
  suggestions: string[];
  usage: {
    turn: TokenUsage;
    session: TokenUsage;
  } | null;
  error: string | null;
}

export interface ChatResult {
  sessionId: string;
  content: string;
  artifacts: Artifact[];
  suggestions: string[];
  usage?: TokenUsage;
}

export async function sendMessage(
  message: string,
  sessionId?: string,
  contextId?: string,
): Promise<ChatResult> {
  const body: Record<string, string | undefined> = { message };
  if (sessionId) {
    body.session_id = sessionId;
  } else {
    body.context_id = contextId;
  }

  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Chat request failed: ${detail}`);
  }

  const data: ChatApiResponse = await res.json();

  if (data.error) {
    throw new Error(data.error);
  }

  const artifacts: Artifact[] = [];

  if (data.sql && data.raw_data) {
    artifacts.push({
      type: "sql",
      query: data.sql,
      result: {
        columns: data.raw_data.columns,
        rows: data.raw_data.rows,
        row_count: data.raw_data.row_count,
        truncated: data.raw_data.truncated,
        execution_time_ms: data.raw_data.execution_time_ms,
      },
    });
  }

  if (data.chart_json) {
    artifacts.push({
      type: "chart",
      config: data.chart_json as unknown as Artifact["config"],
    });
  }

  let content = "";
  if (data.raw_data) {
    const { row_count, columns } = data.raw_data;
    content = `Query returned ${row_count} row${row_count !== 1 ? "s" : ""} with ${columns.length} column${columns.length !== 1 ? "s" : ""}.`;
  } else {
    content = "No results returned.";
  }

  return {
    sessionId: data.session_id,
    content,
    artifacts,
    suggestions: data.suggestions || [],
    usage: data.usage?.turn,
  };
}
