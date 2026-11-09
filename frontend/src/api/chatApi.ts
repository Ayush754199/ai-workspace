export type RoomMember = {
  id: number;
  name: string | null;
  email: string;
  role: string;
};

export type ChatRoom = {
  id: number;
  name: string;
  room_type: string;
  members: number[];
  member_details: RoomMember[];
  last_message_at: string | null;
  created_by: number;
  is_pinned: boolean;
  is_archived: boolean;
};

export type RoomMutation = { name?: string; is_pinned?: boolean; is_archived?: boolean };

export type ChatMessage = {
  id: number;
  room_id: number;
  sender_id: number;
  sender_name: string;
  sender_role: string;
  content: string;
  created_at: string | null;
};

import { apiBaseUrl } from "./client";

async function request<T>(token: string, path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options?.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || "Chat request failed") as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return data as T;
}

export const chatApi = {
  listRooms: (token: string) => request<ChatRoom[]>(token, "/api/v1/rooms"),
  listMessages: (token: string, roomId: number, beforeId?: number) =>
    request<ChatMessage[]>(token, `/api/v1/rooms/${roomId}/messages?limit=50${beforeId ? `&before_id=${beforeId}` : ""}`),
  sendMessage: (token: string, roomId: number, content: string) =>
    request<ChatMessage>(token, `/api/v1/rooms/${roomId}/messages`, { method: "POST", body: JSON.stringify({ content }) }),
  inviteMember: (token: string, roomId: number, identifier: string) =>
    request<{ message: string; user_id: number }>(token, `/api/v1/rooms/${roomId}/members`, { method: "POST", body: JSON.stringify({ identifier }) }),
  createRoom: (token: string, name: string) =>
    request<ChatRoom>(token, "/api/v1/rooms", { method: "POST", body: JSON.stringify({ name, room_type: "chat" }) }),
  updateRoom: (token: string, roomId: number, update: { name?: string; is_pinned?: boolean; is_archived?: boolean }) =>
    request<ChatRoom>(token, `/api/v1/rooms/${roomId}`, { method: "PATCH", body: JSON.stringify(update) }),
  deleteRoom: (token: string, roomId: number) =>
    request<{ deleted: boolean }>(token, `/api/v1/rooms/${roomId}`, { method: "DELETE" }),
};
