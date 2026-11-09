import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import { Attachment, ChatMessage, ChatModel, Conversation, chatV2, imageApi } from "../api/workspaceApi";
import renderMarkdown from "../components/Markdown";
import VoiceControls from "../components/VoiceControls";

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

const STARTERS = [
  { icon: "✍", title: "Draft something", prompt: "Write a clear 150-word product announcement for a new note-taking app." },
  { icon: "◫", title: "Analyse text", prompt: "Summarise the key risks in this plan and suggest mitigations: " },
  { icon: "⌘", title: "Write code", prompt: "Write a Python function that paginates an API using cursors, with tests." },
  { icon: "✦", title: "Think it through", prompt: "Explain how vector databases work to a non-technical founder." },
];

function formatTime(iso: string | null) {
  if (!iso) return "";
  const date = new Date(iso);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

const ChatWorkspace: React.FC = () => {
  const { user, token } = useAuth();
  const [models, setModels] = useState<ChatModel[]>([]);
  const [modelId, setModelId] = useState("auto");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [sending, setSending] = useState(false);
  const [researchStatus, setResearchStatus] = useState("");
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  // Web search is enabled by default so current answers can use fresh sources.
  const [webSearch, setWebSearch] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const imageFileRef = useRef<HTMLInputElement>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{ id: number; title: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState("");
  const [chatMode, setChatMode] = useState<"chat" | "image">("chat");
  const [featureMenuOpen, setFeatureMenuOpen] = useState(false);
  const [imagePrompt, setImagePrompt] = useState("");
  const [imageSource, setImageSource] = useState<File | null>(null);
  const [imageBusy, setImageBusy] = useState(false);
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

  const activeModel = models.find((model) => model.id === modelId);
  const canSend = Boolean(token) && !sending && !artifactView?.pending && (prompt.trim().length > 0 || attachments.length > 0);

  const loadConversations = useCallback(async () => {
    if (!token) return;
    try {
      setConversations(await chatV2.conversations(token, showArchived));
    } catch (err: any) {
      setError(err?.message || "Could not load conversations");
    }
  }, [token, showArchived]);

  useEffect(() => {
    if (!token) return;
    chatV2.models(token).then((data) => setModels(data.models)).catch((err) => setError(err.message));
    loadConversations();
  }, [token, loadConversations]);

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
    for (const file of Array.from(files)) {
      try {
        const uploaded = await chatV2.upload(token, file, activeId ?? undefined);
        setAttachments((current) => [...current, uploaded]);
      } catch (err: any) {
        setError(err?.message || "Upload failed");
      }
    }
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
    setError("");
    const attachmentIds = attachments.map((item) => item.id);
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
      model: modelId,
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
        { content, model: modelId, attachment_ids: attachmentIds, web_search: webSearch },
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
              if (message.id === optimistic.id) return final.role === "user" ? final : message;
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

  const generateInChat = async () => {
    if (!token) return;
    const text = imagePrompt.trim();
    if (!text) return;
    let conversationId = activeId;
    if (conversationId == null) {
      try {
        const conversation = await chatV2.createConversation(token, text.slice(0, 60) || "Image request", "chat");
        setConversations((current) => [conversation, ...current]);
        conversationId = conversation.id;
        setActiveId(conversation.id);
      } catch (err: any) {
        setError(err?.message || "Could not start a conversation");
        return;
      }
    }
    setImageBusy(true);
    setError("");
    const userMessage: ChatMessage = {
      id: -Date.now(),
      role: "user",
      content: imageSource ? `✎ Edit image: ${text}` : `✦ Generate image: ${text}`,
      provider: null,
      model: null,
      status: "complete",
      attachments: [],
      citations: [],
      meta: {}, created_at: new Date().toISOString(),
    };
    const placeholder: ChatMessage = {
      id: -Date.now() - 1,
      role: "assistant",
      content: "",
      provider: "image",
      model: null,
      status: "streaming",
      attachments: [],
      citations: [],
      meta: { kind: "image", pending: true },
      created_at: new Date().toISOString(),
      pending: true,
    };
    setMessages((current) => [...current, userMessage, placeholder]);
    const sentPrompt = text;
    const sentImage = imageSource;
    setImagePrompt("");
    setImageSource(null);
    try {
      const blob = await imageApi.generate(token, sentPrompt, sentImage);
      const url = URL.createObjectURL(blob);
      setMessages((current) =>
        current.map((message) =>
          message.id === placeholder.id
            ? {
                ...message,
                pending: false,
                status: "complete",
                content: sentPrompt,
                meta: { kind: "image", imageUrl: url, prompt: sentPrompt, edited: Boolean(sentImage) },
              }
            : message,
        ),
      );
      loadConversations();
    } catch (err: any) {
      const reason = err?.message || "Image request failed";
      setMessages((current) =>
        current.map((message) =>
          message.id === placeholder.id ? { ...message, pending: false, status: "failed", error: reason, content: "" } : message,
        ),
      );
      setError(reason);
    } finally {
      setImageBusy(false);
    }
  };

  const reuseAsSource = async (url: string) => {
    try {
      const blob = await (await fetch(url)).blob();
      setImageSource(new File([blob], "previous-image.png", { type: blob.type || "image/png" }));
      setChatMode("image");
      setNotice("Image attached — describe the change you want.");
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

  const exportChat = async () => {
    if (!token || activeId == null) return;
    try {
      const data = await chatV2.exportConversation(token, activeId);
      const blob = new Blob([data.markdown], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${data.title.replace(/[^\w\- ]+/g, "") || "conversation"}.md`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 3000);
    } catch (err: any) {
      setError(err?.message || "Export failed");
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

  const filtered = useMemo(
    () => conversations.filter((conversation) => conversation.title.toLowerCase().includes(search.trim().toLowerCase())),
    [conversations, search],
  );

  const activeTitle = conversations.find((conversation) => conversation.id === activeId)?.title ?? "New chat";

  return (
    <div className="cw-shell">
      <aside className="cw-sidebar">
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
            <h1>{activeTitle}</h1>
            <small>{messages.length} messages · context sent automatically</small>
          </div>
          <div className="cw-topbar-actions">
            <button className="cw-feature-toggle" type="button" aria-expanded={featureMenuOpen} onClick={() => setFeatureMenuOpen((open) => !open)}>
              <span aria-hidden="true">☰</span> Features
            </button>
            <label className="cw-model-picker" title={activeModel?.reason || "Choose the model for the next message"}>
              <span>Model</span>
              <select value={modelId} onChange={(e) => setModelId(e.target.value)}>
                {models.map((model) => (
                  <option key={model.id} value={model.id} disabled={!model.selectable}>
                    {model.label}{model.selectable ? "" : " — unavailable"}
                  </option>
                ))}
                {models.length === 0 && <option value="auto">Loading…</option>}
              </select>
            </label>
            <button className="cw-ghost" onClick={exportChat} disabled={activeId == null} title="Export as Markdown">⤓ Export</button>
            <a className="cw-ghost" href="/documents" title="Open document tools">▤ Docs</a>
            <a className="cw-ghost" href="/images" title="Open image AI">◫ Images</a>
          </div>
        </header>

        {featureMenuOpen && (
          <div className="cw-feature-menu" aria-label="Workspace features">
            <button className={chatMode === "chat" ? "selected" : ""} type="button" onClick={() => { setChatMode("chat"); setFeatureMenuOpen(false); }}>
              <span>💬</span><strong>Chat</strong><small>Ask anything</small>
            </button>
            <button className={chatMode === "image" ? "selected" : ""} type="button" onClick={() => { setChatMode("image"); setFeatureMenuOpen(false); }}>
              <span>✦</span><strong>Image</strong><small>Generate or edit</small>
            </button>
            <Link to="/video-studio" onClick={() => setFeatureMenuOpen(false)}><span>▶</span><strong>Video</strong><small>Create videos</small></Link>
            <Link to="/documents" onClick={() => setFeatureMenuOpen(false)}><span>▤</span><strong>Documents</strong><small>Read and generate</small></Link>
            <Link to="/office" onClick={() => setFeatureMenuOpen(false)}><span>▦</span><strong>Office</strong><small>Workspace tools</small></Link>
            <Link to="/research" onClick={() => setFeatureMenuOpen(false)}><span>◎</span><strong>Research</strong><small>Deep research</small></Link>
          </div>
        )}

        {activeModel && !activeModel.selectable && (
          <div className="cw-banner warn">
            This model is not selectable right now: {activeModel.reason || "not configured"}. Auto will use the next available free route.
          </div>
        )}

        <div className="cw-messages">
          {messages.length === 0 && (
            <div className="cw-starters">
              <p className="cw-eyebrow">Start a conversation</p>
              <h2>What would you like to get done?</h2>
              <div className="cw-starter-grid">
                {STARTERS.map((starter) => (
                  <button key={starter.title} onClick={() => send(starter.prompt)}>
                    <span>{starter.icon}</span>
                    <strong>{starter.title}</strong>
                    <small>{starter.prompt.slice(0, 70)}…</small>
                  </button>
                ))}
              </div>
            </div>
          )}
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
                      <span key={attachment.id}>📎 {attachment.filename}</span>
                    ))}
                  </div>
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
          {attachments.length > 0 && (
            <div className="cw-pending-files">
              {attachments.map((attachment) => (
                <span key={attachment.id}>
                  📎 {attachment.filename}
                  <button onClick={() => setAttachments((current) => current.filter((item) => item.id !== attachment.id))}>✕</button>
                </span>
              ))}
            </div>
          )}
          <div className="cw-mode-row" role="tablist" aria-label="Composer mode">
            <button type="button" role="tab" aria-selected={chatMode === "chat"} className={chatMode === "chat" ? "active" : ""} onClick={() => setChatMode("chat")}>💬 Chat</button>
            <button type="button" role="tab" aria-selected={chatMode === "image"} className={chatMode === "image" ? "active" : ""} onClick={() => setChatMode("image")}>✦ Image</button>
            <span className="cw-mode-hint">
              {chatMode === "image" ? (imageSource ? "Describe the edit — your image is the source" : "Describe the image to generate") : "Ask anything — attach files or dictate"}
            </span>
          </div>
          <form
            className="cw-composer"
            onSubmit={(event) => {
              event.preventDefault();
              if (chatMode === "image" && token && imagePrompt.trim() && !imageBusy) {
                void generateInChat();
                return;
              }
              if (chatMode === "chat") void send();
            }}
          >
            {chatMode === "image" ? (
              <>
                <textarea
                  value={imagePrompt}
                  onChange={(e) => setImagePrompt(e.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      if (token && imagePrompt.trim() && !imageBusy) void generateInChat();
                    }
                  }}
                  placeholder={imageSource ? "e.g. make the background a sunset beach" : "e.g. a cinematic photo of a mountain lake at sunrise"}
                  rows={2}
                />
                <div className="cw-composer-actions">
                  <button
                    type="button"
                    className={`cw-icon ${imageSource ? "has-source" : ""}`}
                    title={imageSource ? `Source: ${imageSource.name}` : "Attach an image to edit"}
                    onClick={() => imageFileRef.current?.click()}
                  >🖼</button>
                  {imageSource && (
                    <button type="button" className="cw-chip" onClick={() => setImageSource(null)}>
                      {imageSource.name.slice(0, 24)} ✕
                    </button>
                  )}
                  <span className="cw-hint">{imageSource ? "Image-to-image" : "Text-to-image"}</span>
                  <button type="submit" className="cw-send" disabled={!token || !imagePrompt.trim() || imageBusy}>
                    {imageBusy ? "Painting…" : imageSource ? "Apply edit ↑" : "Generate ↑"}
                  </button>
                </div>
              </>
            ) : (
              <>
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
                  placeholder="Message the assistant… (Enter to send, Shift+Enter for a new line)"
                  rows={2}
                />
                <div className="cw-composer-actions">
                  <button type="button" className="cw-icon" title="Attach files" onClick={() => fileRef.current?.click()}>📎</button>
                  <VoiceControls onTranscript={(text) => setPrompt((current) => (current ? `${current} ${text}` : text))} />
                  <span className="cw-hint">{researchStatus || "Ready"}</span>
                  {sending ? (
                    <button type="button" className="cw-stop" onClick={stop}>■ Stop</button>
                  ) : (
                    <button type="submit" className="cw-send" disabled={!canSend}>{sending ? "…" : "Send ↑"}</button>
                  )}
                </div>
              </>
            )}
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
        <input
          ref={imageFileRef}
          type="file"
          hidden
          accept="image/png,image/jpeg,image/webp"
          onChange={(e) => {
            const chosen = e.target.files?.[0];
            if (chosen) {
              setImageSource(chosen);
              setNotice(`Image attached: ${chosen.name}`);
            }
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
