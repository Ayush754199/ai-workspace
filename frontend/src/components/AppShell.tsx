import React from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

const AppShell: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, logout } = useAuth();
  const navClass = ({ isActive }: { isActive: boolean }) => `shell-nav-item${isActive ? " active" : ""}`;

  return (
    <div className="workspace-shell">
      <aside className="workspace-sidebar" aria-label="Primary navigation">
        <div className="workspace-brand"><span className="brand-mark">✦</span><span>AI Workspace</span></div>
        <nav className="shell-nav">
          <p className="shell-nav-label">Workspace</p>
          <NavLink className={navClass} to="/assistant"><span>✦</span><span className="shell-nav-text">Assistant</span></NavLink>
          <NavLink className={navClass} to="/dashboard"><span>⌁</span><span className="shell-nav-text">Rooms</span></NavLink>
          <p className="shell-nav-label">Video</p>
          <NavLink className={navClass} to="/video-studio" end><span>✦</span><span className="shell-nav-text">Video Generation</span></NavLink>
          <NavLink className={navClass} to="/video-studio/editor"><span>▣</span><span className="shell-nav-text">Video Editing</span></NavLink>
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
      <main className="workspace-main">{children}</main>
    </div>
  );
};

export default AppShell;
