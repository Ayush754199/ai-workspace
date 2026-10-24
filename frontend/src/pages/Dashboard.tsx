import React, { useEffect, useRef, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { chatApi, type ChatMessage, type ChatRoom } from "../api/chatApi";
import InviteMemberModal from "../components/InviteMemberModal";
import MessageBubble from "../components/MessageBubble";
import RoomList from "../components/RoomList";

const Dashboard: React.FC = () => {
  const { user, token, logout } = useAuth();
  const [rooms, setRooms] = useState<ChatRoom[]>([]);
  const [selectedRoom, setSelectedRoom] = useState<ChatRoom | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [showRooms, setShowRooms] = useState(true);
  const [showInvite, setShowInvite] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const refreshRooms = async () => {
    if (!token) return;
    try {
      const nextRooms = await chatApi.listRooms(token);
      setRooms(nextRooms);
      if (selectedRoom && !nextRooms.some((room) => room.id === selectedRoom.id)) {
        setSelectedRoom(null);
        setMessages([]);
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to load rooms");
    }
  };

  useEffect(() => { setIsLoading(true); refreshRooms().finally(() => setIsLoading(false)); }, [token]);
  useEffect(() => {
    if (!token || !selectedRoom) return;
    setError("");
    chatApi.listMessages(token, selectedRoom.id).then(setMessages).catch((requestError) => {
      if ((requestError as Error & { status?: number }).status === 403) { setError("Access denied"); refreshRooms(); }
      else setError(requestError instanceof Error ? requestError.message : "Unable to load messages");
    });
  }, [token, selectedRoom?.id]);
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const sendMessage = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!token || !selectedRoom || !prompt.trim() || isSending) return;
    setIsSending(true);
    try { setMessages((current) => [...current, await chatApi.sendMessage(token, selectedRoom.id, prompt.trim())]); setPrompt(""); }
    catch (requestError) {
      if ((requestError as Error & { status?: number }).status === 403) { setError("Access denied"); await refreshRooms(); }
      else setError(requestError instanceof Error ? requestError.message : "Unable to send message");
    } finally { setIsSending(false); }
  };

  const invite = async (identifier: string) => {
    if (!token || !selectedRoom) return "Unable to invite member";
    try { await chatApi.inviteMember(token, selectedRoom.id, identifier); await refreshRooms(); return null; }
    catch (requestError) {
      const status = (requestError as Error & { status?: number }).status;
      if (status === 404) return "User not found";
      if (status === 409) return "This user is already a member";
      if (status === 403) return "You are not allowed to invite members";
      return requestError instanceof Error ? requestError.message : "Unable to invite member";
    }
  };

  if (!user) return <h1>Please login first</h1>;
  return <main className="chat-page"><header className="chat-header"><div><p className="eyebrow">AI Workspace</p><h1>Shared Rooms</h1></div><button onClick={logout}>Logout</button></header><div className={`chat-shell ${showRooms ? "rooms-visible" : "chat-visible"}`}><RoomList rooms={rooms} selectedId={selectedRoom?.id || null} onSelect={(room) => { setSelectedRoom(room); setShowRooms(false); }} /><section className="room-chat">{selectedRoom ? <><header className="room-chat-header"><button className="mobile-only back-button" onClick={() => setShowRooms(true)}>Back</button><div><h2>{selectedRoom.name}</h2><small>{selectedRoom.member_details.length} members</small></div><button onClick={() => setShowInvite(true)}>Invite</button></header><div className="room-messages">{isLoading && <p className="muted-text">Loading rooms...</p>}{!isLoading && messages.length === 0 && <p className="muted-text">No messages in this room yet.</p>}{messages.map((message) => <MessageBubble key={message.id} message={message} own={message.sender_id === user.id} />)}<div ref={messagesEndRef} /></div><form className="room-message-input" onSubmit={sendMessage}><input value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Write a message..." /><button className="primary-button" disabled={isSending || !prompt.trim()}>{isSending ? "Sending..." : "Send"}</button></form></> : <div className="empty-room"><h2>Select a room</h2><p>Only rooms you belong to are shown.</p></div>}{error && <p className="error-text chat-error">{error}</p>}</section></div>{showInvite && <InviteMemberModal onClose={() => setShowInvite(false)} onSubmit={invite} />}</main>;
+};
+
+export default Dashboard;
