import { useState, useRef, useEffect } from "react";
import { sendMessage } from "../api";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

const SUGGESTIONS = [
  "How much does CloudSync cost?",
  "I'm not interested right now.",
  "How are you different from Snowflake?",
];

// Matches a free-tier backend waking from sleep.
const SLOW_REPLY_MS = 4000;

function EmptyState({ onPick }) {
  return (
    <section
      aria-labelledby="intro-heading"
      className="mx-auto w-full max-w-2xl px-4 sm:px-6 pt-10 sm:pt-14"
    >
      <h2 id="intro-heading" className="text-base font-semibold text-foreground">
        Test Alex, then turn your chats into evals
      </h2>
      <p className="mt-2 text-muted-foreground">
        Alex is an AI sales rep for CloudSync. You play the prospect.
      </p>

      <ol className="mt-5 list-decimal space-y-2 pl-5 text-foreground marker:text-muted-foreground">
        <li>Chat with Alex for at least 3 messages, then select End run.</li>
        <li>Do a second run on the same topic, such as pricing or objections.</li>
        <li>When a pattern is found, select Discover evals, then export the criteria as JSON.</li>
      </ol>

      <p className="mt-6 text-sm font-medium text-foreground">Try asking</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {SUGGESTIONS.map((text) => (
          <Button
            key={text}
            variant="outline"
            size="sm"
            className="h-auto whitespace-normal py-1.5 text-left"
            onClick={() => onPick(text)}
          >
            {text}
          </Button>
        ))}
      </div>
    </section>
  );
}

export default function ChatInterface({ currentTranscript, onMessage, disabled }) {
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [slow, setSlow] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);
  const inputRef = useRef(null);
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
  }, [currentTranscript.length, loading, slow, error]);

  useEffect(() => {
    if (!loading) {
      setSlow(false);
      return;
    }
    const timer = setTimeout(() => setSlow(true), SLOW_REPLY_MS);
    return () => clearTimeout(timer);
  }, [loading]);

  async function requestReply(messages) {
    setLoading(true);
    setError(null);
    try {
      const { reply } = await sendMessage(messages);
      onMessage({ role: "assistant", content: reply });
    } catch {
      setError("Alex didn't respond. Check your connection, then try again.");
    } finally {
      setLoading(false);
    }
  }

  function handleSend() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg = { role: "user", content: text };
    onMessage(userMsg);
    setInput("");
    requestReply([...currentTranscript, userMsg]);
  }

  function handleRetry() {
    if (loading) return;
    requestReply(currentTranscript);
  }

  function applySuggestion(text) {
    setInput(text);
    inputRef.current?.focus();
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const canSend = !disabled && !loading && input.trim().length > 0;
  const canRetry = error !== null && currentTranscript.at(-1)?.role === "user";

  return (
    <div className="flex flex-col flex-1 min-h-0 overflow-hidden bg-white">
      {/* Messages scroll area */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto overscroll-contain"
      >
        {currentTranscript.length === 0 && <EmptyState onPick={applySuggestion} />}

        <div
          role="log"
          aria-label="Conversation with Alex"
          aria-live="polite"
          className="mx-auto w-full max-w-2xl space-y-4 px-4 sm:px-6 py-4"
        >
          {currentTranscript.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] px-4 py-3 text-base leading-relaxed ${
                  msg.role === "user"
                    ? "bg-[#f5f5f5] text-foreground rounded-xl rounded-br-sm"
                    : "bg-white border border-[#ebebeb] text-foreground rounded-xl rounded-bl-sm"
                }`}
              >
                <span className="sr-only">{msg.role === "user" ? "You: " : "Alex: "}</span>
                {msg.content}
              </div>
            </div>
          ))}

          {/* "Alex is typing…" indicator */}
          {loading && (
            <div className="flex justify-start">
              <div className="bg-white border border-[#ebebeb] text-muted-foreground px-4 py-3 rounded-xl rounded-bl-sm text-sm">
                <span className="sr-only">Alex is typing</span>
                <span aria-hidden="true" className="inline-flex gap-1">
                  {[0, 150, 300].map((delay) => (
                    <span
                      key={delay}
                      className="motion-safe:animate-bounce motion-reduce:animate-pulse"
                      style={{ animationDelay: `${delay}ms` }}
                    >
                      ·
                    </span>
                  ))}
                </span>
                {slow && (
                  <p className="mt-2 text-xs">
                    Waking the demo server. The first reply can take up to a minute.
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Composer */}
      <div className="px-4 pb-4 flex-shrink-0">
        <div className="mx-auto w-full max-w-2xl">
          {error && (
            <div
              role="alert"
              className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-red-700"
            >
              <span>{error}</span>
              {canRetry && (
                <Button variant="outline" size="sm" onClick={handleRetry} disabled={loading}>
                  Try again
                </Button>
              )}
            </div>
          )}

          <div className="flex items-center gap-2 rounded-full border border-input bg-[#fafafa] px-4 py-1.5 focus-within:border-[var(--ring)] focus-within:ring-2 focus-within:ring-[var(--ring)]">
            <Input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              aria-label="Message Alex"
              placeholder={disabled ? "Generating eval — please wait…" : "e.g. How much does CloudSync cost?"}
              disabled={disabled || loading}
              className="flex-1 border-0 bg-transparent shadow-none focus-visible:ring-0 h-8 px-0"
            />
            <Button
              onClick={handleSend}
              disabled={!canSend}
              size="icon"
              aria-label="Send message"
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
                aria-hidden="true"
              >
                <line x1="12" y1="19" x2="12" y2="5" />
                <polyline points="5 12 12 5 19 12" />
              </svg>
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
