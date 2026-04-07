import { useEffect, useRef } from "react";
import type { Message } from "../types";
import MessageBubble from "./MessageBubble";

interface Props {
  messages: Message[];
  isLoading: boolean;
  statusText: string;
}

export default function ChatWindow({ messages, isLoading, statusText }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading, statusText]);

  return (
    <div className="chat-window">
      {messages.length === 0 && (
        <div className="chat-empty">
          <h2>Brewed Awakening Data Assistant</h2>
          <p>Select a context above and ask questions about your data.</p>
          <div className="suggestions">
            <p>Try asking:</p>
            <ul>
              <li>"How many orders did we have last month?"</li>
              <li>"What are our top selling products?"</li>
              <li>"Show me revenue by location"</li>
              <li>"Which products have low inventory?"</li>
            </ul>
          </div>
        </div>
      )}
      {messages.map((msg, i) => (
        <MessageBubble key={i} message={msg} />
      ))}
      {isLoading && (
        <div className="status-bar">
          <div className="status-spinner" />
          <span>{statusText || "Thinking..."}</span>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
