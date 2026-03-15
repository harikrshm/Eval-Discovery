import { useState } from "react";
import { sendMessage } from "../api";

export default function ChatInterface({ currentTranscript, onMessage, disabled }) {
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

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

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {currentTranscript.length === 0 && (
          <p className="text-gray-400 text-sm text-center mt-8">
            Start a conversation with Alex to begin a preview run.
          </p>
        )}
        {currentTranscript.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-xs lg:max-w-md px-4 py-2 rounded-2xl text-sm ${
                msg.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-gray-100 text-gray-800"
              }`}
            >
              <p className="text-xs font-semibold mb-1 opacity-60">
                {msg.role === "user" ? "You (Prospect)" : "Alex"}
              </p>
              {msg.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-100 text-gray-500 px-4 py-2 rounded-2xl text-sm">
              Alex is typing…
            </div>
          </div>
        )}
      </div>

      {error && (
        <p className="text-red-500 text-xs px-4 pb-2">{error}</p>
      )}

      <div className="border-t p-4 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? "Save this run to start a new one" : "Reply as the prospect…"}
          disabled={disabled || loading}
          className="flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
        />
        <button
          onClick={handleSend}
          disabled={disabled || loading || !input.trim()}
          className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Send
        </button>
      </div>
    </div>
  );
}
