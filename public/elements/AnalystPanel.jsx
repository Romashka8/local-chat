import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";

// Chainlit 2.12 injects `props`, `callAction`, and `updateElement` globally.
// This is an inline message element; it does not change the Chainlit frontend.
export default function AnalystPanel() {
  const provided = props.settings || {};
  const tools = props.tools || [];
  const [tool, setTool] = useState(provided.tool || "inspect_dataset");
  const [auto, setAuto] = useState(provided.auto !== false);
  const [steps, setSteps] = useState(provided.steps !== false);
  const [temperature, setTemperature] = useState(provided.temperature ?? 0.2);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setTool(provided.tool || "inspect_dataset");
    setAuto(provided.auto !== false);
    setSteps(provided.steps !== false);
    setTemperature(provided.temperature ?? 0.2);
  }, [provided.tool, provided.auto, provided.steps, provided.temperature]);

  const save = async () => {
    setBusy(true);
    setError("");
    const next = { tool, auto, steps, temperature: Math.round(Number(temperature) * 10) / 10 };
    try {
      const result = await callAction({ name: "analyst_save_panel", payload: next });
      if (!result?.success) {
        setError("Не удалось применить настройки. Проверь логи Chainlit.");
        return;
      }
      // Keep this card aligned even after chat resume or a browser reload.
      await updateElement({ ...props, settings: next });
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="w-full max-w-2xl rounded-xl border border-border bg-card p-4 text-card-foreground shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-2">
        <div className="font-semibold text-base">Analyst · панель управления</div>
        <span className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">Настройки этого чата</span>
      </div>
      <div className="grid gap-4">
        <label className="block space-y-2">
          <span className="text-sm font-medium">Выбрать инструмент</span>
          <select
            aria-label="Analyst tool"
            value={tool}
            onChange={(event) => setTool(event.target.value)}
            disabled={auto || busy}
            className="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            {tools.map(({ value, label }) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-sm font-medium">Автоматический выбор инструментов</div>
            <div className="text-xs text-muted-foreground">Все tools доступны модели при Auto</div>
          </div>
          <Switch checked={auto} onCheckedChange={setAuto} disabled={busy} aria-label="Auto tool selection" />
        </div>
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-sm font-medium">Показывать вызовы инструментов</div>
            <div className="text-xs text-muted-foreground">Показывать выполненные tools как Chainlit Steps</div>
          </div>
          <Switch checked={steps} onCheckedChange={setSteps} disabled={busy} aria-label="Show tool calls" />
        </div>
        <div className="space-y-3">
          <div className="flex justify-between text-sm font-medium">
            <span>Temperature</span><span>{Number(temperature).toFixed(1)}</span>
          </div>
          <input
            type="range"
            value={Number(temperature)}
            min="0" max="1" step="0.1"
            onChange={(event) => setTemperature(Number(event.target.value))}
            disabled={busy}
            aria-label="Temperature"
            className="w-full cursor-pointer accent-primary"
          />
        </div>
        {error && <div role="alert" className="text-sm text-destructive">{error}</div>}
        <Button onClick={save} disabled={busy || tools.length === 0} className="w-full">
          {busy ? "Сохраняем…" : "Применить настройки"}
        </Button>
      </div>
    </div>
  );
}
