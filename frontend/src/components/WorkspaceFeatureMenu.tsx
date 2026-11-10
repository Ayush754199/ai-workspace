import React, { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { chatV2, Conversation } from "../api/workspaceApi";
import { useAuth } from "../contexts/AuthContext";

const WorkspaceFeatureMenu: React.FC = () => {
  const { token, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<Conversation[]>([]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    chatV2.conversations(token)
      .then((items) => { if (!cancelled) setHistory(items.slice(0, 12)); })
      .catch((error: any) => {
        if (error?.status === 401) logout();
      });
    return () => { cancelled = true; };
  }, [token, logout, location.pathname]);

  const close = () => setOpen(false);
  const openChat = (conversationId?: number) => {
    close();
    navigate(conversationId ? `/assistant?conversation=${conversationId}` : "/assistant");
  };

  return (
    <div className="workspace-feature-menu-root">
      <button
        type="button"
        className="workspace-feature-trigger"
        aria-label="Open chat features and history"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span aria-hidden="true">☰</span><span>Features</span>
      </button>
      {open && (
        <>
          <button className="workspace-feature-backdrop" type="button" aria-label="Close features" onClick={close} />
          <aside className="workspace-feature-panel" aria-label="Features and chat history">
            <div className="workspace-feature-panel-head">
              <strong>Workspace</strong>
              <button type="button" aria-label="Close features" onClick={close}>×</button>
            </div>
            <nav className="workspace-feature-links" aria-label="Workspace features">
              <button className={location.pathname === "/assistant" ? "active" : ""} type="button" onClick={() => openChat()}><span>💬</span><span><strong>Chat</strong><small>Ask anything</small></span></button>
              <Link to="/images" onClick={close}><span>✦</span><span><strong>Images</strong><small>Generate or edit</small></span></Link>
              <Link to="/documents" onClick={close}><span>▤</span><span><strong>Documents</strong><small>Read and generate</small></span></Link>
              <Link to="/office" onClick={close}><span>▦</span><span><strong>Office</strong><small>Workspace tools</small></span></Link>
              <Link to="/research" onClick={close}><span>◎</span><span><strong>Research</strong><small>Live web sources</small></span></Link>
            </nav>
            <div className="workspace-history-head"><strong>Chat history</strong><button type="button" onClick={() => openChat()}>＋ New chat</button></div>
            <div className="workspace-history-list">
              {history.length === 0 && <p>No conversations yet</p>}
              {history.map((conversation) => (
                <button key={conversation.id} type="button" onClick={() => openChat(conversation.id)}>
                  <span>◌</span><span>{conversation.title}</span>
                </button>
              ))}
            </div>
          </aside>
        </>
      )}
    </div>
  );
};

export default WorkspaceFeatureMenu;
