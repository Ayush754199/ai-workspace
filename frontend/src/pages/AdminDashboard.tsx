import React, { useEffect, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import ChatAuditTab from "../components/ChatAuditTab";

type AdminUser = {
  id: number;
  email: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  is_admin: boolean;
};

type Analytics = {
  total_users: number;
  active_users: number;
  active_chats: number;
  documents_analyzed: number;
  excel_exports_generated: number;
};

const AdminDashboard: React.FC = () => {
  const { token, logout } = useAuth();
  const [tab, setTab] = useState<"analytics" | "users" | "audit">("analytics");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [error, setError] = useState("");
  const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

  const request = async (path: string, options?: RequestInit) => {
    const response = await fetch(`${apiBaseUrl}/api/v1/admin${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(options?.headers || {}) },
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Admin request failed");
    return response.json();
  };

  const loadData = async () => {
    try {
      setError("");
      const [userData, analyticsData] = await Promise.all([request("/users"), request("/analytics")]);
      setUsers(userData);
      setAnalytics(analyticsData);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to load admin data");
    }
  };

  useEffect(() => { if (token) loadData(); }, [token]);

  const updateUser = async (user: AdminUser, field: "is_active" | "role", value: boolean | string) => {
    try {
      await request(`/users/${user.id}/status`, { method: "PATCH", body: JSON.stringify({ [field]: value }) });
      await loadData();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to update user");
    }
  };

  return <main className="admin-page"><header className="admin-header"><div><p className="eyebrow">AI Workspace / Control Room</p><h1>Admin Dashboard</h1></div><button onClick={logout}>Logout</button></header><nav className="admin-tabs" aria-label="Admin sections"><button className={tab === "analytics" ? "active" : ""} onClick={() => setTab("analytics")}>Analytics Overview</button><button className={tab === "users" ? "active" : ""} onClick={() => setTab("users")}>User Management</button><button className={tab === "audit" ? "active" : ""} onClick={() => setTab("audit")}>Chat Surveillance / Audit Logs</button></nav>{error && <p className="admin-error">{error}</p>}{tab === "analytics" && analytics && <section className="admin-cards">{[["Total users", analytics.total_users], ["Active users", analytics.active_users], ["Active chats", analytics.active_chats], ["Documents analyzed", analytics.documents_analyzed], ["Excel exports", analytics.excel_exports_generated]].map(([label, value]) => <article key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>}{tab === "users" && <section className="admin-table-wrap"><table><thead><tr><th>User</th><th>Role</th><th>Admin</th><th>Status</th></tr></thead><tbody>{users.map((user) => <tr key={user.id}><td><strong>{user.full_name || "Unnamed user"}</strong><small>{user.email}</small></td><td><select value={user.role} onChange={(event) => updateUser(user, "role", event.target.value)}><option value="user">User</option><option value="doctor">Doctor</option><option value="ca">CA</option><option value="engineer">Engineer</option></select></td><td>{user.is_admin ? "Yes" : "No"}</td><td><button className={user.is_active ? "status-on" : "status-off"} onClick={() => updateUser(user, "is_active", !user.is_active)}>{user.is_active ? "Enabled" : "Disabled"}</button></td></tr>)}</tbody></table></section>}{tab === "audit" && token && <ChatAuditTab token={token} />}</main>;
};

export default AdminDashboard;