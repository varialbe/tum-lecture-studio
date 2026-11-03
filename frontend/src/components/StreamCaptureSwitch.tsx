import { useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Switch } from "./ui";

/** One switch for stream recording; saves immediately and updates every page. */
export function StreamCaptureSwitch({ label = "Stream recording", className }: { label?: string; className?: string }) {
  const { system, refreshSystem, toast } = useStore();
  const [busy, setBusy] = useState(false);
  if (!system) return null;
  const on = !!system.settings.stream_capture;
  const set = async (v: boolean) => {
    setBusy(true);
    try {
      await api.put("/api/settings", { values: { "download.stream_capture": v } });
      await refreshSystem();
      toast(
        v
          ? "Stream recording is on. Lectures without download permission now show Record. Keep the files to yourself."
          : "Stream recording is off.",
        "ok",
      );
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setBusy(false);
    }
  };
  return (
    <span title="Record the stream of lectures whose lecturer disabled downloads (personal study only)">
      <Switch checked={on} onChange={set} busy={busy} label={label} className={className} />
    </span>
  );
}
