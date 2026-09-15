import { FormEvent, useEffect, useState } from "react";
import { api } from "./api";
import type { SettingsStatus } from "./types";

const MODEL_HELP =
  "Common ids: claude-sonnet-4-20250514, claude-opus-4-20250514, claude-haiku-4-5-20251001";

export function SettingsPage() {
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState<"low" | "medium" | "high">("medium");

  async function refresh() {
    const s = await api.settingsStatus();
    setStatus(s);
    setModel(s.model || "claude-sonnet-4-20250514");
    setEffort((s.effort as "low" | "medium" | "high") || "medium");
  }

  useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  async function onSetup(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      await api.settingsSetup(password);
      setPassword("");
      setOkMsg("Admin password created.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onLogin(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      await api.settingsLogin(password);
      setPassword("");
      setOkMsg("Signed in.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onLogout() {
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      await api.settingsLogout();
      setOkMsg("Signed out.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSaveLlm(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      await api.settingsPutLlm({
        api_key: apiKey.trim() || undefined,
        model: model.trim() || undefined,
        effort,
      });
      setApiKey("");
      setOkMsg("Anthropic settings saved. Applied immediately (no restart).");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wrap">
      <header className="top">
        <div>
          <h1>Settings</h1>
          <div className="deck">Anthropic key, model, and effort for Claude briefs.</div>
        </div>
        <a className="chip" href="/" style={{ textDecoration: "none" }}>
          ← Workbench
        </a>
      </header>

      {error ? (
        <div className="panel">
          <p className="err">{error}</p>
        </div>
      ) : null}
      {okMsg ? (
        <div className="panel">
          <p className="ok">{okMsg}</p>
        </div>
      ) : null}

      {!status ? (
        <div className="panel">
          <p className="hint">Loading…</p>
        </div>
      ) : !status.password_configured ? (
        <div className="panel">
          <h2>Create admin password</h2>
          <p className="hint">
            First-time setup. Minimum 8 characters. This password protects Anthropic settings — it is
            never asked in chat.
          </p>
          <form className="settings-form" onSubmit={onSetup}>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={8}
                required
                autoComplete="new-password"
              />
            </label>
            <button className="primary btn" type="submit" disabled={busy || password.length < 8}>
              Create password
            </button>
          </form>
        </div>
      ) : !status.authenticated ? (
        <div className="panel">
          <h2>Admin login</h2>
          <form className="settings-form" onSubmit={onLogin}>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
            </label>
            <button className="primary btn" type="submit" disabled={busy || !password}>
              Sign in
            </button>
          </form>
        </div>
      ) : (
        <>
          <div className="panel">
            <div className="actions" style={{ marginTop: 0, justifyContent: "space-between" }}>
              <h2 style={{ margin: 0 }}>Anthropic</h2>
              <button type="button" className="btn" onClick={() => void onLogout()} disabled={busy}>
                Log out
              </button>
            </div>
            <p className="hint">
              {status.llm_configured
                ? `Key configured (…${status.key_suffix || "****"}). Leave the key blank to keep it.`
                : "No API key yet — paste one below (or set ANTHROPIC_API_KEY in env as a default)."}
            </p>
            <form className="settings-form" onSubmit={onSaveLlm}>
              <label>
                Anthropic API key
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={status.llm_configured ? `••••${status.key_suffix || ""}` : "sk-ant-…"}
                  autoComplete="off"
                />
              </label>
              <label>
                Model
                <input
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  spellCheck={false}
                  required
                />
              </label>
              <p className="hint">{MODEL_HELP}</p>
              <label>
                Effort
                <select value={effort} onChange={(e) => setEffort(e.target.value as "low" | "medium" | "high")}>
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                </select>
              </label>
              <button className="primary btn" type="submit" disabled={busy}>
                Save
              </button>
            </form>
          </div>
        </>
      )}

      <footer>
        <a href="/">← Back to Workbench</a>
      </footer>
    </div>
  );
}
