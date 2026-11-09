import React, { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import AppShell from "../components/AppShell";
import MessageBubble from "../components/MessageBubble";
import InviteMemberModal from "../components/InviteMemberModal";
import { chatApi, type ChatMessage, type ChatRoom } from "../api/chatApi";
import { runImageStudio } from "../api/imageApi";
import { useAuth } from "../contexts/AuthContext";

const Dashboard: React.FC = () => {
  const { user, token } = useAuth();
  const [rooms, setRooms] = useState<ChatRoom[]>([]);
  const [selectedRoom, setSelectedRoom] = useState<ChatRoom | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [newRoomName, setNewRoomName] = useState("");
  const [imageFile, setImageFile] = useState<File>();
  const [imageResult, setImageResult] = useState<string>();
  const [composerMode, setComposerMode] = useState<"chat" | "image">("chat");
  const [showArchived, setShowArchived] = useState(false);
  const [showRooms, setShowRooms] = useState(true);
  const [showInvite, setShowInvite] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [searchParams] = useSearchParams();

  const refreshRooms = async () => {
    if (!token) return;
    try {
      const next = await chatApi.listRooms(token);
      setRooms(next);
      if (selectedRoom) setSelectedRoom(next.find((room) => room.id === selectedRoom.id) || null);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to load conversations"); }
  };
  useEffect(() => { refreshRooms(); }, [token]);
  useEffect(() => {
    if (!token || !selectedRoom) return;
    chatApi.listMessages(token, selectedRoom.id).then(setMessages).catch((requestError) => setError(requestError instanceof Error ? requestError.message : "Unable to load messages"));
  }, [token, selectedRoom?.id]);
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => () => { if (imageResult) URL.revokeObjectURL(imageResult); }, [imageResult]);

  const visibleRooms = useMemo(() => rooms.filter((room) => showArchived || !room.is_archived), [rooms, showArchived]);
  const imageWorkspaceRequested = searchParams.get("view") === "images";

  const createRoom = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!token || !newRoomName.trim()) return;
    try { const room = await chatApi.createRoom(token, newRoomName.trim()); setNewRoomName(""); await refreshRooms(); setSelectedRoom(room); setShowRooms(false); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to create conversation"); }
  };
  const mutateRoom = async (update: { name?: string; is_pinned?: boolean; is_archived?: boolean }) => {
    if (!token || !selectedRoom) return;
    try { const room = await chatApi.updateRoom(token, selectedRoom.id, update); setSelectedRoom(room); await refreshRooms(); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to update conversation"); }
  };
  const deleteRoom = async () => {
    if (!token || !selectedRoom || !window.confirm("Delete this conversation? This cannot be undone.")) return;
    try { await chatApi.deleteRoom(token, selectedRoom.id); setSelectedRoom(null); setMessages([]); await refreshRooms(); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to delete conversation"); }
  };
  const inviteMember = async (identifier: string): Promise<string | null> => {
    if (!token || !selectedRoom) return "Select a conversation first";
    try {
      await chatApi.inviteMember(token, selectedRoom.id, identifier);
      await refreshRooms();
      return null;
    } catch (requestError) {
      return requestError instanceof Error ? requestError.message : "Unable to invite member";
    }
  };
  const send = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!token || !prompt.trim() || isSending) return;
    setIsSending(true); setError("");
    try {
      if (composerMode === "image") {
        const blob = await runImageStudio(token, prompt.trim(), imageFile);
        if (imageResult) URL.revokeObjectURL(imageResult);
        setImageResult(URL.createObjectURL(blob));
      } else if (selectedRoom) {
        const message = await chatApi.sendMessage(token, selectedRoom.id, prompt.trim());
        setMessages((current) => [...current, message]);
      } else setError("Create or select a conversation first");
      setPrompt("");
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "The request failed"); }
    finally { setIsSending(false); }
  };

  if (!user) return null;
  return <AppShell><div className="chat-workspace">
    <header className="workspace-topbar"><div><p className="eyebrow">AI Workspace</p><h1>{imageWorkspaceRequested ? "Image workspace" : "Chat"}</h1><p className="page-subtitle">Conversations, files and image workflows in one place.</p></div><form className="new-conversation" onSubmit={createRoom}><input value={newRoomName} onChange={(event) => setNewRoomName(event.target.value)} placeholder="New conversation name" aria-label="New conversation name" /><button className="secondary-action">＋ New</button></form></header>
    <div className={`chat-workspace-grid ${showRooms ? "rooms-visible" : "chat-visible"}`}>
      <aside className="conversation-sidebar"><div className="conversation-heading"><div><span className="eyebrow">Conversations</span><strong>{visibleRooms.length}</strong></div><button onClick={() => setShowArchived((value) => !value)}>{showArchived ? "Hide archived" : "Archived"}</button></div>{visibleRooms.length === 0 && <p className="muted-text">No conversations yet.</p>}{visibleRooms.map((room) => <button key={room.id} className={`conversation-item ${selectedRoom?.id === room.id ? "selected" : ""}`} onClick={() => { setSelectedRoom(room); setShowRooms(false); }}><span className="conversation-icon">{room.is_pinned ? "★" : "○"}</span><span><strong>{room.name}</strong><small>{room.is_archived ? "Archived" : room.member_details.length + " members"}</small></span></button>)}</aside>
        <section className="conversation-main">{selectedRoom ? <><header className="conversation-header"><button className="mobile-only back-button" onClick={() => setShowRooms(true)}>Back</button><div><h2>{selectedRoom.name}</h2><small>{selectedRoom.member_details.length} members · {selectedRoom.is_archived ? "archived" : "active"}</small></div><div className="conversation-actions"><button onClick={() => setShowInvite(true)}>Invite</button><button onClick={() => { const name = window.prompt("Rename conversation", selectedRoom.name); if (name?.trim()) mutateRoom({ name: name.trim() }); }}>Rename</button><button onClick={() => mutateRoom({ is_pinned: !selectedRoom.is_pinned })}>{selectedRoom.is_pinned ? "Unpin" : "Pin"}</button><button onClick={() => mutateRoom({ is_archived: !selectedRoom.is_archived })}>{selectedRoom.is_archived ? "Restore" : "Archive"}</button><button className="danger-button" onClick={deleteRoom}>Delete</button></div></header><div className="room-messages">{messages.length === 0 && <div className="chat-empty"><span>✦</span><h3>Start the conversation</h3><p>Ask a question, attach a file, or switch to Image Studio below.</p></div>}{messages.map((message) => <MessageBubble key={message.id} message={message} own={message.sender_id === user.id} />)}<div ref={messagesEndRef} /></div></> : <div className="chat-empty"><span>⌁</span><h2>Select a conversation</h2><p>Choose one from the sidebar or create a new workspace.</p></div>}
        <div className="composer-wrap"><div className="composer-tabs"><button className={composerMode === "chat" ? "active" : ""} onClick={() => setComposerMode("chat")}>Chat</button><button className={composerMode === "image" ? "active" : ""} onClick={() => setComposerMode("image")}>✦ Image generation & editing</button></div><form className="chat-composer" onSubmit={send}>{composerMode === "image" && <label className="image-attach">{imageFile ? imageFile.name : "Attach image to edit (optional)"}<input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setImageFile(event.target.files?.[0])} /></label>}<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={composerMode === "image" ? imageFile ? "Describe the edit you want…" : "Describe the image you want to generate…" : "Message your workspace…"} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(event); } }} /><button className="primary-action" disabled={isSending || !prompt.trim()}>{isSending ? "Working…" : composerMode === "image" ? "Create image" : "Send"}</button></form>{imageResult && <div className="image-result"><img src={imageResult} alt="Generated result" /><a href={imageResult} download="ai-workspace-image.png">Download image</a></div>}<p className="composer-note">Provider availability, quotas and editing support are validated by the backend.</p></div>
      </section>
    </div>{error && <p className="error-text workspace-error">{error}</p>}{showInvite && <InviteMemberModal onClose={() => setShowInvite(false)} onSubmit={inviteMember} />}
  </div></AppShell>;
};

export default Dashboard;
