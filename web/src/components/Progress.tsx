const STAGES = [
  "queued",
  "resolving",
  "cloning",
  "walking",
  "input_required",
  "scanning:manifests",
  "scanning:ci",
  "scanning:secrets",
  "scanning:tests",
  "writing",
  "briefing",
];

export function Progress({ stage, status, message }: { stage?: string; status?: string; message?: string }) {
  const current = stage || "queued";
  const idx = STAGES.findIndex((s) => s === current || (s.startsWith("scanning:") && current === s));
  const failed = status === "failed";
  const done = status === "completed";
  const inputWait = status === "input_required" || current === "input_required";
  return (
    <div>
      <div className="progress">
        {STAGES.map((s, i) => {
          let cls = "";
          if (failed && (s === current || (current === "failed" && i === STAGES.length - 1))) cls = "fail";
          else if (done || (idx >= 0 && i < idx)) cls = "done";
          else if (i === idx || s === current) cls = "on";
          else if (inputWait && s === "input_required") cls = "on";
          const label = s === "input_required" ? "input" : s.replace("scanning:", "");
          return (
            <span key={s} className={cls}>
              {label}
            </span>
          );
        })}
      </div>
      {message ? <p className={failed ? "err" : undefined}>{message}</p> : null}
    </div>
  );
}
