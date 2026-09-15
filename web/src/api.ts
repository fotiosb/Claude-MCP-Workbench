import type { Architecture, Classify, Example, Run, SettingsStatus } from "./types";

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.reason || JSON.stringify(body);
    } catch {
      /* keep statusText */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  classify(url: string) {
    return fetch(`/api/classify?url=${encodeURIComponent(url)}`).then((r) => asJson<Classify>(r));
  },
  examples() {
    return fetch("/api/examples").then((r) => asJson<{ examples: Example[] }>(r));
  },
  createRun(url: string) {
    return fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }).then((r) => asJson<{ run_id: string }>(r));
  },
  getRun(id: string) {
    return fetch(`/api/runs/${id}`).then((r) => asJson<Run>(r));
  },
  sendInput(id: string, choice: "top-level-only" | "full-tree") {
    return fetch(`/api/runs/${id}/input`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ choice }),
    }).then((r) => asJson<Run>(r));
  },
  reportUrl(id: string) {
    return `/api/runs/${id}/report`;
  },
  reportPdfUrl(id: string) {
    return `/api/runs/${id}/report.pdf`;
  },
  architecture() {
    return fetch("/api/architecture").then((r) => asJson<Architecture>(r));
  },
  events(id: string) {
    return new EventSource(`/api/runs/${id}/events`);
  },
  settingsStatus() {
    return fetch("/api/settings/status", { credentials: "include" }).then((r) =>
      asJson<SettingsStatus>(r),
    );
  },
  settingsSetup(password: string) {
    return fetch("/api/settings/setup", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }).then((r) => asJson<{ ok: boolean }>(r));
  },
  settingsLogin(password: string) {
    return fetch("/api/settings/login", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }).then((r) => asJson<{ ok: boolean }>(r));
  },
  settingsLogout() {
    return fetch("/api/settings/logout", {
      method: "POST",
      credentials: "include",
    }).then((r) => asJson<{ ok: boolean }>(r));
  },
  settingsPutLlm(body: { api_key?: string; model?: string; effort?: string }) {
    return fetch("/api/settings/llm", {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => asJson<Omit<SettingsStatus, "password_configured" | "authenticated">>(r));
  },
};
