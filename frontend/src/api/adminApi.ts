import type { ChatMessage, RoomMember } from "./chatApi";

export type AdminChatRoom = {
  id: number;
  name: string;
  room_type: string;
  members: RoomMember[];
  last_message_at: string | null;
};

export type AdminTranscript = AdminChatRoom & {
  messages: ChatMessage[];
  member_count: number;
};

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request<T>(token: string, path: string): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Admin request failed");
  return data as T;
}

export const adminApi = {
  listChats: (token: string, filters: { username?: string; role?: string; roomName?: string }) => {
    const query = new URLSearchParams();
    if (filters.username) query.set("username", filters.username);
    if (filters.role) query.set("role", filters.role);
    if (filters.roomName) query.set("room_name", filters.roomName);
    return request<{ items: AdminChatRoom[]; total: number }>(token, `/api/v1/admin/chats?${query.toString()}`);
  },
  getTranscript: (token: string, roomId: number) => request<AdminTranscript>(token, `/api/v1/admin/chats/${roomId}`),
};
