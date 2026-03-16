import { useState } from "react";
import { Button } from "./ui/button";
import { ScrollArea } from "./ui/scroll-area";
import { ChevronDown, ChevronUp } from "lucide-react";

function RunCard({ transcript, index }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-white">
      <button
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-muted/30"
      >
        <span className="text-sm font-medium text-foreground">Run {index + 1}</span>
        {expanded ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground flex-shrink-0" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground flex-shrink-0" />
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-2 space-y-2 border-t border-border">
          {transcript.map((msg, i) => (
            <div key={i} className="text-sm leading-relaxed">
              <span className="font-medium text-foreground">
                {msg.role === "user" ? "Prospect" : "Alex"}:
              </span>{" "}
              <span className="text-muted-foreground">{msg.content}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function HistoryPanel({ transcripts, onDismiss }) {
  return (
    <div className="flex flex-col h-full bg-[#fafafa]">
      {/* Header */}
      <div className="h-11 flex items-center justify-between px-4 border-b border-border flex-shrink-0">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          History
        </span>
        <Button
          variant="ghost"
          size="icon"
          onClick={onDismiss}
          className="h-7 w-7 text-muted-foreground"
          aria-label="Close history panel"
        >
          ×
        </Button>
      </div>

      {/* Body */}
      {transcripts.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center mt-12 px-4">
          No completed runs yet. End a run to see it here.
        </p>
      ) : (
        <ScrollArea className="flex-1">
          <div className="p-4 space-y-3">
            {transcripts.map((transcript, index) => (
              <RunCard key={index} transcript={transcript} index={index} />
            ))}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
