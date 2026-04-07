import { useEffect, useRef } from "react";
import type { Message } from "../types";
import MessageBubble from "./MessageBubble";

interface Props {
  messages: Message[];
  isLoading: boolean;
}

export default function ChatWindow({ messages, isLoading }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

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
        <div className="message message-assistant">
          <div className="message-role">Assistant</div>
          <div className="thinking">Thinking...</div>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
