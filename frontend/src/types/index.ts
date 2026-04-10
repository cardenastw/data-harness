export interface Context {
  id: string;
  name: string;
  description: string;
}

export interface QueryResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  execution_time_ms: number;
}

export interface ChartConfig {
  chartType: "bar" | "line" | "pie" | "area" | "scatter";
  title: string;
  data: Record<string, unknown>[];
  xAxis: string;
  yAxis: string;
  xLabel?: string;
  yLabel?: string;
  colors?: string[];
}

export interface DocResult {
  path: string;
  title: string;
  snippet: string;
  content: string;
}

export interface LineageNode {
  kind: "metric" | "column" | "table";
  name: string;
  formula?: string;
  upstream_tables?: string[];
  upstream_columns?: string[];
  derived_from?: string[];
  source_system?: string;
  refresh_cadence?: string;
  primary_key?: string;
  contexts?: string[];
  notes?: string;
}

export interface Artifact {
  type: "sql" | "chart" | "docs" | "lineage";
  query?: string;
  result?: QueryResult;
  config?: ChartConfig;
  docs?: DocResult[];
  lineage?: LineageNode;
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  llm_calls: number;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  artifacts?: Artifact[];
  usage?: TokenUsage;
  suggestions?: string[];
}

export interface ChatRequest {
  message: string;
  session_id?: string;
  context_id?: string;
}

export interface ContextsResponse {
  contexts: Context[];
}
