import type { ArchEvent } from "../types";

export function Inspector({ events }: { events: ArchEvent[] }) {
  if (!events.length) {
    return <p style={{ color: "#8b90a0" }}>No events yet. Run an audit; the last 20 real events appear here.</p>;
  }
  return (
    <div className="inspector">
      {events.map((e) => (
        <div key={e.id}>
          <span className="ts">{new Date(e.created_at * 1000).toISOString().slice(11, 19)}</span>
          <span>
            <code>{e.stage}</code> {e.message}
            {e.run_id ? (
              <>
                {" "}
                <code>{e.run_id.slice(0, 8)}</code>
              </>
            ) : null}
          </span>
        </div>
      ))}
    </div>
  );
}
