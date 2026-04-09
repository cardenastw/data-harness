import type { ChatRequest, ContextsResponse, Artifact, TokenUsage } from "../types";

const API_BASE = "/api";

export async function fetchContexts(): Promise<ContextsResponse> {
  const res = await fetch(`${API_BASE}/contexts`);
  if (!res.ok) throw new Error(`Failed to fetch contexts: ${res.statusText}`);
  return res.json();
}

export interface StreamCallbacks {
  onSession?: (sessionId: string) => void;
  onStatus: (message: string) => void;
  onArtifact: (artifact: Artifact) => void;
  onContent: (text: string) => void;
  onUsage?: (usage: TokenUsage) => void;
  onDone: () => void;
  onError: (error: string) => void;
}

export async function sendMessageStream(
  request: ChatRequest,
  callbacks: StreamCallbacks
): Promise<void> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!res.ok) {
    const detail = await res.text();
    callbacks.onError(`Chat request failed: ${detail}`);
    return;
  }

  const reader = res.body?.getReader();
  if (!reader) {
    callbacks.onError("No response body");
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Parse SSE events from buffer
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";

    for (const part of parts) {
      const lines = part.split("\n");
      let eventType = "";
      let data = "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          eventType = line.slice(7);
        } else if (line.startsWith("data: ")) {
          data = line.slice(6);
        }
      }

      if (!eventType || !data) continue;

      try {
        const parsed = JSON.parse(data);

        switch (eventType) {
          case "session":
            callbacks.onSession?.(parsed.session_id);
            break;
          case "status":
            callbacks.onStatus(parsed.message);
            break;
          case "artifact":
            callbacks.onArtifact(parsed);
            break;
          case "content":
            callbacks.onContent(parsed.text);
            break;
          case "usage":
            callbacks.onUsage?.(parsed);
            break;
          case "done":
            callbacks.onDone();
            break;
          case "error":
            callbacks.onError(parsed.message);
            break;
        }
      } catch {
        // skip malformed events
      }
    }
  }
}
