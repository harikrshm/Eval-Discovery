import { useState, useId } from "react";
import { Button } from "./ui/button";
import { ScrollArea } from "./ui/scroll-area";
import { ChevronDown, ChevronUp, X } from "lucide-react";

function RunCard({ transcript, index, agent }) {
  const [expanded, setExpanded] = useState(false);
  const bodyId = useId();

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-white">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        aria-controls={bodyId}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-muted"
      >
        <span className="text-sm font-medium text-foreground">Run {index + 1}</span>
        {expanded ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground flex-shrink-0" aria-hidden="true" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground flex-shrink-0" aria-hidden="true" />
        )}
      </button>

      {expanded && (
        <div id={bodyId} className="px-4 pb-4 pt-2 space-y-2 border-t border-border">
          {transcript.map((msg, i) => (
            <div key={i} className="text-sm leading-relaxed">
              <span className="font-medium text-foreground">
                {msg.role === "user" ? agent.user_role_label : agent.name}:
              </span>{" "}
              <span className="text-muted-foreground">{msg.content}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function HistoryPanel({ agent, transcripts, onDismiss }) {
  return (
    <div className="flex flex-col h-full bg-[#fafafa]">
      {/* Header */}
      <div className="h-11 flex items-center justify-between px-4 border-b border-border flex-shrink-0">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          History
        </h2>
        <Button
          variant="ghost"
          size="icon"
          onClick={onDismiss}
          className="h-8 w-8 text-muted-foreground"
          aria-label="Close history panel"
        >
          <X className="h-4 w-4" aria-hidden="true" />
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
              <RunCard key={index} transcript={transcript} index={index} agent={agent} />
            ))}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
