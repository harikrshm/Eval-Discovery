import { Component } from "react";
import { restoreAssistBackup } from "../../lib/workspace";
import { Button } from "../ui/button";

// Catches a console that cannot be shown (usually its code chunk failed to download: offline, or a
// stale file name after a deploy) so the rest of the app stays standing. `onRetry` must make the
// children try again (ConsoleLoader makes a fresh lazy component); `onDisconnect` is the same exit
// the console's own Disconnect button takes.
export default class ConsoleBoundary extends Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  retry = () => {
    this.props.onRetry();
    this.setState({ failed: false });
  };

  disconnect = () => {
    restoreAssistBackup();
    this.props.onDisconnect();
  };

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3 p-6 text-sm text-foreground">
        <p>Couldn&apos;t load the console.</p>
        <Button type="button" variant="outline" size="sm" onClick={this.retry}>
          Retry
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={this.disconnect}>
          Disconnect
        </Button>
      </div>
    );
  }
}
