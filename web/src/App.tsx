import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { ArchitectureSvg } from "./components/Architecture";
import { Brief } from "./components/Brief";
import { ExampleTile } from "./components/ExampleTile";
import { Findings } from "./components/Findings";
import { Inspector } from "./components/Inspector";
import { Progress } from "./components/Progress";
import { TreeView } from "./components/TreeView";
import { SettingsPage } from "./SettingsPage";
import type { Architecture, Example, Run } from "./types";

const FALLBACK_EXAMPLES: Example[] = [
  {
    id: "video-analysis",
    owner: "fotiosb",
    title: "Multi-Agent Behavioral Video Analysis",
    url: "https://github.com/fotiosb/Multi-Agent-Behavioral-Video-Analysis",
    blurb: "RTSP anomaly detection with YOLO + Gemini + Claude. FastAPI / React.",
    size: "small",
    image: "/examples/video-analysis.svg",
  },
  {
    id: "proxy-aggregator",
    owner: "fotiosb",
    title: "Residential Proxy Aggregator",
    url: "https://github.com/fotiosb/residential-proxy-aggregator",
    blurb: "Windows edge nodes into a SOCKS5 pool. Ships ~27MB binaries — cache warm.",
    size: "~27MB binaries",
    image: "/examples/proxy-aggregator.svg",
  },
  {
    id: "mac-presenter",
    owner: "fotiosb",
    title: "MacPresenterView",
    url: "https://github.com/fotiosb/MacPresenterView",
    blurb: "Slides to NDI notes. Node + Chrome extension.",
    size: "small",
    image: "/examples/mac-presenter.svg",
  },
];

export default function App() {
  if (typeof window !== "undefined" && window.location.pathname === "/settings") {
    return <SettingsPage />;
  }
  return <Workbench />;
}

function Workbench() {
  const [examples, setExamples] = useState<Example[]>(FALLBACK_EXAMPLES);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [arch, setArch] = useState<Architecture | null>(null);
  const startLock = useRef(false);
  const inputLock = useRef(false);

  useEffect(() => {
    api.examples().then((r) => setExamples(r.examples)).catch(() => undefined);
    api.architecture().then(setArch).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!run?.id) return;
    // Subscribe once per run id. Do not re-open on status changes (queued→working→
    // input_required would otherwise thrash EventSource and drop events).
    if (run.status === "completed" || run.status === "failed" || run.status === "cancelled") return;
    let closed = false;
    const es = api.events(run.id);
    const refresh = () => {
      api
        .getRun(run.id)
        .then((next) => {
          if (closed) return;
          setRun(next);
          if (next.status === "completed" || next.status === "failed" || next.status === "cancelled") {
            es.close();
            api.architecture().then(setArch).catch(() => undefined);
          }
        })
        .catch(() => undefined);
    };
    es.addEventListener("progress", refresh);
    es.addEventListener("done", refresh);
    es.onerror = () => refresh();
    return () => {
      closed = true;
      es.close();
    };
  }, [run?.id]); // only remount on new run id — not on status changes

  async function start(target: string) {
    // Ref lock: React setState(busy) is async; two fast tile clicks both enter otherwise.
    if (startLock.current) return;
    startLock.current = true;
    setError(null);
    setBusy(true);
    try {
      const classified = await api.classify(target);
      if (!classified.accepted) {
        setError(classified.reason);
        setRun(null);
        return;
      }
      const created = await api.createRun(target);
      const next = await api.getRun(created.run_id);
      setRun(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      startLock.current = false;
      setBusy(false);
    }
  }

  function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    if (url.trim()) void start(url.trim());
  }

  async function onInput(choice: "top-level-only" | "full-tree") {
    if (!run || inputLock.current) return;
    inputLock.current = true;
    try {
      const next = await api.sendInput(run.id, choice);
      setRun(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      inputLock.current = false;
    }
  }

  const canDownload = Boolean(run && run.status === "completed");

  const mcpUrl = useMemo(() => {
    if (typeof window === "undefined") return "/mcp";
    return `${window.location.origin}/mcp`;
  }, []);

  return (
    <div className="wrap">
      <header className="top">
        <div>
          <h1>MCP Workbench</h1>
          <div className="deck">Public GitHub repo audit, over MCP.</div>
        </div>
        <div className="top-actions">
          <a className="chip link-chip" href="/settings">Settings</a>
          <span className="chip">GitHub repo</span>
        </div>
      </header>

      <section className="intro">
        <p>
          This is a live MCP host. Give it a public GitHub repository URL. The host
          classifies the URL, starts a Task, shallow-clones the repo, and scans it
          without executing anything in the clone. Findings and the tree are published
          as MCP resources (<code>audit://</code>, <code>tree://</code>,{" "}
          <code>findings://</code>). The same host speaks Streamable HTTP at{" "}
          <code>/mcp</code> (spec 2026-07-28).
        </p>
        <p>A tool, not a startup. Caps instead of accounts. Public repos only.</p>
      </section>

      <div className="tiles">
        {examples.map((ex) => (
          <ExampleTile key={ex.id} example={ex} onPick={(u) => { setUrl(u); void start(u); }} />
        ))}
      </div>

      <div className="url-block">
        <label className="url-label" htmlFor="github-url">Paste a public GitHub repo URL</label>
        <form className="row url-row" onSubmit={onSubmit}>
          <input
            id="github-url"
            className="url-input"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/owner/repo"
            spellCheck={false}
            maxLength={512}
          />
          <button className="primary" type="submit" disabled={busy || !url.trim()}>
            Run
          </button>
        </form>
        <p className="url-hint">
          Accepts <code>https://github.com/{"{owner}"}/{"{repo}"}</code> and optional{" "}
          <code>/tree/{"{branch}"}</code>. Rejects gist, issues, PRs, blob, GitLab, SSH.
        </p>
      </div>

      {error ? (
        <div className="panel">
          <p className="err">{error}</p>
        </div>
      ) : null}

      {run ? (
        <>
          <div className="panel">
            <h2>Progress</h2>
            <Progress stage={run.stage} status={run.status} message={run.progress_message || run.error || undefined} />
            {run.cache_hit ? <p className="hint">Served from cache (Claude skipped).</p> : null}
          </div>

          {run.status === "input_required" && run.input_request ? (
            <div className="panel">
              <h2>{run.input_request.title}</h2>
              <p>{run.input_request.message}</p>
              <div className="actions">
                {run.input_request.choices.map((ch) => (
                  <button key={ch.id} className="primary" type="button" onClick={() => void onInput(ch.id as "top-level-only" | "full-tree")}>
                    {ch.label}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {run.findings ? (
            <div className="panel">
              <h2>Findings</h2>
              <Findings findings={run.findings} />
            </div>
          ) : null}

          {run.brief ? (
            <div className="panel">
              <h2>Brief</h2>
              <Brief text={run.brief} source={run.brief_source} />
            </div>
          ) : null}

          {run.tree ? (
            <div className="panel">
              <h2>Tree</h2>
              <TreeView tree={run.tree} />
            </div>
          ) : null}

          {canDownload ? (
            <div className="panel">
              <h2>Report</h2>
              <div className="actions">
                <a
                  className="btn primary"
                  href={api.reportPdfUrl(run.id)}
                  target="_blank"
                  rel="noopener"
                >
                  Open PDF
                </a>
                <a
                  className="btn"
                  href={api.reportPdfUrl(run.id)}
                  download={`${run.owner || "repo"}-${run.repo || "audit"}-audit.pdf`}
                >
                  Download PDF
                </a>
              </div>
            </div>
          ) : null}
        </>
      ) : null}

      <section className="how">
        <h2>How this works</h2>
        <p className="intro">
          Browser talks to the FastAPI host. The host starts a Task. The worker
          resolves GitHub unauthenticated, shallow-clones, walks, scans in
          parallel, writes a report, then a brief. Resources stay in SQLite WAL.
          The MCP endpoint is the same process.
        </p>
        <ArchitectureSvg />
        <div className="panel">
          <h2>Inspector — last 20 real events</h2>
          <Inspector events={arch?.inspector || []} />
          {arch ? (
            <p className="hint">
              MCP mode: {arch.mcp_mode} · {arch.protocol} · Tasks: {arch.tasks}
              {arch.tools && arch.tools.length
                ? ` · ${arch.tools.length} tools: ${arch.tools.join(", ")}`
                : ""}
            </p>
          ) : null}
        </div>
        <div className="add">
          <strong>Add to Claude.</strong> Streamable HTTP endpoint{" "}
          <code>{mcpUrl}</code>, protocol 2026-07-28. Use the <code>repo-audit</code>{" "}
          prompt. Poll <code>tasks/get</code> — Tasks are spec-shaped, SDK-incomplete.
          No Apps iframe; the tree is a host widget + <code>ui://audit-tree/{"{run}"}</code>.
        </div>
      </section>

      <footer>
        MCP Workbench v1 · public GitHub only · MIT ·{" "}
        <a href="/settings">Settings</a>
      </footer>
    </div>
  );
}
