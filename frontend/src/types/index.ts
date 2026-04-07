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

export interface Artifact {
  type: "sql" | "chart";
  query?: string;
  result?: QueryResult;
  config?: ChartConfig;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  artifacts?: Artifact[];
}

export interface ChatRequest {
  context_id: string;
  messages: { role: string; content: string }[];
}

export interface ChatResponse {
  message: { role: string; content: string };
  artifacts: Artifact[];
}

export interface ContextsResponse {
  contexts: Context[];
}
