import React, { useEffect, useState } from "react";
import { adminApi, type AdminChatRoom, type AdminTranscript } from "../api/adminApi";

const ChatAuditTab: React.FC<{ token: string }> = ({ token }) => {
  const [filters, setFilters] = useState({ username: "", role: "", roomName: "" });
  const [rooms, setRooms] = useState<AdminChatRoom[]>([]);
  const [transcript, setTranscript] = useState<AdminTranscript | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLoading(true);
      adminApi.listChats(token, filters).then((data) => { setRooms(data.items); setError(""); }).catch((requestError) => setError(requestError instanceof Error ? requestError.message : "Unable to load chats")).finally(() => setLoading(false));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [token, filters.username, filters.role, filters.roomName]);

  const openTranscript = async (roomId: number) => {
    try { setTranscript(await adminApi.getTranscript(token, roomId)); setError(""); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to load transcript"); }
  };

  return <section className="audit-panel"><div className="audit-note">Admin access to chats is logged.</div><div className="audit-filters"><input placeholder="Username or email" value={filters.username} onChange={(event) => setFilters({ ...filters, username: event.target.value })} /><select value={filters.role} onChange={(event) => setFilters({ ...filters, role: event.target.value })}><option value="">All roles</option><option value="doctor">Doctor</option><option value="ca">CA</option><option value="engineer">Engineer</option><option value="user">User</option></select><input placeholder="Room name" value={filters.roomName} onChange={(event) => setFilters({ ...filters, roomName: event.target.value })} /><button onClick={() => setFilters({ username: "", role: "", roomName: "" })}>Clear</button></div>{error && <p className="admin-error">{error}</p>}<div className="audit-layout"><div className={`audit-room-list ${transcript ? "mobile-hidden" : ""}`}>{loading && <p className="muted-text">Loading chats...</p>}{!loading && rooms.length === 0 && <p className="muted-text">No matching rooms.</p>}{rooms.map((room) => <button className="audit-room-card" key={room.id} onClick={() => openTranscript(room.id)}><strong>{room.name}</strong><span>{room.room_type} · {room.members.length} members</span><small>{room.members.map((member) => member.name || member.email).join(", ")}</small><time>{room.last_message_at ? new Date(room.last_message_at).toLocaleString() : "No messages"}</time></button>)}</div><div className={`audit-transcript ${!transcript ? "mobile-hidden" : ""}`}>{transcript ? <><button className="mobile-only back-button" onClick={() => setTranscript(null)}>Back to rooms</button><h2>{transcript.name}</h2><p className="muted-text">Read-only transcript · {transcript.member_count} members</p>{transcript.messages.map((message) => <article className="audit-message" key={message.id}><small>{message.sender_name} ({message.sender_role}) · {message.created_at ? new Date(message.created_at).toLocaleString() : ""}</small><p>{message.content}</p></article>)}</> : <p className="muted-text">Select a room to inspect its transcript.</p>}</div></div></section>;
+};
+
+export default ChatAuditTab;
