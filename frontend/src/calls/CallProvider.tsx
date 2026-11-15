import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Room } from "livekit-client";
import { useAuth } from "../contexts/AuthContext";
import { callsApi, CallRecord, CallType } from "./callsApi";
import LiveKitCallRoom from "./LiveKitCallRoom";
import { CallIcon } from "../components/CallIcon";
import { describeCallError } from "./callErrors";

type CallContextValue = { incomingCall: CallRecord | null; outgoingCall: CallRecord | null; activeCall: CallRecord | null; startCall: (participantIds: number[], type: CallType) => Promise<void>; acceptCall: (call: CallRecord) => Promise<void>; rejectCall: (call: CallRecord) => Promise<void>; endCall: () => Promise<void>; cancelOutgoingCall: () => Promise<void>; };
const CallContext = createContext<CallContextValue | null>(null);
const noProviderCalls: CallContextValue = { incomingCall: null, outgoingCall: null, activeCall: null, startCall: async () => {}, acceptCall: async () => {}, rejectCall: async () => {}, endCall: async () => {}, cancelOutgoingCall: async () => {} };
export const useCalls = () => useContext(CallContext) ?? noProviderCalls;

export const CallProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { token } = useAuth(); const [callError, setCallError] = useState(""); const [incomingCall, setIncomingCall] = useState<CallRecord | null>(null); const [outgoingCall, setOutgoingCall] = useState<CallRecord | null>(null); const [activeCall, setActiveCall] = useState<CallRecord | null>(null); const roomRef = useRef<Room | null>(null); const endingRef = useRef(false); const outgoingRef = useRef<CallRecord | null>(null); const activeRef = useRef<CallRecord | null>(null);
  useEffect(() => { outgoingRef.current = outgoingCall; }, [outgoingCall]);
  useEffect(() => { activeRef.current = activeCall; }, [activeCall]);
  const disconnect = useCallback(async () => { roomRef.current?.disconnect(); roomRef.current = null; }, []);
  const join = useCallback(async (call: CallRecord) => { if (!token) throw new Error("Sign in to join a call"); const joined = await callsApi.join(token, call.call_id); const room = new Room({ adaptiveStream: true, dynacast: true }); try { await room.connect(joined.server_url, joined.participant_token, { autoSubscribe: true }); await room.localParticipant.setMicrophoneEnabled(true); if (call.call_type === "video") await room.localParticipant.setCameraEnabled(true, { resolution: { width: 1920, height: 1080 }, frameRate: 30 }); roomRef.current = room; setOutgoingCall(null); setActiveCall({ ...joined.call, status: "active" }); } catch (error) { room.disconnect(); throw error; } }, [token]);
  const acceptCall = useCallback(async (call: CallRecord) => { if (!token) return; setCallError(""); try { const accepted = await callsApi.accept(token, call.call_id); setIncomingCall(null); await join(accepted); } catch (reason) { setIncomingCall(null); setCallError(describeCallError(reason, "Could not join the call")); } }, [join, token]);
  const startCall = useCallback(async (participantIds: number[], type: CallType) => { if (!token) throw new Error("Sign in to call"); setCallError(""); const call = await callsApi.start(token, participantIds, type); setOutgoingCall(call); }, [token]);
  const cancelOutgoingCall = useCallback(async () => { const call = outgoingRef.current; setOutgoingCall(null); if (token && call) await callsApi.cancel(token, call.call_id).catch(() => undefined); }, [token]);
  const rejectCall = useCallback(async (call: CallRecord) => { if (token) await callsApi.reject(token, call.call_id); setIncomingCall(null); }, [token]);
  const endCall = useCallback(async () => { if (endingRef.current) return; endingRef.current = true; const callToEnd = activeRef.current; setActiveCall(null); try { if (token && callToEnd) await callsApi.end(token, callToEnd.call_id).catch(() => undefined); } finally { await disconnect(); endingRef.current = false; } }, [disconnect, token]);
  /** Applies one signalling frame; shared by the initial connect and every reconnect. */
  const handleSignal = useCallback((event: { type?: string; call?: CallRecord }) => {
    const eventCall = event.call;
    if (!eventCall) return;
    if (event.type === "call.incoming" && eventCall.status === "ringing") setIncomingCall(eventCall);
    if (eventCall.call_id === incomingCall?.call_id && ["call.cancelled", "call.rejected", "call.ended"].includes(String(event.type))) setIncomingCall(null);
    if (eventCall.call_id === outgoingRef.current?.call_id) {
      // A failed join used to vanish into the void, leaving the caller stuck on
      // "Ringing" forever with no explanation.
      if (event.type === "call.accepted") join(eventCall).catch((reason) => { setOutgoingCall(null); setCallError(describeCallError(reason, "Could not connect the call")); });
      if (["call.cancelled", "call.rejected", "call.ended"].includes(String(event.type))) setOutgoingCall(null);
    }
    if (eventCall.call_id === activeRef.current?.call_id && ["call.ended", "call.rejected", "call.cancelled"].includes(String(event.type))) void endCall();
  }, [endCall, incomingCall?.call_id, join]);
  // The signalling socket carries every incoming call, so losing it silently is
  // the worst failure mode: the user simply never rings again. Reconnect with
  // backoff, and reconnect immediately when the tab or network comes back.
  useEffect(() => {
    if (!token) return;
    let socket: WebSocket | null = null; let retry = 0; let timer: number | undefined; let closed = false;
    const connect = () => {
      if (closed) return;
      // A wake event and a pending retry can race; never leave two sockets open,
      // otherwise one user shows up twice in the call hub.
      if (socket && socket.readyState !== WebSocket.CLOSED) {
        socket.onclose = null;
        socket.close();
      }
      socket = new WebSocket(callsApi.websocketUrl(token));
      socket.onopen = () => { retry = 0; };
      socket.onclose = () => {
        if (closed) return;
        const delay = Math.min(30_000, 1_000 * 2 ** retry);
        retry += 1;
        timer = window.setTimeout(connect, delay);
      };
      socket.onerror = () => { socket?.close(); };
      socket.onmessage = (message) => { try { handleSignal(JSON.parse(String(message.data)) as { type?: string; call?: CallRecord }); } catch { /* ignore malformed frames */ } };
    };
    const wake = () => { if (closed || socket?.readyState === WebSocket.OPEN) return; window.clearTimeout(timer); connect(); };
    connect();
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      closed = true;
      window.clearTimeout(timer);
      window.removeEventListener("online", wake);
      document.removeEventListener("visibilitychange", wake);
      socket?.close();
    };
  }, [handleSignal, token]);
  useEffect(() => { if (!outgoingCall) return; const timer = window.setTimeout(() => { void cancelOutgoingCall(); }, 45_000); return () => window.clearTimeout(timer); }, [cancelOutgoingCall, outgoingCall]);
  useEffect(() => () => { void disconnect(); }, [disconnect]);
  return <CallContext.Provider value={{ incomingCall, outgoingCall, activeCall, startCall, acceptCall, rejectCall, endCall, cancelOutgoingCall }}>{children}{callError && <div className="call-error-banner" role="alert"><span>{callError}</span><button onClick={() => setCallError("")} aria-label="Dismiss call error">✕</button></div>}{incomingCall && <IncomingCall call={incomingCall} onAccept={() => void acceptCall(incomingCall)} onReject={() => void rejectCall(incomingCall)} />}{outgoingCall && <OutgoingCall call={outgoingCall} onCancel={() => void cancelOutgoingCall()} />}{activeCall && (roomRef.current ? <LiveKitCallRoom call={activeCall} room={roomRef.current} token={token} onEnd={() => void endCall()} /> : <div className="call-room call-connecting" role="dialog" aria-label="Connecting call"><strong>{activeCall.call_type === "video" ? "Video call" : "Voice call"}</strong><span>Connecting to call…</span></div>)}</CallContext.Provider>;
};

const IncomingCall: React.FC<{ call: CallRecord; onAccept: () => void; onReject: () => void }> = ({ call, onAccept, onReject }) => <div className="call-incoming" role="dialog" aria-label="Incoming call"><div className="call-incoming-icon"><CallIcon type={call.call_type} size={28} /></div><strong>Incoming {call.call_type} call</strong><span>Participants: {call.participant_ids.length}</span><div><button onClick={onAccept}>Accept</button><button onClick={onReject}>Decline</button></div></div>;

const OutgoingCall: React.FC<{ call: CallRecord; onCancel: () => void }> = ({ call, onCancel }) => <div className="call-room call-outgoing" role="dialog" aria-label="Outgoing call"><div className="call-outgoing-icon"><CallIcon type={call.call_type} size={30} /></div><strong>{call.call_type === "video" ? "Video calling" : "Voice calling"}</strong><span>Calling…</span><span className="call-ringing">Ringing</span><button className="call-end" onClick={onCancel}>Cancel call</button></div>;
