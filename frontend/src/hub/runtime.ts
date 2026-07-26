export interface HubInfo {
  product: string;
  protocolVersion: number;
  version: string;
  installationId: string;
  deploymentMode: "personal" | "school";
  workspace: {
    id: string;
    scopeHeader: string;
    multiWorkspaceEnabled: boolean;
  };
  authorization: {
    required: boolean;
    authorized: boolean;
    tokenHeader: string;
    webSocketQueryParameter: string;
    pairingSupported: boolean;
  };
  capabilities: Record<string, boolean>;
  privacy: {
    studentDataLocation: "local-hub";
    cloudStudentDataTransfer: false;
    cloudControlPlane: "metadata-only";
  };
}

export interface HubConnection {
  baseUrl: string;
  token: string;
  workspaceId: string;
  info: HubInfo;
}

export interface PairingChallenge {
  challengeId: string;
  expiresAt: string;
  codeLength: number;
  displayRequired: boolean;
  displayUrl: string;
  devCode?: string;
}

const SELECTED_HUB_KEY = "paperflow.hub.url";
const TOKEN_PREFIX = "paperflow.hub.token.";
const DEFAULT_WORKSPACE = "personal";

let activeConnection: HubConnection | null = null;

function normalizeUrl(value: string): string {
  const url = new URL(value, window.location.href);
  url.hash = "";
  url.search = "";
  return url.toString().replace(/\/$/, "");
}

function isLoopback(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return host === "localhost" || host === "::1" || host.startsWith("127.");
}

function isPrivateIpv4(hostname: string): boolean {
  const parts = hostname.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  if (parts[0] === 10) return true;
  if (parts[0] === 192 && parts[1] === 168) return true;
  return parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31;
}

function explicitAllowedHosts(): Set<string> {
  return new Set(
    String(import.meta.env.VITE_PAPERFLOW_ALLOWED_HUB_HOSTS ?? "")
      .split(",")
      .map((value) => value.trim().toLowerCase())
      .filter(Boolean),
  );
}

export function isAllowedHubUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    const privateHost =
      isLoopback(host) ||
      isPrivateIpv4(host) ||
      host.endsWith(".local") ||
      explicitAllowedHosts().has(host);
    if (!privateHost) return false;
    if (url.protocol === "https:") return true;
    return url.protocol === "http:" && (isLoopback(host) || import.meta.env.DEV);
  } catch {
    return false;
  }
}

function uiMode(): "local" | "cloud" {
  const configured = String(import.meta.env.VITE_PAPERFLOW_UI_MODE ?? "").toLowerCase();
  if (configured === "local" || configured === "cloud") return configured;
  return isLoopback(window.location.hostname) || isPrivateIpv4(window.location.hostname) || window.location.hostname.endsWith(".local")
    ? "local"
    : "cloud";
}

function tokenKey(baseUrl: string): string {
  return `${TOKEN_PREFIX}${baseUrl}`;
}

function readToken(baseUrl: string): string {
  return window.localStorage.getItem(tokenKey(baseUrl)) ?? "";
}

function candidateUrls(): string[] {
  const values: string[] = [];
  const selected = window.localStorage.getItem(SELECTED_HUB_KEY);
  if (selected) values.push(selected);

  const configured = String(import.meta.env.VITE_PAPERFLOW_HUB_URLS ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  values.push(...configured);

  if (uiMode() === "local") values.push(window.location.origin);
  values.push("https://127.0.0.1:17841", "https://localhost:17841");

  return [...new Set(values.map(normalizeUrl))].filter(isAllowedHubUrl);
}

function hubHeaders(baseUrl: string, workspaceId = DEFAULT_WORKSPACE): HeadersInit {
  const token = readToken(baseUrl);
  return {
    "X-PaperFlow-Workspace": workspaceId,
    ...(token ? { "X-PaperFlow-Hub-Token": token } : {}),
  };
}

export async function probeHub(baseUrl: string): Promise<HubConnection> {
  const normalized = normalizeUrl(baseUrl);
  if (!isAllowedHubUrl(normalized)) {
    throw new Error("Адрес Hub должен указывать на этот компьютер или частную школьную сеть");
  }

  const response = await fetch(`${normalized}/api/hub/info`, {
    method: "GET",
    mode: "cors",
    cache: "no-store",
    headers: hubHeaders(normalized),
  });
  if (!response.ok) throw new Error(`PaperFlow Hub ответил с кодом ${response.status}`);
  const info = (await response.json()) as HubInfo;
  if (info.product !== "PaperFlow Hub" || info.protocolVersion !== 1) {
    throw new Error("Обнаружен несовместимый локальный сервис");
  }

  const connection: HubConnection = {
    baseUrl: normalized,
    token: readToken(normalized),
    workspaceId: info.workspace.id || DEFAULT_WORKSPACE,
    info,
  };
  activeConnection = connection;
  window.localStorage.setItem(SELECTED_HUB_KEY, normalized);
  return connection;
}

export async function discoverHub(): Promise<HubConnection> {
  const errors: string[] = [];
  for (const candidate of candidateUrls()) {
    try {
      return await probeHub(candidate);
    } catch (error) {
      errors.push(`${candidate}: ${(error as Error).message}`);
    }
  }
  throw new Error(errors[errors.length - 1] ?? "PaperFlow Hub не найден");
}

export async function beginPairing(connection: HubConnection, clientName = "PaperFlow Web"): Promise<PairingChallenge> {
  const response = await fetch(`${connection.baseUrl}/api/hub/pair/start`, {
    method: "POST",
    mode: "cors",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-PaperFlow-Workspace": connection.workspaceId,
    },
    body: JSON.stringify({
      client_name: clientName,
      workspace_id: connection.workspaceId,
    }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Не удалось начать подключение к Hub");
  }
  return (await response.json()) as PairingChallenge;
}

export async function finishPairing(
  connection: HubConnection,
  challenge: PairingChallenge,
  code: string,
): Promise<HubConnection> {
  const response = await fetch(`${connection.baseUrl}/api/hub/pair/confirm`, {
    method: "POST",
    mode: "cors",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-PaperFlow-Workspace": connection.workspaceId,
    },
    body: JSON.stringify({
      challenge_id: challenge.challengeId,
      code,
      workspace_id: connection.workspaceId,
    }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Код подключения не принят");
  }
  const body = (await response.json()) as { token: string };
  window.localStorage.setItem(tokenKey(connection.baseUrl), body.token);
  return probeHub(connection.baseUrl);
}

export function getActiveHub(): HubConnection {
  if (!activeConnection) throw new Error("PaperFlow Hub ещё не подключён");
  return activeConnection;
}

export function clearHubConnection(): void {
  if (activeConnection) {
    window.localStorage.removeItem(tokenKey(activeConnection.baseUrl));
  }
  activeConnection = null;
  window.localStorage.removeItem(SELECTED_HUB_KEY);
}

export function buildHubHeaders(extra?: HeadersInit): Headers {
  const hub = getActiveHub();
  const headers = new Headers(extra);
  headers.set("X-PaperFlow-Workspace", hub.workspaceId);
  if (hub.token) headers.set("X-PaperFlow-Hub-Token", hub.token);
  return headers;
}
