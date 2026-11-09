import React, { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import AppShell from "../components/AppShell";
import MessageBubble from "../components/MessageBubble";
import InviteMemberModal from "../components/InviteMemberModal";
import { chatApi, type ChatMessage, type ChatRoom, type RoomInvitation, type ChatUser } from "../api/chatApi";
import { runImageStudio } from "../api/imageApi";
import { useAuth } from "../contexts/AuthContext";

const Dashboard: React.FC = () => {
  const { user, token } = useAuth();
  const [rooms, setRooms] = useState<ChatRoom[]>([]);
  const [invitations, setInvitations] = useState<RoomInvitation[]>([]);
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
  const [showRename, setShowRename] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const refreshRooms = async () => {
    if (!token) return;
    try {
      const next = await chatApi.listRooms(token);
      setRooms(next);
      if (selectedRoom) setSelectedRoom(next.find((room) => room.id === selectedRoom.id) || null);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to load conversations"); }
  };
  useEffect(() => { refreshRooms(); }, [token]);
  const refreshInvitations = async () => {
    if (!token) return;
    try { setInvitations(await chatApi.invitations(token)); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to load invitations"); }
  };
  useEffect(() => { refreshInvitations(); }, [token]);
  useEffect(() => {
    const inviteToken = searchParams.get("invite");
    if (!token || !inviteToken) return;
    chatApi.acceptInviteLink(token, inviteToken).then((room) => {
      setSelectedRoom(room);
      setShowRooms(false);
      void refreshRooms();
      setSearchParams({}, { replace: true });
    }).catch((requestError) => setError(requestError instanceof Error ? requestError.message : "Unable to accept invite link"));
  }, [token, searchParams, setSearchParams]);
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
  const renameRoom = async (event: React.FormEvent) => {
    event.preventDefault();
    const name = renameValue.trim();
    if (!name || name === selectedRoom?.name) { setShowRename(false); return; }
    await mutateRoom({ name });
    setShowRename(false);
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
  const createInviteLink = async (): Promise<string | null> => {
    if (!token || !selectedRoom) return null;
    try { return (await chatApi.createInviteLink(token, selectedRoom.id)).token; }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to create invite link"); return null; }
  };
  const searchUsers = async (query: string): Promise<ChatUser[]> => {
    if (!token) return [];
    return chatApi.searchUsers(token, query);
  };
  const acceptInvitation = async (invitation: RoomInvitation) => {
    if (!token) return;
    try {
      const room = await chatApi.acceptInvitation(token, invitation.id);
      setInvitations((current) => current.filter((item) => item.id !== invitation.id));
      await refreshRooms();
      setSelectedRoom(room);
      setShowRooms(false);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to accept invitation"); }
  };
  const declineInvitation = async (invitation: RoomInvitation) => {
    if (!token) return;
    try {
      await chatApi.declineInvitation(token, invitation.id);
      setInvitations((current) => current.filter((item) => item.id !== invitation.id));
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to decline invitation"); }
  };
  const removeMember = async (memberId: number) => {
    if (!token || !selectedRoom || !window.confirm("Remove this member from the room?")) return;
    try {
      await chatApi.removeMember(token, selectedRoom.id, memberId);
      await refreshRooms();
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to remove member"); }
  };
  const leaveRoom = async () => {
    if (!token || !selectedRoom || !window.confirm("Leave this room?")) return;
    try {
      await chatApi.leaveRoom(token, selectedRoom.id);
      setSelectedRoom(null);
      setMessages([]);
      await refreshRooms();
      setShowRooms(true);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to leave room"); }
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
    {invitations.length > 0 && <section className="room-invitations" aria-label="Pending room invitations"><strong>Pending room invitations</strong>{invitations.map((invitation) => <div className="room-invitation" key={invitation.id}><span><b>{invitation.room_name}</b>{invitation.inviter_name ? ` · invited by ${invitation.inviter_name}` : ""}</span><span className="room-invitation-actions"><button onClick={() => void acceptInvitation(invitation)}>Accept</button><button onClick={() => void declineInvitation(invitation)}>Cancel</button></span></div>)}</section>}
    <header className="workspace-topbar"><div><p className="eyebrow">AI Workspace</p><h1>{imageWorkspaceRequested ? "Image workspace" : "Chat"}</h1><p className="page-subtitle">Conversations, files and image workflows in one place.</p></div><form className="new-conversation" onSubmit={createRoom}><input value={newRoomName} onChange={(event) => setNewRoomName(event.target.value)} placeholder="New conversation name" aria-label="New conversation name" /><button className="secondary-action">＋ New</button></form></header>
    <div className={`chat-workspace-grid ${showRooms ? "rooms-visible" : "chat-visible"}`}>
      <aside className="conversation-sidebar"><div className="conversation-heading"><div><span className="eyebrow">Conversations</span><strong>{visibleRooms.length}</strong></div><button onClick={() => setShowArchived((value) => !value)}>{showArchived ? "Hide archived" : "Archived"}</button></div>{visibleRooms.length === 0 && <p className="muted-text">No conversations yet.</p>}{visibleRooms.map((room) => <button key={room.id} className={`conversation-item ${selectedRoom?.id === room.id ? "selected" : ""}`} onClick={() => { setSelectedRoom(room); setShowRooms(false); }}><span className="conversation-icon">{room.is_pinned ? "★" : "○"}</span><span><strong>{room.name}</strong><small>{room.is_archived ? "Archived" : room.member_details.length + " members"}</small></span></button>)}</aside>
        <section className="conversation-main">{selectedRoom ? <><header className="conversation-header"><button className="mobile-only back-button" onClick={() => setShowRooms(true)}>Back</button><div><h2>{selectedRoom.name}</h2><small>{selectedRoom.member_details.length} members · {selectedRoom.is_archived ? "archived" : "active"}</small></div><div className="conversation-actions"><button onClick={() => setShowInvite(true)}>Invite</button><button onClick={() => { setRenameValue(selectedRoom.name); setShowRename(true); }}>Rename</button><button onClick={() => mutateRoom({ is_pinned: !selectedRoom.is_pinned })}>{selectedRoom.is_pinned ? "Unpin" : "Pin"}</button><button onClick={() => mutateRoom({ is_archived: !selectedRoom.is_archived })}>{selectedRoom.is_archived ? "Restore" : "Archive"}</button>{user.id !== selectedRoom.created_by && <button onClick={() => void leaveRoom()}>Leave</button>}<button className="danger-button" onClick={deleteRoom}>Delete</button></div></header><div className="room-members"><strong>Members</strong>{selectedRoom.member_details.map((member) => <span className="room-member" key={member.id}>{member.name || member.email}{member.id === selectedRoom.created_by ? " (owner)" : member.id === user.id ? " (you)" : user.id === selectedRoom.created_by ? <button onClick={() => void removeMember(member.id)}>Remove</button> : null}</span>)}</div><div className="room-messages">{messages.length === 0 && <div className="chat-empty"><span>✦</span><h3>Start the conversation</h3><p>Ask a question, attach a file, or switch to Image Studio below.</p></div>}{messages.map((message) => <MessageBubble key={message.id} message={message} own={message.sender_id === user.id} />)}<div ref={messagesEndRef} /></div></> : <div className="chat-empty"><span>⌁</span><h2>Select a conversation</h2><p>Choose one from the sidebar or create a new workspace.</p></div>}
        <div className="composer-wrap"><div className="composer-tabs"><button className={composerMode === "chat" ? "active" : ""} onClick={() => setComposerMode("chat")}>Chat</button><button className={composerMode === "image" ? "active" : ""} onClick={() => setComposerMode("image")}>✦ Image generation & editing</button></div><form className="chat-composer" onSubmit={send}>{composerMode === "image" && <label className="image-attach">{imageFile ? imageFile.name : "Attach image to edit (optional)"}<input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setImageFile(event.target.files?.[0])} /></label>}<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={composerMode === "image" ? imageFile ? "Describe the edit you want…" : "Describe the image you want to generate…" : "Message your workspace…"} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(event); } }} /><button className="primary-action" disabled={isSending || !prompt.trim()}>{isSending ? "Working…" : composerMode === "image" ? "Create image" : "Send"}</button></form>{imageResult && <div className="image-result"><img src={imageResult} alt="Generated result" /><a href={imageResult} download="ai-workspace-image.png">Download image</a></div>}<p className="composer-note">Provider availability, quotas and editing support are validated by the backend.</p></div>
      </section>
  </div>{error && <p className="error-text workspace-error">{error}</p>}{showInvite && <InviteMemberModal onClose={() => setShowInvite(false)} onSubmit={inviteMember} onCreateLink={createInviteLink} onSearchUsers={searchUsers} />}{showRename && selectedRoom && <div className="modal-backdrop" role="presentation"><form className="invite-modal" onSubmit={renameRoom}><h2>Rename conversation</h2><input autoFocus value={renameValue} maxLength={120} onChange={(event) => setRenameValue(event.target.value)} aria-label="Conversation name" /><div className="modal-actions"><button type="button" onClick={() => setShowRename(false)}>Cancel</button><button className="primary-button" disabled={!renameValue.trim()}>Save</button></div></form></div>}
  </div></AppShell>;
};

export default Dashboard;
