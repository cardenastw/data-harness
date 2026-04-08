import { useState } from "react";
import "./App.css";
import { sendMessageStream } from "./api/client";
import ChatInput from "./components/ChatInput";
import ChatWindow from "./components/ChatWindow";
import ContextSelector from "./components/ContextSelector";
import type { Message, Artifact } from "./types";

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
    setStatusText("Thinking...");

    // Create a placeholder assistant message that we'll build up
    const streamArtifacts: Artifact[] = [];
    let streamContent = "";

    const addAssistantMessage = (content: string, artifacts: Artifact[]) => {
      setMessages([
        ...updatedMessages,
        { role: "assistant", content, artifacts: [...artifacts] },
      ]);
    };

    try {
      await sendMessageStream(
        {
          message: content,
          session_id: sessionId ?? undefined,
          context_id: sessionId ? undefined : contextId,
        },
        {
          onSession: (id) => {
            setSessionId(id);
          },
          onStatus: (message) => {
            setStatusText(message);
          },
          onArtifact: (artifact) => {
            streamArtifacts.push(artifact);
            addAssistantMessage(streamContent, streamArtifacts);
          },
          onContent: (text) => {
            streamContent = text;
            addAssistantMessage(streamContent, streamArtifacts);
          },
          onDone: () => {
            setIsLoading(false);
            setStatusText("");
          },
          onError: (error) => {
            streamContent = `Error: ${error}`;
            addAssistantMessage(streamContent, streamArtifacts);
            setIsLoading(false);
            setStatusText("");
          },
        }
      );
    } catch (error) {
      addAssistantMessage(
        `Error: ${error instanceof Error ? error.message : "Something went wrong"}`,
        []
      );
      setIsLoading(false);
      setStatusText("");
    }
  };

  return (
    <div className="app">
      <header className="app-header">
        <h1>Brewed Awakening</h1>
        <span className="app-subtitle">Data Assistant</span>
        <ContextSelector selectedId={contextId} onChange={handleContextChange} />
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
