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

const links = [
  { to: "/dashboard", label: "Главная" },
  { to: "/sessions", label: "Сессии" },
  { to: "/catalog", label: "Классы и задания" },
  { to: "/forms", label: "Бланки" },
  { to: "/calibration", label: "Калибровка" },
  { to: "/settings", label: "Настройки" },
];

export default function App() {
  const hub = useHub();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          Paper<span>Flow</span> Web
        </div>
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            {l.label}
          </NavLink>
        ))}
        <div className="footer">
          <div style={{ marginBottom: 8 }}>
            Hub {hub.connection?.info.version} · {hub.connection?.info.deploymentMode === "school" ? "школьный" : "персональный"}
          </div>
          <div>Данные учеников обрабатываются и хранятся локально.</div>
          <button className="btn small mt" onClick={hub.disconnect}>
            Отключить Hub
          </button>
        </div>
      </aside>
      <main className="main">
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
      </main>
    </div>
  );
}
