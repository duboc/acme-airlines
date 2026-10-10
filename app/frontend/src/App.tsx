import { NavLink, Navigate, Outlet, Route, Routes } from 'react-router-dom';
import { ConsolePage } from './routes/ConsolePage';
import { NotFoundPage } from './routes/NotFoundPage';
import { WhatsAppPage } from './routes/WhatsAppPage';

function Shell() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="app-header">
        <span className="brand">ACME Airlines</span>
        <nav aria-label="Main">
          <NavLink to="/console">Console</NavLink>
          <NavLink to="/whatsapp">WhatsApp</NavLink>
        </nav>
        <span className="sim-note">Simulated data</span>
      </header>
      <main id="main" tabIndex={-1}>
        <Outlet />
      </main>
    </>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/console" replace />} />
        <Route path="console" element={<ConsolePage />} />
        <Route path="whatsapp" element={<WhatsAppPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
