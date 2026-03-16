import { useState, useRef, useEffect } from "react";
import { sendMessage } from "../api";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

export default function ChatInterface({ currentTranscript, onMessage, disabled }) {
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);
  const isAtBottomRef = useRef(true);

  // Track whether user is at the bottom of the scroll container
  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  }

  // Auto-scroll to bottom only when already at bottom
  useEffect(() => {
    if (isAtBottomRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [currentTranscript.length, loading]);

  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg = { role: "user", content: text };
    onMessage(userMsg);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const allMessages = [...currentTranscript, userMsg];
      const { reply } = await sendMessage(allMessages);
      onMessage({ role: "assistant", content: reply });
    } catch {
      setError("Failed to reach Alex. Check that the backend is running.");
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const canSend = !disabled && !loading && input.trim().length > 0;

  return (
    <div className="flex flex-col flex-1 overflow-hidden bg-white">
      {/* Messages scroll area */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-6 py-4 space-y-4"
      >
        {currentTranscript.length === 0 && (
          <p className="text-muted-foreground text-sm text-center mt-12">
            Start a conversation with Alex to begin a preview run.
          </p>
        )}

        {currentTranscript.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[72%] px-4 py-3 text-base leading-relaxed ${
                msg.role === "user"
                  ? "bg-[#f5f5f5] text-foreground rounded-xl rounded-br-sm"
                  : "bg-white border border-[#ebebeb] text-foreground rounded-xl rounded-bl-sm"
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}

        {/* "Alex is typing…" indicator */}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-white border border-[#ebebeb] text-muted-foreground px-3 py-2 rounded-xl rounded-bl-sm text-sm">
              <span className="inline-flex gap-1">
                <span className="animate-bounce" style={{ animationDelay: "0ms" }}>·</span>
                <span className="animate-bounce" style={{ animationDelay: "150ms" }}>·</span>
                <span className="animate-bounce" style={{ animationDelay: "300ms" }}>·</span>
              </span>
            </div>
          </div>
        )}
      </div>

      {error && (
        <p className="text-red-500 text-xs px-6 pb-2">{error}</p>
      )}

      {/* Pill-shaped input bar */}
      <div className="px-4 pb-4 flex-shrink-0">
        <div className="flex items-center gap-2 bg-[#fafafa] border border-[#e8e8e8] rounded-full px-4 py-1.5">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={disabled ? "Generating eval — please wait…" : "Message Alex…"}
            disabled={disabled || loading}
            className="flex-1 border-0 bg-transparent shadow-none focus-visible:ring-0 text-sm h-8 px-0"
          />
          <Button
            onClick={handleSend}
            disabled={!canSend}
            size="icon"
            className="rounded-full h-8 w-8 flex-shrink-0"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-4 w-4"
            >
              <line x1="12" y1="19" x2="12" y2="5" />
              <polyline points="5 12 12 5 19 12" />
            </svg>
          </Button>
        </div>
      </div>
    </div>
  );
}
