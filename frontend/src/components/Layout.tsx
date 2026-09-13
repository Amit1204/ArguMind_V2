import { NavLink, Outlet } from "react-router-dom";

const links = [
  { to: "/", label: "Overview", end: true },
  { to: "/ask", label: "Ask", end: false },
  { to: "/runs", label: "Runs", end: false },
];

export default function Layout() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <img src="/favicon.svg" alt="" width={28} height={28} />
          <div>
            <div className="brand-name">ArguMind</div>
            <div className="brand-sub">evidence reasoning</div>
          </div>
        </div>
        <nav>
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) => (isActive ? "nav active" : "nav")}
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">Phase 1 · foundation</div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
