import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import { Attachment, ChatMessage, Conversation, chatV2 } from "../api/workspaceApi";
import { imageApi } from "../api/workspaceApi";
import renderMarkdown from "../components/Markdown";
import VoiceControls from "../components/VoiceControls";
import ChatNavigationDrawer from "../components/ChatNavigationDrawer";

type ChatArtifact = { kind: "pdf" | "excel" | "code"; title: string; filename: string; content: string };

function parseArtifact(text: string): { chat: string; artifact: ChatArtifact | null; pending: boolean } {
  const start = /```file_(pdf|excel|code):([^:\n]+):([^\s`]+)[^\S\r\n]*\r?\n?/i.exec(text);
  const headerPrefix = /```file_(?:pdf|excel|code):/i;
  if (!start) {
    const tagStart = headerPrefix.exec(text);
    const pending = Boolean(tagStart);
    return { chat: pending ? text.slice(0, tagStart!.index).trim() || "Preparing your file preview…" : text, artifact: null, pending };
  }
  const headerTerminator = text.indexOf("\n", start.index + start[0].length - 1);
  const contentStart = headerTerminator >= 0 ? headerTerminator + 1 : start.index + start[0].length;
  const end = text.indexOf("```", contentStart);
  if (end < 0) return { chat: text.slice(0, start.index).trim() || "Preparing your file preview…", artifact: null, pending: true };
  const chat = `${text.slice(0, start.index)} ${text.slice(end + 3)}`.trim();
  const title = start[2].trim();
  const filename = start[3].trim().replace(/[,;]+$/, "");
  if (!filename || !/^[\w./ -]+$/.test(filename)) {
    const tagStart = headerPrefix.exec(text)!;
    return { chat: text.slice(0, tagStart.index).trim() || "Preparing your file preview…", artifact: null, pending: true };
  }
  return {
    chat,
    artifact: {
      kind: start[1].toLowerCase() as ChatArtifact["kind"],
      title,
      filename,
      content: text.slice(contentStart, end).replace(/^\n/, "").trimEnd(),
    },
    pending: false,
  };
}

function csvRows(value: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < value.length; i += 1) {
    const char = value[i];
    if (char === '"' && quoted && value[i + 1] === '"') { cell += '"'; i += 1; }
    else if (char === '"') quoted = !quoted;
    else if (char === "," && !quoted) { row.push(cell); cell = ""; }
    else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && value[i + 1] === "\n") i += 1;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += char;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((item) => item.some((column) => column.length));
}

function formatTime(iso: string | null) {
  if (!iso) return "";
  const date = new Date(iso);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const ChatWorkspace: React.FC = () => {
  const { user, token, logout } = useAuth();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploadingFiles, setUploadingFiles] = useState(0);
  const attachmentFiles = useRef(new Map<number, File>());
  const [sending, setSending] = useState(false);
  const [researchStatus, setResearchStatus] = useState("");
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  // Web search is enabled by default so current answers can use fresh sources.
  const [webSearch, setWebSearch] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{ id: number; title: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState("");
  const [chatRoomsOpen, setChatRoomsOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [searchParams] = useSearchParams();
  const [artifactCopied, setArtifactCopied] = useState(false);
  const artifactFrames = useRef(new Map<number, HTMLIFrameElement>());
  const artifactView = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const item = messages[index];
      if (item.role !== "assistant") continue;
      const savedArtifact = item.meta?.artifact as ChatArtifact | undefined;
      const parsed = parseArtifact(item.content || "");
      const artifactPending = Boolean(item.meta?.artifactPending);
      if (savedArtifact) return { messageId: item.id, chat: parsed.chat, artifact: savedArtifact, pending: false };
      if (parsed.artifact || parsed.pending || artifactPending) return { messageId: item.id, ...parsed, pending: parsed.pending || artifactPending };
    }
    return null;
  }, [messages]);

  const canSend = Boolean(token) && !sending && !artifactView?.pending && (prompt.trim().length > 0 || attachments.length > 0);

  const loadConversations = useCallback(async () => {
    if (!token) return;
    try {
      setConversations(await chatV2.conversations(token, showArchived));
    } catch (err: any) {
      if (err?.status === 401) {
        logout();
        return;
      }
      setError(err?.message || "Could not load conversations");
    }
  }, [token, showArchived]);

  useEffect(() => {
    if (!token) return;
    loadConversations();
  }, [token, loadConversations]);

  useEffect(() => {
    const requestedId = Number(searchParams.get("conversation"));
    if (requestedId > 0 && conversations.some((conversation) => conversation.id === requestedId)) {
      setActiveId(requestedId);
    }
  }, [conversations, searchParams]);

  useEffect(() => {
    if (!token || activeId == null) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setLoadingMessages(true);
    chatV2
      .conversation(token, activeId)
      .then((data) => {
        if (!cancelled) setMessages(data.messages);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoadingMessages(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, activeId]);

  // Ctrl/Cmd+K starts a new chat, Esc stops a running stream.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        void createConversation();
      }
      if (event.key === "Escape" && abortRef.current) abortRef.current.abort();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const openConversation = (id: number) => {
    setActiveId(id);
    setError("");
  };

  const createConversation = async () => {
    if (!token) return;
    try {
      const conversation = await chatV2.createConversation(token, "New chat");
      setConversations((current) => [conversation, ...current]);
      setActiveId(conversation.id);
      setMessages([]);
      textareaRef.current?.focus();
    } catch (err: any) {
      setError(err?.message || "Could not create a conversation");
    }
  };

  const attachFiles = async (files: FileList | File[]) => {
    if (!token) return;
    const selected = Array.from(files);
    if (!selected.length) return;
    setError("");
    setNotice(selected.length === 1 ? `Uploading ${selected[0].name}…` : `Uploading ${selected.length} files…`);
    setUploadingFiles(selected.length);
    for (const file of selected) {
      try {
        const uploaded = await chatV2.upload(token, file, activeId ?? undefined);
        const preview = file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined;
        attachmentFiles.current.set(uploaded.id, file);
        setAttachments((current) => [...current, preview ? { ...uploaded, preview } : uploaded]);
      } catch (err: any) {
        const message = err?.message || "Upload failed";
        setError(`${file.name}: ${message}`);
      } finally {
        setUploadingFiles((current) => Math.max(0, current - 1));
      }
    }
    setNotice((current) => current.startsWith("Uploading ") ? "File attached — add your instruction and press Send." : current);
  };

  const send = async (override?: string) => {
    if (!token) return;
    const content = (override ?? prompt).trim();
    if (!content && !attachments.length) return;
    let conversationId = activeId;
    if (conversationId == null) {
      try {
        const conversation = await chatV2.createConversation(token, content.slice(0, 60) || "New chat");
        setConversations((current) => [conversation, ...current]);
        conversationId = conversation.id;
        setActiveId(conversation.id);
      } catch (err: any) {
        setError(err?.message || "Could not start a conversation");
        return;
      }
    }
    setSending(true);
    setResearchStatus(attachments.length ? "Preparing your attachment…" : "Preparing your answer…");
    setError("");
    const attachmentIds = attachments.map((item) => item.id);
    const imageAttachment = attachments.find((item) => item.kind === "image" && attachmentFiles.current.has(item.id));
    const optimistic: ChatMessage = {
      id: -Date.now(),
      role: "user",
      content: content || "Attached files",
      provider: null,
      model: null,
      status: "complete",
      attachments,
      citations: [],
      meta: {},
      created_at: new Date().toISOString(),
    };
    const placeholder: ChatMessage = {
      id: -Date.now() - 1,
      role: "assistant",
      content: "",
      provider: null,
      model: "auto",
      status: "streaming",
      attachments: [],
      citations: [],
      meta: {},
      created_at: new Date().toISOString(),
      pending: true,
    };
    setMessages((current) => [...current, optimistic, placeholder]);
    setPrompt("");
    setAttachments([]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      if (imageAttachment) {
        setResearchStatus("Editing your image…");
        const sourceFile = attachmentFiles.current.get(imageAttachment.id);
        if (!sourceFile) throw new Error("The attached image is no longer available. Please attach it again.");
        const output = await imageApi.generate(token, content || "Improve this image", sourceFile);
        const imageUrl = URL.createObjectURL(output);
        attachmentIds.forEach((id) => attachmentFiles.current.delete(id));
        setMessages((current) => current.map((message) => message.id === placeholder.id
          ? {
              ...message,
              content: "Image edited successfully.",
              status: "complete",
              pending: false,
              meta: { kind: "image", imageUrl, prompt: content || "Improve this image" },
            }
          : message));
        return;
      }
      attachmentIds.forEach((id) => attachmentFiles.current.delete(id));
      let text = "";
      const updateStreamedText = (value: string) => {
        const parsed = parseArtifact(value);
        setMessages((current) =>
          current.map((message) => message.id === placeholder.id
            ? { ...message, content: parsed.chat, meta: { ...message.meta, artifact: parsed.artifact, artifactPending: parsed.pending }, pending: true }
            : message),
        );
      };
      for await (const event of chatV2.stream(
        token,
        conversationId,
        { content, model: "auto", attachment_ids: attachmentIds, web_search: webSearch },
        controller.signal,
      )) {
        if (event.error) throw new Error(event.error);
        if (event.status) setResearchStatus(event.label || event.status);
        if (event.chunk) {
          text += event.chunk;
          updateStreamedText(text);
        }
        if (event.message) {
          const final = event.message;
          const parsed = parseArtifact(final.content || text);
          const normalizedFinal = {
            ...final,
            content: parsed.chat,
            meta: { ...final.meta, artifactPending: parsed.pending, ...(parsed.artifact ? { artifact: parsed.artifact } : {}) },
          };
          setMessages((current) =>
            current.map((message) => {
              if (message.id === placeholder.id) return { ...normalizedFinal, attachments: [] };
              if (message.id === optimistic.id) return final.role === "user" ? { ...final, attachments: optimistic.attachments } : message;
              return message;
            }),
          );
        }
      }
    } catch (err: any) {
      if (err?.name === "AbortError") {
        setMessages((current) =>
          current.map((message) =>
            message.id === placeholder.id
              ? { ...message, pending: false, status: "stopped", error: "Stopped by you" }
              : message,
          ),
        );
      } else {
        if (err?.status === 401) {
          logout();
          return;
        }
        const text = err?.message || "The model did not answer";
        const partialArtifact = parseArtifact(typeof text === "string" ? text : "");
        setMessages((current) =>
          current.map((message) =>
            message.id === placeholder.id
              ? {
                  ...message,
                  content: partialArtifact.chat,
                  pending: false,
                  status: "failed",
                  error: text,
                  meta: { ...message.meta, artifactPending: partialArtifact.pending, ...(partialArtifact.artifact ? { artifact: partialArtifact.artifact } : {}) },
                }
              : message,
          ),
        );
        setError(text);
      }
    } finally {
      attachmentIds.forEach((id) => attachmentFiles.current.delete(id));
      setSending(false);
      setResearchStatus("");
      abortRef.current = null;
      loadConversations();
    }
  };

  const stop = () => abortRef.current?.abort();

  const copyArtifact = async (content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setArtifactCopied(true);
      window.setTimeout(() => setArtifactCopied(false), 1600);
    } catch {
      setError("Clipboard is not available in this browser");
    }
  };

  const downloadArtifact = (artifact: ChatArtifact, exportCsv = false) => {
    const mime = artifact.kind === "pdf" ? "text/html;charset=utf-8" : artifact.kind === "excel" ? "text/csv;charset=utf-8" : "text/plain;charset=utf-8";
    const blob = new Blob([artifact.content], { type: mime });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    const fallback = artifact.kind === "pdf" ? "report.html" : artifact.kind === "excel" ? "data.csv" : "source.txt";
    const filename = artifact.filename || fallback;
    anchor.download = exportCsv && artifact.kind === "excel" ? filename.replace(/\.(xlsx|xls|csv)$/i, ".csv") : filename;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
  };

  const printArtifact = (frame: HTMLIFrameElement | null) => {
    try { frame?.contentWindow?.print(); } catch { setError("Could not open the print dialog"); }
  };

  const convertCsvToXlsx = async (csv: string) => {
    const csvRowsData = csvRows(csv);
    const table = csvRowsData.map((row) => row.map((cell) => `<td>${cell.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</td>`).join(""))
      .map((row) => `<tr>${row}</tr>`).join("");
    const workbook = `<!doctype html><html><head><meta charset="utf-8"></head><body><table>${table}</table></body></html>`;
    const blob = new Blob([workbook], { type: "application/vnd.ms-excel;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "data.xls";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
  };

  const reuseAsSource = async (url: string) => {
    try {
      const blob = await (await fetch(url)).blob();
      await attachFiles([new File([blob], "previous-image.png", { type: blob.type || "image/png" })]);
      setNotice("Image attached — describe what you want and press Send.");
    } catch {
      setError("Could not reuse this image");
    }
  };

  const regenerate = async (messageId: number) => {
    if (!token || activeId == null) return;
    setSending(true);
    setError("");
    try {
      const { message } = await chatV2.regenerate(token, activeId, messageId);
      setMessages((current) => current.map((item) => (item.id === messageId ? message : item)));
    } catch (err: any) {
      setError(err?.message || "Regenerate failed");
    } finally {
      setSending(false);
    }
  };

  const copy = async (message: ChatMessage) => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedId(message.id);
      window.setTimeout(() => setCopiedId(null), 1600);
    } catch {
      setError("Clipboard is not available in this browser");
    }
  };

  const deleteConversation = async (id: number, confirmFirst = true) => {
    if (!token) return;
    if (confirmFirst) {
      const target = conversations.find((item) => item.id === id);
      setPendingDelete({ id, title: target?.title || "this chat" });
      return;
    }
    setDeleting(true);
    try {
      await chatV2.deleteConversation(token, id);
      setConversations((current) => current.filter((item) => item.id !== id));
      if (activeId === id) {
        setActiveId(null);
        setMessages([]);
      }
      setPendingDelete(null);
    } catch (err: any) {
      setError(err?.message || "Delete failed");
    } finally {
      setDeleting(false);
    }
  };

  const archiveConversation = async (conversation: Conversation) => {
    if (!token) return;
    try {
      await chatV2.updateConversation(token, conversation.id, { is_archived: !conversation.is_archived });
      loadConversations();
    } catch (err: any) {
      setError(err?.message || "Could not update conversation");
    }
  };

  const projectConversation = async (conversation: Conversation) => {
    if (!token) return;
    try {
      await chatV2.updateConversation(token, conversation.id, { is_pinned: !conversation.is_pinned });
      loadConversations();
    } catch (err: any) {
      setError(err?.message || "Could not update project");
    }
  };

  const filtered = useMemo(
    () => conversations.filter((conversation) => conversation.title.toLowerCase().includes(search.trim().toLowerCase())),
    [conversations, search],
  );

  const activeTitle = conversations.find((conversation) => conversation.id === activeId)?.title ?? "Chat";

  return (
    <div className="cw-shell">
      <ChatNavigationDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        conversations={filtered}
        activeId={activeId}
        search={search}
        onSearchChange={setSearch}
        onNewChat={() => { void createConversation(); setDrawerOpen(false); }}
        onOpenConversation={openConversation}
        onArchiveConversation={archiveConversation}
        onProjectConversation={projectConversation}
        onDeleteConversation={deleteConversation}
        showArchived={showArchived}
        onToggleArchived={() => setShowArchived((value) => !value)}
        user={user}
      />
      <aside className={`cw-sidebar ${chatRoomsOpen ? "cw-sidebar-open" : ""}`}>
        <div className="cw-sidebar-head">
          <div className="cw-brand">
            <span>✦</span>
            <div>
              <strong>AI Workspace</strong>
              <small>{user?.full_name || user?.email || "Account"}</small>
            </div>
          </div>
          <button className="cw-new" onClick={createConversation}>＋ New chat</button>
        </div>
        <div className="cw-search">
          <span>🔍</span>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search conversations" />
        </div>
        <div className="cw-conv-list">
          {filtered.length === 0 && <p className="cw-empty-list">{search ? "No matches" : "No conversations yet"}</p>}
          {filtered.map((conversation) => (
            <div key={conversation.id} className={`cw-conv ${conversation.id === activeId ? "active" : ""}`}>
              <button className="cw-conv-main" onClick={() => openConversation(conversation.id)}>
                <span className="cw-conv-icon">{conversation.is_pinned ? "★" : "◌"}</span>
                <span className="cw-conv-copy">
                  <strong>{conversation.title}</strong>
                  <small>{conversation.is_archived ? "Archived" : conversation.provider || "local"} · {formatTime(conversation.updated_at ?? null)}</small>
                </span>
              </button>
              <div className="cw-conv-actions">
                <button title="Archive" aria-label={`Archive ${conversation.title}`} onClick={() => archiveConversation(conversation)}>◱</button>
                <button title="Delete" aria-label={`Delete ${conversation.title}`} className="cw-conv-delete" onClick={() => deleteConversation(conversation.id)}>🗑</button>
              </div>
            </div>
          ))}
        </div>
        <div className="cw-sidebar-foot">
          <button className="cw-archive-toggle" onClick={() => setShowArchived((value) => !value)}>
            {showArchived ? "Hide archived" : "Show archived"}
          </button>
          <Link className="cw-profile" to="/profile" title="Open your profile">
            <span className="cw-profile-avatar">{(user?.full_name || user?.email || "U").slice(0, 1).toUpperCase()}</span>
            <span className="cw-profile-copy">
              <strong>{user?.full_name || "Account"}</strong>
              <small>{user?.email}</small>
            </span>
            <span className="cw-profile-chevron">›</span>
          </Link>
        </div>
      </aside>

      <main className={`cw-main ${artifactView ? "has-artifact" : ""}`}>
        <header className="cw-topbar">
          <div className="cw-title">
            <button
              type="button"
              className="cw-menu-toggle"
              aria-label="Open chat features and history"
              aria-expanded={drawerOpen}
              onClick={() => setDrawerOpen((open) => !open)}
            >
              <span aria-hidden="true">☰</span>
            </button>
            <h1>{activeTitle}</h1>
          </div>
          <div className="cw-topbar-actions" />
        </header>

        <div className="cw-messages">
          {loadingMessages && messages.length === 0 && (
            <div className="cw-skeleton">
              {[0, 1, 2].map((row) => (
                <div className="cw-skeleton-row" key={row}>
                  <span />
                  <i />
                </div>
              ))}
            </div>
          )}
          {messages.map((message) => (
            <article key={message.id} className={`cw-msg ${message.role} ${message.error ? "failed" : ""}`}>
              <div className="cw-msg-avatar">{message.role === "user" ? (user?.email || "U").slice(0, 1).toUpperCase() : "✦"}</div>
              <div className="cw-msg-body">
                <div className="cw-msg-head">
                  <strong>{message.role === "user" ? "You" : message.provider ? `${message.provider}` : "Assistant"}</strong>
                  {message.role === "assistant" && message.model && !message.pending && <em>{message.model}</em>}
                  <small>{formatTime(message.created_at)}</small>
                </div>
                {message.attachments?.length > 0 && (
                  <div className="cw-attach-list">
                    {message.attachments.map((attachment) => (
                      <span key={attachment.id}>
                        {attachment.content_type.startsWith("image/") && attachment.preview ? <img src={attachment.preview} alt={attachment.filename} /> : "📎"} {attachment.filename}
                      </span>
                    ))}
                  </div>
                )}
                {message.role === "user" && typeof message.meta?.attachedImageUrl === "string" && (
                  <div className="cw-user-image"><img src={String(message.meta.attachedImageUrl)} alt="Image supplied for editing" /></div>
                )}
                {message.meta?.kind === "image" && message.meta?.imageUrl ? (
                  <div className="cw-image-result">
                    <img src={String(message.meta.imageUrl)} alt={String(message.meta.prompt || "Generated image")} />
                    <div className="cw-image-actions">
                      <button onClick={() => copy({ ...message, content: String(message.meta?.prompt || "") })}>
                        {copiedId === message.id ? "✓ Prompt copied" : "⧉ Copy prompt"}
                      </button>
                      <a href={String(message.meta.imageUrl)} download="ai-workspace-image.png">⤓ Download</a>
                      <button onClick={() => reuseAsSource(String(message.meta.imageUrl))}>✎ Edit this</button>
                    </div>
                  </div>
                ) : (
                <div className="cw-msg-content">
                  {(() => {
                    const parsed = message.role === "assistant" ? parseArtifact(message.content || "") : { chat: message.content, artifact: null, pending: false };
                    const displayText = parsed.pending ? parsed.chat : parsed.artifact ? "" : parsed.chat;
                    return displayText ? renderMarkdown(displayText) : message.pending && !parsed.pending ? <span className="cw-typing" aria-label="Assistant is typing"><i /><i /><i /></span> : null;
                  })()}
                  {message.pending && message.content && !parseArtifact(message.content).pending && !parseArtifact(message.content).artifact && <span className="cw-caret" />}
                </div>
                )}
                {message.error && <p className="cw-msg-error">{message.error}</p>}
                {message.citations?.length > 0 && (
                  <div className="cw-citations">
                    <strong>Sources</strong>
                    <ol>
                      {message.citations.map((source, index) => (
                        <li key={`${message.id}-${index}`}>
                          <a href={source.url} target="_blank" rel="noreferrer noopener">{source.title}</a>
                          {source.published_date && <small>{source.published_date}</small>}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}
                {message.role === "assistant" && !message.pending && message.status !== "streaming" && message.meta?.kind !== "image" && !message.meta?.artifact && (
                  <div className="cw-msg-actions">
                    <button onClick={() => copy(message)}>{copiedId === message.id ? "✓ Copied" : "⧉ Copy"}</button>
                    <button onClick={() => regenerate(message.id)} disabled={sending}>↻ Regenerate</button>
                    {message.content && (
                      <a
                        href={`data:text/markdown;charset=utf-8,${encodeURIComponent(message.content)}`}
                        download="answer.md"
                      >
                        ⤓ Save
                      </a>
                    )}
                  </div>
                )}
              </div>
            </article>
          ))}
          <div ref={bottomRef} />
        </div>

        {error && <div className="cw-error" role="alert">{error}<button onClick={() => setError("")} aria-label="Dismiss error">✕</button><button className="cw-retry" onClick={() => loadConversations()}>Retry</button></div>}

        {artifactView && (
          <aside className="cw-artifact-pane" aria-label="File preview">
            <header className="cw-artifact-head">
              <div><small>FILE PREVIEW</small><strong>{artifactView.artifact?.title || "Preparing artifact"}</strong></div>
              {artifactView.artifact && <span>{artifactView.artifact.filename}</span>}
            </header>
            {artifactView.pending || !artifactView.artifact ? (
              <div className="cw-artifact-loading"><span className="cw-typing" aria-label="Preparing preview"><i /><i /><i /></span><p>Building your file preview…</p></div>
            ) : (
              <>
                <div className="cw-artifact-toolbar">
                  <button onClick={() => downloadArtifact(artifactView.artifact!)}>{artifactView.artifact.kind === "excel" ? "Download CSV" : "Download"}</button>
                  {artifactView.artifact.kind === "excel" && <button onClick={() => downloadArtifact(artifactView.artifact!, true)}>Export CSV</button>}
                  <button onClick={() => void copyArtifact(artifactView.artifact!.content)}>{artifactCopied ? "Copied" : "Copy"}</button>
                  {artifactView.artifact.kind === "excel" && <button onClick={() => void convertCsvToXlsx(artifactView.artifact!.content)}>Export XLS</button>}
                  {artifactView.artifact.kind === "pdf" && <button onClick={() => printArtifact(artifactFrames.current.get(artifactView.messageId) || null)}>Print</button>}
                </div>
                <div className="cw-artifact-body">
                  {artifactView.artifact.kind === "pdf" ? (
                    <iframe ref={(frame) => { if (frame) artifactFrames.current.set(artifactView.messageId, frame); else artifactFrames.current.delete(artifactView.messageId); }} className="cw-artifact-frame" title={artifactView.artifact.title} sandbox="allow-scripts" srcDoc={artifactView.artifact.content} />
                  ) : artifactView.artifact.kind === "excel" ? (
                    <div className="cw-artifact-table-wrap"><table className="cw-artifact-table"><tbody>{csvRows(artifactView.artifact.content).map((row, rowIndex) => {
                      const Cell = rowIndex === 0 ? "th" : "td";
                      return <tr key={rowIndex}>{row.map((cell, cellIndex) => <Cell key={cellIndex}>{cell}</Cell>)}</tr>;
                    })}</tbody></table></div>
                  ) : (
                    <pre className="cw-artifact-code"><code>{artifactView.artifact.content}</code></pre>
                  )}
                </div>
              </>
            )}
          </aside>
        )}

        <div className="cw-composer-wrap">
          <form
            className="cw-composer"
            onSubmit={(event) => {
              event.preventDefault();
              if (canSend) void send();
            }}
          >
            <input className="cw-visually-hidden" type="checkbox" checked={webSearch} aria-label="Search the web for this answer" onChange={(event) => setWebSearch(event.target.checked)} />
            {attachments.length > 0 && (
              <div className="cw-composer-attachments" aria-label="Attached files">
                {attachments.map((attachment) => (
                  <span key={attachment.id} className="cw-file-chip">
                    {attachment.content_type.startsWith("image/") && attachment.preview ? <img src={attachment.preview} alt="" /> : <span>📎</span>}
                    <b>{attachment.filename}</b>
                    <button type="button" onClick={() => { attachmentFiles.current.delete(attachment.id); setAttachments((current) => current.filter((item) => item.id !== attachment.id)); }} aria-label={`Remove ${attachment.filename}`}>✕</button>
                  </span>
                ))}
              </div>
            )}
            <textarea
              ref={textareaRef}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  if (canSend) void send();
                }
              }}
              placeholder="Message the assistant… attach a file and describe what you need"
              rows={3}
            />
            <div className="cw-composer-actions">
              <button type="button" className="cw-icon" title="Attach files" onClick={() => fileRef.current?.click()}>📎</button>
              <VoiceControls onTranscript={(text) => setPrompt((current) => (current ? `${current} ${text}` : text))} />
              <span className={`cw-hint${researchStatus || uploadingFiles ? " is-active" : ""}`} role="status">
                {uploadingFiles ? `Uploading ${uploadingFiles} file${uploadingFiles === 1 ? "" : "s"}…` : researchStatus || "Chat and attachments ready"}
              </span>
              {sending ? (
                <button type="button" className="cw-stop" onClick={stop}>■ Stop</button>
              ) : (
              <button type="submit" className="cw-send" disabled={!canSend || uploadingFiles > 0}>{sending ? "Working…" : uploadingFiles ? "Uploading…" : "Send ↑"}</button>
              )}
            </div>
          </form>
          {notice && <p className="cw-notice">{notice}<button onClick={() => setNotice("")}>✕</button></p>}
          <p className="cw-disclaimer">
            Answers are generated by the provider your admin enabled. Verify important information.
          </p>
        </div>
        <input
          ref={fileRef}
          type="file"
          multiple
          hidden
          accept=".pdf,.docx,.pptx,.xlsx,.xlsm,.txt,.md,.csv,image/png,image/jpeg,image/webp,audio/*"
          onChange={(e) => {
            if (e.target.files) void attachFiles(e.target.files);
            e.target.value = "";
          }}
        />
        {pendingDelete && (
          <div className="cw-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="cw-delete-title" onClick={() => !deleting && setPendingDelete(null)}>
            <div className="cw-modal" onClick={(e) => e.stopPropagation()}>
              <h3 id="cw-delete-title">Delete this chat?</h3>
              <p>
                “{pendingDelete.title}” and all of its messages will be permanently removed. This cannot be undone.
              </p>
              <div className="cw-modal-actions">
                <button className="cw-ghost" onClick={() => setPendingDelete(null)} disabled={deleting}>Cancel</button>
                <button className="cw-danger" onClick={() => void deleteConversation(pendingDelete.id, false)} disabled={deleting}>
                  {deleting ? "Deleting…" : "Delete chat"}
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
};

export default ChatWorkspace;
