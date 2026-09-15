import type { Findings } from "../types";

export function Findings({ findings }: { findings: Findings }) {
  const secrets = findings.secrets || [];
  const manifests = findings.manifests || [];
  const ci = findings.ci || [];
  const tests = findings.tests;
  const testsLabel = tests?.present ? "yes" : "no";

  return (
    <div className="findings">
      <p className="findings-summary">
        {manifests.length} manifests · {ci.length} workflows · tests {testsLabel} ·{" "}
        {secrets.length} credential signals
      </p>
      <div className="card">
        <div className="sev">Manifests</div>
        {manifests.length === 0 ? (
          <p>None found.</p>
        ) : (
          <ul>
            {manifests.map((m) => (
              <li key={m.path}>
                <code>{m.path}</code> ({m.name})
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="card">
        <div className="sev">CI</div>
        {ci.length === 0 ? (
          <p>No .github/workflows files.</p>
        ) : (
          <ul>
            {ci.map((w) => (
              <li key={w.path}>
                <code>{w.path}</code>
                {w.jobs?.length ? ` — ${w.jobs.join(", ")}` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="card">
        <div className="sev">Tests</div>
        {tests?.present ? (
          <p>
            {tests.test_file_count} test-like files
            {tests.test_dirs?.length ? ` · ${tests.test_dirs.join(" ")}` : ""}
            {tests.frameworks_guess?.length ? ` · ${tests.frameworks_guess.join("; ")}` : ""}
          </p>
        ) : (
          <p>No conventional test layout.</p>
        )}
      </div>
      <div className="card">
        <div className="sev">Credential signals</div>
        {secrets.length === 0 ? (
          <p>None in project source (dependency trees are ignored).</p>
        ) : (
          secrets.map((s, i) => (
            <p key={i}>
              <span className={`sev ${s.severity}`}>{s.severity}</span> {s.kind} · <code>{s.path}</code>
              <br />
              {s.detail}
              {s.sample ? (
                <>
                  {" "}
                  <code>{s.sample}</code>
                </>
              ) : null}
            </p>
          ))
        )}
      </div>
    </div>
  );
}
