import type {
  Artifact,
  ChartConfig,
  ContextsResponse,
  DocResult,
  LineageNode,
  QueryResult,
  TokenUsage,
} from "../types";

const API_BASE = "/api";

export async function fetchContexts(): Promise<ContextsResponse> {
  const res = await fetch(`${API_BASE}/contexts`);
  if (!res.ok) throw new Error(`Failed to fetch contexts: ${res.statusText}`);
  return res.json();
}

interface ApiArtifact {
  type: "sql" | "docs" | "lineage";
  subtask_id?: string;
  question?: string;
  reason?: string;
  // SQL
  sql?: string | null;
  raw_data?: QueryResult | null;
  chart_json?: Record<string, unknown> | null;
  // Docs
  docs?: DocResult[];
  // Lineage
  lineage?: LineageNode | null;
  // Either-side intermediate answer
  answer_text?: string | null;
  error?: string | null;
}

interface ChatApiResponse {
  session_id: string;
  answer_text: string | null;
  artifacts: ApiArtifact[];
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

function mapArtifact(api: ApiArtifact): Artifact {
  const base: Artifact = {
    type: api.type,
    subtaskId: api.subtask_id,
    question: api.question,
    reason: api.reason,
    error: api.error ?? undefined,
  };

  if (api.type === "sql") {
    base.query = api.sql ?? undefined;
    base.result = api.raw_data ?? undefined;
    if (api.chart_json) {
      base.chart = api.chart_json as unknown as ChartConfig;
    }
  } else if (api.type === "docs") {
    base.docs = api.docs ?? [];
    base.answerText = api.answer_text ?? undefined;
  } else if (api.type === "lineage") {
    base.lineage = api.lineage ?? undefined;
    base.answerText = api.answer_text ?? undefined;
  }
  return base;
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

  const artifacts = (data.artifacts || []).map(mapArtifact);

  // The synthesizer composes the user-facing answer; fall back if absent.
  let content = data.answer_text || "";
  if (!content) {
    if (artifacts.length === 0) {
      content = "No results returned.";
    } else {
      const sqlCount = artifacts.filter((a) => a.type === "sql").length;
      const docCount = artifacts.filter((a) => a.type === "docs").length;
      const lineageCount = artifacts.filter((a) => a.type === "lineage").length;
      const parts = [];
      if (sqlCount) parts.push(`${sqlCount} query result${sqlCount > 1 ? "s" : ""}`);
      if (docCount) parts.push(`${docCount} doc lookup${docCount > 1 ? "s" : ""}`);
      if (lineageCount) parts.push(`${lineageCount} lineage record${lineageCount > 1 ? "s" : ""}`);
      content = `Returned ${parts.join(", ")}.`;
    }
  }

  return {
    sessionId: data.session_id,
    content,
    artifacts,
    suggestions: data.suggestions || [],
    usage: data.usage?.turn,
  };
}
