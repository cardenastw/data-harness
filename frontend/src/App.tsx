import { useState } from "react";
import "./App.css";
import { sendMessage } from "./api/client";
import ChatInput from "./components/ChatInput";
import ChatWindow from "./components/ChatWindow";
import ContextSelector from "./components/ContextSelector";
import type { Message } from "./types";

function App() {
  const [contextId, setContextId] = useState("marketing");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [statusText, setStatusText] = useState("");

  const handleContextChange = (id: string) => {
    setContextId(id);
    setSessionId(null);
    setMessages([]);
  };

  const handleSend = async (content: string) => {
    const userMessage: Message = { role: "user", content };
    const updatedMessages = [...messages, userMessage];
    setMessages(updatedMessages);
    setIsLoading(true);
    setStatusText("Running workflow...");

    try {
      const result = await sendMessage(
        content,
        sessionId ?? undefined,
        sessionId ? undefined : contextId,
      );
      setSessionId(result.sessionId);

      setMessages([
        ...updatedMessages,
        {
          role: "assistant",
          content: result.content,
          artifacts: result.artifacts,
          suggestions: result.suggestions,
          usage: result.usage,
        },
      ]);
    } catch (error) {
      setMessages([
        ...updatedMessages,
        {
          role: "assistant",
          content: `Error: ${error instanceof Error ? error.message : "Something went wrong"}`,
        },
      ]);
    } finally {
      setIsLoading(false);
      setStatusText("");
    }
  };

  return (
    <div className="app">
      <header className="app-header">
        <h1>Brewed Awakening</h1>
        <span className="app-subtitle">Data Assistant</span>
        <ContextSelector
          selectedId={contextId}
          onChange={handleContextChange}
        />
      </header>
      <ChatWindow
        messages={messages}
        isLoading={isLoading}
        statusText={statusText}
      />
      <ChatInput onSend={handleSend} disabled={isLoading} />
    </div>
  );
}

export default App;
