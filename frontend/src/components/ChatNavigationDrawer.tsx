import React, { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Conversation } from "../api/workspaceApi";

type Props = {
  open: boolean;
  onClose: () => void;
  conversations: Conversation[];
  activeId: number | null;
  search: string;
  onSearchChange: (value: string) => void;
  onNewChat: () => void;
  onOpenConversation: (id: number) => void;
  onArchiveConversation: (conversation: Conversation) => void;
  onProjectConversation: (conversation: Conversation) => void;
  onDeleteConversation: (id: number) => void;
  showArchived: boolean;
  onToggleArchived: () => void;
  user?: { full_name?: string | null; email?: string | null } | null;
};

const ChatNavigationDrawer: React.FC<Props> = ({
  open, onClose, conversations, activeId, search, onSearchChange, onNewChat,
  onOpenConversation, onArchiveConversation, onProjectConversation, onDeleteConversation,
  showArchived, onToggleArchived, user,
}) => {
  const location = useLocation();
  const navigate = useNavigate();
  const [menuId, setMenuId] = useState<number | null>(null);
  const drawerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const closeAnd = (action: () => void) => { setMenuId(null); action(); };
  const openChat = (id?: number) => {
    if (id == null) navigate("/assistant");
    else onOpenConversation(id);
    onClose();
  };

  return (
    <>
      <button className={`cw-drawer-backdrop ${open ? "open" : ""}`} type="button" aria-label="Close chat features" onClick={onClose} />
      <aside ref={drawerRef} className={`cw-navigation-drawer ${open ? "open" : ""}`} aria-label="Chat history and features" aria-hidden={!open}>
        {open && <div className="cw-drawer-content">
        <div className="cw-drawer-head">
          <div className="cw-brand"><span>✦</span><strong>AI Workspace</strong></div>
          <button type="button" className="cw-drawer-close" aria-label="Close chat features" onClick={onClose}>×</button>
        </div>
        <button type="button" className="cw-drawer-new" onClick={() => closeAnd(onNewChat)}>✎ <strong>New chat</strong></button>
        <nav className="cw-drawer-links" aria-label="Workspace features">
          <button type="button" className={location.pathname === "/assistant" ? "active" : ""} onClick={() => openChat()}><span>▣</span><strong>Chat</strong></button>
          <Link to="/rooms" className={location.pathname === "/rooms" ? "active" : ""} onClick={onClose}><span>⌁</span><strong>Rooms</strong></Link>
          <Link to="/calls" className={location.pathname === "/calls" ? "active" : ""} onClick={onClose}><span>☎</span><strong>Calls</strong></Link>
          <Link to="/images" onClick={onClose}><span>✦</span><strong>Images</strong></Link>
          <Link to="/documents" onClick={onClose}><span>▤</span><strong>Documents</strong></Link>
          <Link to="/office" onClick={onClose}><span>▦</span><strong>Office</strong></Link>
          <Link to="/research" onClick={onClose}><span>⌕</span><strong>Research</strong></Link>
        </nav>
        <div className="cw-drawer-history-head">
          <strong>Chat history</strong>
          <button type="button" onClick={onToggleArchived}>{showArchived ? "Active" : "All"}</button>
        </div>
        <label className="cw-drawer-search"><span aria-hidden="true">⌕</span><input value={search} onChange={(event) => onSearchChange(event.target.value)} placeholder="Search chats" /></label>
        <div className="cw-drawer-history">
          {conversations.length === 0 && <p>{search ? "No matches" : "No conversations yet"}</p>}
          {conversations.map((conversation) => (
            <div className={`cw-drawer-row ${conversation.id === activeId ? "active" : ""}`} key={conversation.id}>
              <button type="button" className="cw-drawer-row-main" onClick={() => openChat(conversation.id)}>
                <span className="cw-drawer-row-icon">{conversation.is_pinned ? "★" : "◌"}</span>
                <span>{conversation.title}</span>
              </button>
              <button type="button" className="cw-drawer-row-menu" aria-label={`More actions for ${conversation.title}`} aria-expanded={menuId === conversation.id} onClick={() => setMenuId(menuId === conversation.id ? null : conversation.id)}>⋮</button>
              {menuId === conversation.id && (
                <div className="cw-drawer-context-menu" role="menu">
                  <button type="button" role="menuitem" onClick={() => closeAnd(() => onArchiveConversation(conversation))}>{conversation.is_archived ? "Restore" : "Archive"}</button>
                  <button type="button" role="menuitem" onClick={() => closeAnd(() => onProjectConversation(conversation))}>{conversation.is_pinned ? "Remove from project" : "Project"}</button>
                  <button type="button" role="menuitem" className="danger" onClick={() => closeAnd(() => onDeleteConversation(conversation.id))}>Delete</button>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="cw-drawer-foot">
          <Link className="cw-profile" to="/profile" onClick={onClose}>
            <span className="cw-profile-avatar">{(user?.full_name || user?.email || "U").slice(0, 1).toUpperCase()}</span>
            <span className="cw-profile-copy"><strong>{user?.full_name || "Account"}</strong><small>{user?.email}</small></span>
            <span className="cw-profile-chevron">›</span>
          </Link>
        </div>
        </div>}
      </aside>
    </>
  );
};

export default ChatNavigationDrawer;
