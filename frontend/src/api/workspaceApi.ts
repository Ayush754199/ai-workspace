import { apiBaseUrl, readErrorDetail } from "./client";

export type ChatModel = {
  id: string;
  label: string;
  provider: string;
  capabilities: string[];
  free: boolean;
  selectable: boolean;
  reason: string | null;
};

export type Attachment = {
  id: number;
  filename: string;
  kind: string;
  content_type: string;
  size_bytes?: number;
  has_text?: boolean;
  preview?: string;
};

export type ChatMessage = {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  provider: string | null;
  model: string | null;
  status: string;
  attachments: Attachment[];
  citations: { title: string; url: string; snippet?: string; published_date?: string | null }[];
  meta: Record<string, unknown>;
  created_at: string | null;
  pending?: boolean;
  error?: string;
};

export type Conversation = {
  id: number;
  title: string;
  model: string | null;
  provider: string | null;
  mode: string;
  system_prompt: string | null;
  is_pinned: boolean;
  is_archived: boolean;
  message_count?: number;
  updated_at?: string | null;
};

export type ResearchResult = {
  query: string;
  answer: string | null;
  provider: string | null;
  sources: { title: string; url: string; snippet: string }[];
  error?: string;
};

async function request<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}/api/v1${path}`, {
      ...init,
      headers: {
        ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        Authorization: `Bearer ${token}`,
        ...(init.headers || {}),
      },
    });
  } catch {
    throw new Error(`Cannot reach the API at ${apiBaseUrl}. Is the backend running?`);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(readErrorDetail((data as { detail?: unknown }).detail, `Request failed (${response.status})`)) as Error & {
      status?: number;
    };
    error.status = response.status;
    throw error;
  }
  return data as T;
}

export const chatV2 = {
  models: (token: string) => request<{ models: ChatModel[] }>(token, "/chat/v2/models"),
  conversations: (token: string, includeArchived = false) =>
    request<Conversation[]>(token, `/chat/v2/conversations?include_archived=${includeArchived}`),
  createConversation: (token: string, title = "New chat", mode = "chat") =>
    request<Conversation>(token, "/chat/v2/conversations", {
      method: "POST",
      body: JSON.stringify({ title, mode }),
    }),
  conversation: (token: string, id: number) =>
    request<Conversation & { messages: ChatMessage[] }>(token, `/chat/v2/conversations/${id}`),
  updateConversation: (token: string, id: number, patch: Partial<Conversation>) =>
    request<Conversation>(token, `/chat/v2/conversations/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteConversation: (token: string, id: number) =>
    request<{ deleted: number }>(token, `/chat/v2/conversations/${id}`, { method: "DELETE" }),
  exportConversation: (token: string, id: number) =>
    request<{ title: string; markdown: string }>(token, `/chat/v2/conversations/${id}/export`),
  send: (token: string, id: number, content: string, model?: string, attachmentIds: number[] = [], webSearch = false) =>
    request<{ user_message: ChatMessage; message: ChatMessage }>(token, `/chat/v2/conversations/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, model, attachment_ids: attachmentIds, web_search: webSearch }),
    }),
  regenerate: (token: string, id: number, messageId: number) =>
    request<{ message: ChatMessage }>(token, `/chat/v2/conversations/${id}/regenerate`, {
      method: "POST",
      body: JSON.stringify({ message_id: messageId }),
    }),
  upload: (token: string, file: File, conversationId?: number) => {
    const form = new FormData();
    form.append("file", file);
    if (conversationId) form.append("conversation_id", String(conversationId));
    return request<Attachment>(token, "/chat/v2/attachments", { method: "POST", body: form });
  },

  /** SSE stream. Yields text chunks and finishes with the stored message. */
  async *stream(
    token: string,
    id: number,
    payload: { content: string; model?: string; attachment_ids?: number[]; web_search?: boolean },
    signal?: AbortSignal,
  ): AsyncGenerator<{ chunk?: string; message?: ChatMessage; error?: string; status?: string; label?: string; meta?: { user_message_id: number } }> {
    let response: Response;
    try {
      response = await fetch(`${apiBaseUrl}/api/v1/chat/v2/conversations/${id}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
        body: JSON.stringify(payload),
        signal,
      });
    } catch (error) {
      throw new Error(`Cannot reach the API at ${apiBaseUrl}. Is the backend running?`);
    }
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({}));
      throw new Error(readErrorDetail((data as { detail?: unknown }).detail, `Stream failed (${response.status})`));
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";
      for (const event of events) {
        const line = event.split("\n").find((l) => l.startsWith("data:"));
        if (!line) continue;
        const raw = line.slice(5).trim();
        if (raw === "[DONE]") return;
        try {
          yield JSON.parse(raw);
        } catch {
          // ignore keep-alive fragments
        }
      }
    }
  },
};

export type DocumentResult = {
  filename: string;
  kind: string;
  characters: number;
  words: number;
  pages?: number;
  text: string;
  ocr_unavailable?: boolean;
};

export const documentsApi = {
  extract: async (token: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentResult>(token, "/documents/extract", { method: "POST", body: form });
  },
  summarize: async (token: string, file: File, style: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("style", style);
    return request<{ summary: string; provider: string; filename: string }>(token, "/documents/summarize", { method: "POST", body: form });
  },
  ask: async (token: string, file: File, question: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("question", question);
    return request<{ answer: string; provider: string }>(token, "/documents/ask", { method: "POST", body: form });
  },
  generate: async (token: string, body: { title: string; content: string; format: string; filename?: string }) => {
    const response = await fetch(`${apiBaseUrl}/api/v1/documents/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(readErrorDetail((data as { detail?: unknown }).detail, "Document generation failed"));
    }
    return response.blob();
  },
};

export const imageApi = {
  understand: async (token: string, file: File, question: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("question", question);
    return request<{ answer: string }>(token, "/images/understand", { method: "POST", body: form });
  },
  removeBackground: async (token: string, file: File, tolerance = 28) => {
    const form = new FormData();
    form.append("file", file);
    form.append("tolerance", String(tolerance));
    const response = await fetch(`${apiBaseUrl}/api/v1/images/remove-background`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(readErrorDetail((data as { detail?: unknown }).detail, "Background removal failed"));
    }
    return response.blob();
  },
  enhance: async (token: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${apiBaseUrl}/api/v1/images/enhance`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(readErrorDetail((data as { detail?: unknown }).detail, "Enhancement failed"));
    }
    return response.blob();
  },
  variations: async (token: string, file: File, instruction: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("instruction", instruction);
    return request<{ prompts: string[]; provider: string; description: Record<string, unknown> }>(
      token,
      "/images/variations",
      { method: "POST", body: form },
    );
  },
  generate: async (token: string, prompt: string, image?: File | null) => {
    const form = new FormData();
    form.append("prompt", prompt);
    if (image) form.append("image", image);
    const response = await fetch(`${apiBaseUrl}/api/v1/chat/image`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const error = new Error(readErrorDetail((data as { detail?: unknown }).detail, "Image request failed")) as Error & { status?: number };
      error.status = response.status;
      throw error;
    }
    return response.blob();
  },
};

export const officeApi = {
  generate: async (token: string, body: { prompt: string; format: string; title?: string; filename?: string }) => {
    const response = await fetch(`${apiBaseUrl}/api/v1/office/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(readErrorDetail((data as { detail?: unknown }).detail, "Office generation failed"));
    }
    return { blob: await response.blob(), provider: response.headers.get("X-AI-Provider") };
  },
};

export const voiceApi = {
  status: (token: string) =>
    request<{ stt: Record<string, boolean>; tts: Record<string, boolean> }>(token, "/voice/status"),
  transcribe: async (token: string, blob: Blob, language = "en") => {
    const form = new FormData();
    form.append("file", blob, "recording.webm");
    form.append("language", language);
    return request<{ text: string; engine: string }>(token, "/voice/transcribe", { method: "POST", body: form });
  },
  speak: async (token: string, text: string) => {
    const response = await fetch(`${apiBaseUrl}/api/v1/voice/speak`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(readErrorDetail((data as { detail?: unknown }).detail, "Speech synthesis failed"));
    }
    return response.blob();
  },
};

export const researchApi = {
  query: (token: string, question: string, limit = 6) =>
    request<ResearchResult>(token, "/research/query", { method: "POST", body: JSON.stringify({ question, limit }) }),
};

export async function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 4000);
}
