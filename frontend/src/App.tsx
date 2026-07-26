import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import DashboardPage from "./pages/DashboardPage";
import CatalogPage from "./pages/CatalogPage";
import StudentPage from "./pages/StudentPage";
import FormsPage from "./pages/FormsPage";
import SessionsPage from "./pages/SessionsPage";
import ScanPage from "./pages/ScanPage";
import ReviewPage from "./pages/ReviewPage";
import SummaryPage from "./pages/SummaryPage";
import CalibrationPage from "./pages/CalibrationPage";
import SettingsPage from "./pages/SettingsPage";
import { useHub } from "./hub/HubProvider";

type IconName = "home" | "sessions" | "catalog" | "forms" | "camera" | "settings";

const links: Array<{ to: string; label: string; icon: IconName }> = [
  { to: "/dashboard", label: "Главная", icon: "home" },
  { to: "/sessions", label: "Сессии", icon: "sessions" },
  { to: "/catalog", label: "Классы и задания", icon: "catalog" },
  { to: "/forms", label: "Бланки", icon: "forms" },
  { to: "/calibration", label: "Калибровка", icon: "camera" },
  { to: "/settings", label: "Настройки", icon: "settings" },
];

function NavIcon({ name }: { name: IconName }) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };

  if (name === "home") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path {...common} d="M3.5 10.6 12 3.8l8.5 6.8v8.6a1.6 1.6 0 0 1-1.6 1.6H5.1a1.6 1.6 0 0 1-1.6-1.6z" />
        <path {...common} d="M9 20.8v-6.2h6v6.2" />
      </svg>
    );
  }

  if (name === "sessions") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect {...common} x="4" y="3.5" width="16" height="17" rx="2.5" />
        <path {...common} d="M8 8h8M8 12h8M8 16h5" />
      </svg>
    );
  }

  if (name === "catalog") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path {...common} d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v16H6.5A2.5 2.5 0 0 0 4 21.5z" />
        <path {...common} d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v16h4.5a2.5 2.5 0 0 1 2.5 2.5z" />
      </svg>
    );
  }

  if (name === "forms") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path {...common} d="M6 3.5h8l4 4v13H6z" />
        <path {...common} d="M14 3.5v4h4M9 12h6M9 16h6" />
      </svg>
    );
  }

  if (name === "camera") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path {...common} d="M4 8.5h3l1.5-2h7l1.5 2h3v10H4z" />
        <circle {...common} cx="12" cy="13.5" r="3.2" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle {...common} cx="12" cy="12" r="3" />
      <path {...common} d="M19.4 15a1.8 1.8 0 0 0 .36 2l.05.05-2.8 2.8-.05-.05a1.8 1.8 0 0 0-2-.36 1.8 1.8 0 0 0-1.1 1.65V21h-4v-.09A1.8 1.8 0 0 0 8.75 19.3a1.8 1.8 0 0 0-2 .36l-.05.05-2.8-2.8.05-.05a1.8 1.8 0 0 0 .36-2A1.8 1.8 0 0 0 2.66 13H2.6V9h.06A1.8 1.8 0 0 0 4.3 7.9a1.8 1.8 0 0 0-.36-2l-.05-.05 2.8-2.8.05.05a1.8 1.8 0 0 0 2 .36A1.8 1.8 0 0 0 9.85 1.8V1.7h4v.1A1.8 1.8 0 0 0 15 3.45a1.8 1.8 0 0 0 2-.36l.05-.05 2.8 2.8-.05.05a1.8 1.8 0 0 0-.36 2A1.8 1.8 0 0 0 21.1 9h.1v4h-.1A1.8 1.8 0 0 0 19.4 15Z" />
    </svg>
  );
}

export default function App() {
  const hub = useHub();
  const mode = hub.connection?.info.deploymentMode === "school" ? "Школьный контур" : "Персональный контур";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none">
              <path d="m6.5 12.5 3.3 3.3 7.7-8" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div className="brand-copy">
            <div className="brand">Чистовик</div>
            <div className="brand-caption">Проверка без стопок</div>
          </div>
        </div>

        <nav className="nav-group" aria-label="Основная навигация">
          <div className="nav-section-label">Рабочее пространство</div>
          {links.map((link) => (
            <NavLink key={link.to} to={link.to} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
              <span className="nav-link-icon">
                <NavIcon name={link.icon} />
              </span>
              <span>{link.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="footer">
          <div className="local-status">
            <span className="local-status-dot" />
            Локальный модуль подключён
          </div>
          <div>
            Версия {hub.connection?.info.version ?? "—"} · {mode.toLowerCase()}
          </div>
          <div style={{ marginTop: 5 }}>Работы учеников остаются на этом компьютере.</div>
          <button className="btn small" onClick={hub.disconnect}>
            Отключить модуль
          </button>
        </div>
      </aside>

      <div className="app-main">
        <header className="app-topbar">
          <div className="topbar-context">{mode}</div>
          <div className="topbar-note">Камера · OCR · архив работают локально</div>
        </header>
        <main className="main">
          <div className="main-content">
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/sessions" element={<SessionsPage />} />
              <Route path="/sessions/:id/scan" element={<ScanPage />} />
              <Route path="/sessions/:id/review" element={<ReviewPage />} />
              <Route path="/sessions/:id/summary" element={<SummaryPage />} />
              <Route path="/catalog" element={<CatalogPage />} />
              <Route path="/students/:id" element={<StudentPage />} />
              <Route path="/forms" element={<FormsPage />} />
              <Route path="/calibration" element={<CalibrationPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  );
}
