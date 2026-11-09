import React from "react";
import { useAuth } from "../contexts/AuthContext";
import AppShell from "../components/AppShell";

const Profile: React.FC = () => {
  const { user } = useAuth();
  return <AppShell><div className="page-frame profile-page"><div className="page-heading"><div><p className="eyebrow">Account</p><h1>Profile & preferences</h1><p className="page-subtitle">Your account controls live here in one place.</p></div></div><section className="profile-card"><div className="profile-hero"><div className="profile-large-avatar">{(user?.full_name || user?.email || "U").slice(0, 1).toUpperCase()}</div><div><h2>{user?.full_name || "Workspace member"}</h2><p>{user?.email}</p></div></div><dl className="profile-details"><div><dt>Role</dt><dd>{user?.role || "user"}</dd></div><div><dt>Status</dt><dd>{user?.is_active ? "Active" : "Disabled"}</dd></div><div><dt>Permissions</dt><dd>{user?.is_admin ? "Administrator" : "Standard workspace access"}</dd></div></dl></section></div></AppShell>;
};

export default Profile;
