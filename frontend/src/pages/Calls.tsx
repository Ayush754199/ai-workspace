import React, { useEffect, useState } from "react";
import AppShell from "../components/AppShell";
import { chatApi, ChatUser } from "../api/chatApi";
import { callsApi } from "../calls/callsApi";
import { useAuth } from "../contexts/AuthContext";
import { useCalls } from "../calls/CallProvider";
import { CallIcon } from "../components/CallIcon";

const Calls: React.FC = () => {
  const { token, user } = useAuth();
  const { startCall } = useCalls();
  const [query, setQuery] = useState("");
  const [users, setUsers] = useState<ChatUser[]>([]);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [history, setHistory] = useState<Awaited<ReturnType<typeof callsApi.list>>>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token || query.trim().length < 2) { setUsers([]); setBusy({}); return; }
    const timer = window.setTimeout(() => {
      chatApi.searchUsers(token, query.trim()).then((items) => {
        setUsers(items.filter((item) => item.id !== user?.id));
        return callsApi.availability(token, items.map((item) => item.id));
      }).then(setBusy).catch((reason) => setError(reason instanceof Error ? reason.message : "Unable to search users"));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query, token, user?.id]);

  useEffect(() => { if (token) callsApi.list(token).then(setHistory).catch(() => undefined); }, [token]);
  const call = async (person: ChatUser, type: "audio" | "video") => {
    if (!busy[String(person.id)]) { try { await startCall([person.id], type); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not start call"); } }
  };
  return <AppShell><div className="calls-page"><header className="workspace-topbar"><div><p className="eyebrow">Workspace</p><h1>Calls</h1><p className="page-subtitle">Call any authenticated user by voice or video.</p></div></header><section className="calls-card"><label htmlFor="call-user-search">Find a user</label><input id="call-user-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by name, email or mobile" />{users.length > 0 && <div className="call-user-list">{users.map((person) => { const isBusy = Boolean(busy[String(person.id)]); return <div className="call-user-row" key={person.id}><span className={`call-presence-dot ${isBusy ? "busy" : "available"}`} /><span className="call-user-copy"><strong>{person.name || person.email}</strong><small>{isBusy ? "Busy on another call" : "Available"}</small></span><button disabled={isBusy} onClick={() => void call(person, "audio")} title={isBusy ? "User is busy" : "Start voice call"}><CallIcon type="audio" /></button><button disabled={isBusy} onClick={() => void call(person, "video")} title={isBusy ? "User is busy" : "Start video call"}><CallIcon type="video" /></button></div>; })}</div>}{query.trim().length >= 2 && !users.length && <p className="muted-text">No matching users.</p>}</section><section className="calls-card"><h2>Call history</h2>{history.length === 0 ? <p className="muted-text">No calls yet.</p> : <div className="call-history-list">{history.map((item) => <div className="call-history-row" key={item.call_id}><span><CallIcon type={item.call_type} /></span><span>{item.status === "missed" ? "Missed call" : `${item.call_type} call`}</span><small>{new Date(item.created_at).toLocaleString()}</small></div>)}</div>}</section>{error && <p className="error-text">{error}</p>}</div></AppShell>;
};

export default Calls;
