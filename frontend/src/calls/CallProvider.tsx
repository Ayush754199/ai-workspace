import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Room } from "livekit-client";
import { useAuth } from "../contexts/AuthContext";
import { callsApi, CallRecord, CallType } from "./callsApi";
import LiveKitCallRoom from "./LiveKitCallRoom";

type CallContextValue = { incomingCall: CallRecord | null; activeCall: CallRecord | null; startCall: (participantIds: number[], type: CallType) => Promise<void>; acceptCall: (call: CallRecord) => Promise<void>; rejectCall: (call: CallRecord) => Promise<void>; endCall: () => Promise<void>; };
const CallContext = createContext<CallContextValue | null>(null);
const noProviderCalls: CallContextValue = { incomingCall: null, activeCall: null, startCall: async () => {}, acceptCall: async () => {}, rejectCall: async () => {}, endCall: async () => {} };
export const useCalls = () => useContext(CallContext) ?? noProviderCalls;

export const CallProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { token } = useAuth(); const [incomingCall, setIncomingCall] = useState<CallRecord | null>(null); const [activeCall, setActiveCall] = useState<CallRecord | null>(null); const roomRef = useRef<Room | null>(null); const endingRef = useRef(false);
  const disconnect = useCallback(async () => { roomRef.current?.disconnect(); roomRef.current = null; }, []);
  const join = useCallback(async (call: CallRecord) => { if (!token) throw new Error("Sign in to join a call"); const joined = await callsApi.join(token, call.call_id); const room = new Room({ adaptiveStream: true, dynacast: true }); await room.connect(joined.server_url, joined.participant_token, { autoSubscribe: true }); await room.localParticipant.setMicrophoneEnabled(true); if (call.call_type === "video") await room.localParticipant.setCameraEnabled(true, { resolution: { width: 1920, height: 1080 }, frameRate: 30 }); roomRef.current = room; setActiveCall({ ...call, status: "active" }); }, [token]);
  const acceptCall = useCallback(async (call: CallRecord) => { if (!token) return; const accepted = await callsApi.accept(token, call.call_id); setIncomingCall(null); await join(accepted); }, [join, token]);
  const startCall = useCallback(async (participantIds: number[], type: CallType) => { if (!token) throw new Error("Sign in to call"); const call = await callsApi.start(token, participantIds, type); try { await join(call); } catch (error) { await callsApi.cancel(token, call.call_id).catch(() => undefined); throw error; } }, [join, token]);
  const rejectCall = useCallback(async (call: CallRecord) => { if (token) await callsApi.reject(token, call.call_id); setIncomingCall(null); }, [token]);
  const endCall = useCallback(async () => { if (endingRef.current) return; endingRef.current = true; const callToEnd = activeCall; setActiveCall(null); try { if (token && callToEnd) await callsApi.end(token, callToEnd.call_id); } finally { await disconnect(); endingRef.current = false; } }, [activeCall, disconnect, token]);
  useEffect(() => { if (!token) return; const socket = new WebSocket(callsApi.websocketUrl(), [token]); socket.onmessage = (message) => { try { const event = JSON.parse(message.data); const eventCall = event.call as CallRecord | undefined; if (event.type === "call.incoming" && eventCall?.status === "ringing") setIncomingCall(eventCall); if (eventCall?.call_id === incomingCall?.call_id && ["call.cancelled", "call.rejected", "call.ended"].includes(event.type)) setIncomingCall(null); if (eventCall?.call_id === activeCall?.call_id && event.type === "call.ended") void endCall(); } catch { /* ignore malformed frames */ } }; return () => socket.close(); }, [activeCall?.call_id, endCall, incomingCall?.call_id, token]);
  useEffect(() => () => { void disconnect(); }, [disconnect]);
  return <CallContext.Provider value={{ incomingCall, activeCall, startCall, acceptCall, rejectCall, endCall }}>{children}{incomingCall && <IncomingCall call={incomingCall} onAccept={() => void acceptCall(incomingCall)} onReject={() => void rejectCall(incomingCall)} />}{activeCall && <LiveKitCallRoom call={activeCall} room={roomRef.current} token={token} onEnd={() => void endCall()} />}</CallContext.Provider>;
};

const IncomingCall: React.FC<{ call: CallRecord; onAccept: () => void; onReject: () => void }> = ({ call, onAccept, onReject }) => <div className="call-incoming" role="dialog" aria-label="Incoming call"><strong>Incoming {call.call_type} call</strong><span>Participants: {call.participant_ids.length}</span><div><button onClick={onAccept}>Accept</button><button onClick={onReject}>Decline</button></div></div>;
