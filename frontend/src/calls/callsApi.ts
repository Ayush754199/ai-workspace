import { apiBaseUrl, apiRequest } from "../api/client";

export type CallType = "audio" | "video";
export type CallStatus = "ringing" | "accepted" | "active" | "rejected" | "cancelled" | "ended" | "missed";
export interface CallRecord { call_id: string; room_name: string; initiated_by: number; participant_ids: number[]; call_type: CallType; status: CallStatus; missed: boolean; created_at: string; updated_at: string; started_at?: string | null; ended_at?: string | null; duration_seconds?: number; }
export interface JoinCallResponse { server_url: string; participant_token: string; call: CallRecord; }
export interface CallMessage { message_id: string; call_id: string; sender_id: number; sender_name: string; content: string; created_at: string; }
export const callsApi = {
  start: (token: string, participantIds: number[], callType: CallType) => apiRequest<CallRecord>(token, "/api/v1/calls", { method: "POST", body: JSON.stringify({ participant_ids: participantIds, call_type: callType, idempotency_key: crypto.randomUUID() }) }),
  list: (token: string) => apiRequest<CallRecord[]>(token, "/api/v1/calls"),
  availability: (token: string, userIds: number[]) => apiRequest<Record<string, boolean>>(token, `/api/v1/calls/availability?user_ids=${userIds.join(",")}`),
  accept: (token: string, id: string) => apiRequest<CallRecord>(token, `/api/v1/calls/${id}/accept`, { method: "POST" }),
  reject: (token: string, id: string) => apiRequest<CallRecord>(token, `/api/v1/calls/${id}/reject`, { method: "POST" }),
  cancel: (token: string, id: string) => apiRequest<CallRecord>(token, `/api/v1/calls/${id}/cancel`, { method: "POST" }),
  end: (token: string, id: string) => apiRequest<CallRecord>(token, `/api/v1/calls/${id}/end`, { method: "POST" }),
  join: (token: string, id: string) => apiRequest<JoinCallResponse>(token, `/api/v1/calls/${id}/join`, { method: "POST" }),
  messages: (token: string, id: string) => apiRequest<CallMessage[]>(token, `/api/v1/calls/${id}/messages`),
  sendMessage: (token: string, id: string, content: string) => apiRequest<CallMessage>(token, `/api/v1/calls/${id}/messages`, { method: "POST", body: JSON.stringify({ content }) }),
  addParticipant: (token: string, id: string, participantId: number) => apiRequest<CallRecord>(token, `/api/v1/calls/${id}/participants`, { method: "POST", body: JSON.stringify({ participant_id: participantId }) }),
  // Use the query token instead of the WebSocket subprotocol. Some proxies
  // reject a non-empty Sec-WebSocket-Protocol header unless the upstream
  // explicitly echoes it during the handshake.
  websocketUrl: (token?: string) => `${apiBaseUrl.replace(/^http/, "ws")}/api/v1/calls/ws${token ? `?token=${encodeURIComponent(token)}` : ""}`,
};
