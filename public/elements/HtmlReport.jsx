import { useState } from "react";
import { Button } from "@/components/ui/button";
import { FileText, PanelRightOpen } from "lucide-react";

// One persisted element provides both the preview launcher in the message
// and the viewer inside Chainlit's native right-hand ElementSidebar.
export default function HtmlReport() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const name = String(props.filename || "HTML report");

  if (props.mode === "viewer") {
    return (
      <div className="flex h-full min-h-[60vh] w-full flex-col gap-2">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <FileText className="h-4 w-4" />
          <span className="truncate" title={name}>{name}</span>
        </div>
        <iframe
          src={props.url}
          title={"HTML report: " + name}
          sandbox=""
          referrerPolicy="no-referrer"
          className="min-h-[60vh] w-full flex-1 rounded-md border border-border bg-white"
          style={{ height: "calc(100vh - 170px)" }}
        />
      </div>
    );
  }

  const open = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await callAction({
        name: "analyst_preview_html_report",
        payload: { filename: name },
      });
      if (!result?.success) setError("Не получилось открыть предпросмотр.");
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex w-full max-w-xl flex-col gap-2 rounded-lg border border-border bg-card p-3">
      <div className="flex items-center gap-2 text-sm">
        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 truncate font-medium" title={name}>{name}</span>
      </div>
      <Button onClick={open} disabled={busy} variant="outline" size="sm" className="w-fit gap-2">
        <PanelRightOpen className="h-4 w-4" />
        {busy ? "Открываем…" : "Просмотреть справа"}
      </Button>
      {error && <div role="alert" className="text-xs text-destructive">{error}</div>}
    </div>
  );
}
