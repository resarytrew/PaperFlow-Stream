/** API client bound to the discovered local Чистовик. */

import { buildHubHeaders, getActiveHub, withHubNetworkAccess } from "../hub/runtime";

const WS_AUTH_PREFIX = "paperflow-auth.";
let webSocketAuthInstalled = false;

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
  const hub = getActiveHub();
  const headers = buildHubHeaders(init?.headers);
  return fetch(
    apiUrl(path),
    withHubNetworkAccess(hub.baseUrl, {
      ...init,
      mode: "cors",
      cache: "no-store",
      headers,
    }),
  ).then((response) => handle<T>(response));
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
    const hub = getActiveHub();
    const response = await fetch(
      apiUrl(path),
      withHubNetworkAccess(hub.baseUrl, {
        mode: "cors",
        cache: "no-store",
        headers: buildHubHeaders(),
      }),
    );
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

/**
 * Existing scanner pages construct the native WebSocket directly. Install a
 * narrow adapter that adds Hub protocols only for the active Hub origin. Other
 * WebSocket traffic is left untouched. This keeps credentials out of URLs while
 * the UI migrates incrementally to an explicit transport factory.
 */
export function installHubWebSocketAuth(): void {
  if (webSocketAuthInstalled) return;
  webSocketAuthInstalled = true;

  const NativeWebSocket = window.WebSocket;
  const HubAwareWebSocket = function (
    this: WebSocket,
    url: string | URL,
    protocols?: string | string[],
  ): WebSocket {
    let selectedProtocols = protocols;
    try {
      const target = new URL(String(url), window.location.href);
      const hub = getActiveHub();
      const hubUrl = new URL(hub.baseUrl);
      if (selectedProtocols === undefined && target.host === hubUrl.host) {
        selectedProtocols = wsProtocols();
      }
    } catch {
      // Hub is not connected yet or URL is malformed; native constructor owns the error.
    }

    return selectedProtocols === undefined
      ? new NativeWebSocket(url)
      : new NativeWebSocket(url, selectedProtocols);
  } as unknown as typeof WebSocket;

  Object.setPrototypeOf(HubAwareWebSocket, NativeWebSocket);
  Object.defineProperty(HubAwareWebSocket, "prototype", {
    value: NativeWebSocket.prototype,
    writable: false,
  });
  window.WebSocket = HubAwareWebSocket;
}

export function sheetImageUrl(
  sheetId: number,
  kind: "source" | "normalized" | "enhanced" | "answer" | "thumbnail" | "qr",
): string {
  const hub = getActiveHub();
  const url = new URL(`${hub.baseUrl}/api/sheets/${sheetId}/image/${kind}`);
  url.searchParams.set("workspace", hub.workspaceId);
  if (hub.mediaToken) url.searchParams.set("hub_token", hub.mediaToken);
  return url.toString();
}
