import React from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import WorkspaceFeatureMenu from "./WorkspaceFeatureMenu";

const AppShell: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, logout } = useAuth();
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  const navClass = ({ isActive }: { isActive: boolean }) => `shell-nav-item${isActive ? " active" : ""}`;

  return (
    <div className={`workspace-shell${mobileNavOpen ? " mobile-nav-open" : ""}`}>
      <aside className="workspace-sidebar" aria-label="Primary navigation">
        <div className="workspace-brand"><span className="brand-mark">✦</span><span>AI Workspace</span></div>
        <nav className="shell-nav" onClick={() => setMobileNavOpen(false)}>
          <p className="shell-nav-label">Workspace</p>
          <NavLink className={navClass} to="/assistant"><span>✦</span><span className="shell-nav-text">Assistant</span></NavLink>
          <NavLink className={navClass} to="/rooms"><span>⌁</span><span className="shell-nav-text">Rooms</span></NavLink>
          <NavLink className={navClass} to="/images"><span>◈</span><span className="shell-nav-text">Images</span></NavLink>
          <NavLink className={navClass} to="/documents"><span>▤</span><span className="shell-nav-text">Documents</span></NavLink>
          <NavLink className={navClass} to="/office"><span>▦</span><span className="shell-nav-text">Office</span></NavLink>
          <NavLink className={navClass} to="/research"><span>◎</span><span className="shell-nav-text">Research</span></NavLink>
          <p className="shell-nav-label shell-nav-label-spaced">Manage</p>
          {user?.is_admin && <NavLink className={navClass} to="/admin"><span>⌘</span><span className="shell-nav-text">Admin</span></NavLink>}
        </nav>
        <div className="shell-account">
          <NavLink className={navClass} to="/profile">
            <span className="account-avatar">{(user?.full_name || user?.email || "U").slice(0, 1).toUpperCase()}</span>
            <span className="account-copy"><strong>{user?.full_name || "Account"}</strong><small>{user?.email}</small></span>
          </NavLink>
          <button className="shell-logout" onClick={logout}>Sign out</button>
        </div>
      </aside>
    <main className="workspace-main">
      <WorkspaceFeatureMenu />
      <button className="mobile-nav-toggle" type="button" aria-label={mobileNavOpen ? "Close navigation" : "Open navigation"} aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen((open) => !open)}>
        {mobileNavOpen ? "×" : "☰"}
      </button>
      {mobileNavOpen && <button className="mobile-nav-backdrop" type="button" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)} />}
      <div className="mobile-account-name" title={user?.full_name || user?.email || "Account"}>
        <span className="account-avatar">{(user?.full_name || user?.email || "U").slice(0, 1).toUpperCase()}</span>
        <span>{user?.full_name || user?.email || "Account"}</span>
      </div>
      {children}
    </main>
    </div>
  );
};

export default AppShell;
