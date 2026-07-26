/** API client bound to the discovered local PaperFlow Hub. */

import { buildHubHeaders, getActiveHub } from "../hub/runtime";

const WS_AUTH_PREFIX = "paperflow-auth.";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function apiUrl(path: string): string {
  const hub = getActiveHub();
  return `${hub.baseUrl}/api${path}`;
}

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not json */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = buildHubHeaders(init?.headers);
  return fetch(apiUrl(path), {
    ...init,
    mode: "cors",
    cache: "no-store",
    headers,
  }).then((response) => handle<T>(response));
}

export const api = {
  get: <T>(path: string) => request<T>(path),

  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),

  patch: <T>(path: string, body: unknown) =>
    request<T>(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  put: <T>(path: string, body: unknown) =>
    request<T>(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  delete: (path: string) => request<void>(path, { method: "DELETE" }),

  /** Download a binary endpoint and trigger the browser save-file flow. */
  async download(path: string, fallbackName: string): Promise<void> {
    const response = await fetch(apiUrl(path), {
      mode: "cors",
      cache: "no-store",
      headers: buildHubHeaders(),
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        if (body?.detail) detail = body.detail;
      } catch {
        /* ignore */
      }
      throw new ApiError(response.status, detail);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const match = /filename="?([^";]+)"?/.exec(disposition);
    const name = match ? match[1] : fallbackName;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },
};

/** Build a WebSocket URL without credentials in the query string. */
export function wsUrl(path: string): string {
  const hub = getActiveHub();
  const url = new URL(`${hub.baseUrl}/api${path}`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("workspace", hub.workspaceId);
  return url.toString();
}

/** Carry the bearer token in a WebSocket subprotocol header, not access logs. */
export function wsProtocols(): string[] {
  const hub = getActiveHub();
  return hub.token ? ["paperflow.v1", `${WS_AUTH_PREFIX}${hub.token}`] : ["paperflow.v1"];
}

export function sheetImageUrl(
  sheetId: number,
  kind: "source" | "normalized" | "enhanced" | "answer" | "thumbnail" | "qr",
): string {
  const hub = getActiveHub();
  const url = new URL(`${hub.baseUrl}/api/sheets/${sheetId}/image/${kind}`);
  url.searchParams.set("workspace", hub.workspaceId);
  if (hub.token) url.searchParams.set("hub_token", hub.token);
  return url.toString();
}
