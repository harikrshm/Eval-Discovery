// Wraps a panel whose data is being refreshed behind the numbers already on screen (a new filter, a
// Refresh). While `refreshing` is true the content is marked `aria-busy`, dimmed (not disabled: clicks
// still work) and a visible "Updating…" line says the numbers are about to change. The line is one
// polite status that exists only while refreshing, so it is announced once per refresh.
//
// Props: `refreshing` (boolean, from `useConsoleQuery(...).refreshing`), `children`, `className`.
export default function RefreshFrame({ refreshing = false, className = "", children }) {
  return (
    <div aria-busy={refreshing ? "true" : undefined} className={className}>
      {refreshing ? (
        <p role="status" className="mb-2 text-xs font-medium text-muted-foreground">
          Updating…
        </p>
      ) : null}
      <div data-refresh-content className={refreshing ? "opacity-60 transition-opacity" : ""}>
        {children}
      </div>
    </div>
  );
}
