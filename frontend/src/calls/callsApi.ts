import { apiBaseUrl, apiRequest } from "../api/client";

export type CallType = "audio" | "video";
export type CallStatus = "ringing" | "accepted" | "active" | "rejected" | "cancelled" | "ended" | "missed";
export interface CallRecord { call_id: string; room_name: string; initiated_by: number; participant_ids: number[]; call_type: CallType; status: CallStatus; missed: boolean; created_at: string; updated_at: string; ended_at?: string | null; duration_seconds?: number; }
export interface JoinCallResponse { server_url: string; participant_token: string; call: CallRecord; }
export const callsApi = {
  start: (token: string, participantIds: number[], callType: CallType) => apiRequest<CallRecord>(token, "/api/v1/calls", { method: "POST", body: JSON.stringify({ participant_ids: participantIds, call_type: callType, idempotency_key: crypto.randomUUID() }) }),
  list: (token: string) => apiRequest<CallRecord[]>(token, "/api/v1/calls"),
  availability: (token: string, userIds: number[]) => apiRequest<Record<string, boolean>>(token, `/api/v1/calls/availability?user_ids=${userIds.join(",")}`),
  accept: (token: string, id: string) => apiRequest<CallRecord>(token, `/api/v1/calls/${id}/accept`, { method: "POST" }),
  reject: (token: string, id: string) => apiRequest<CallRecord>(token, `/api/v1/calls/${id}/reject`, { method: "POST" }),
  end: (token: string, id: string) => apiRequest<CallRecord>(token, `/api/v1/calls/${id}/end`, { method: "POST" }),
  join: (token: string, id: string) => apiRequest<JoinCallResponse>(token, `/api/v1/calls/${id}/join`, { method: "POST" }),
  websocketUrl: () => `${apiBaseUrl.replace(/^http/, "ws")}/api/v1/calls/ws`,
};
