import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import { installHubWebSocketAuth } from "./api/client";
import { HubConnectionGate, HubProvider } from "./hub/HubProvider";
import "./styles.css";

installHubWebSocketAuth();

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js");
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HubProvider>
      <HubConnectionGate>
        <HashRouter>
          <App />
        </HashRouter>
      </HubConnectionGate>
    </HubProvider>
  </React.StrictMode>,
);
