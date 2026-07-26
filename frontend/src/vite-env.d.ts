/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PAPERFLOW_UI_MODE?: "local" | "cloud";
  readonly VITE_PAPERFLOW_HUB_URLS?: string;
  readonly VITE_PAPERFLOW_ALLOWED_HUB_HOSTS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
