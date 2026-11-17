import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { Room } from "livekit-client";
import { useAuth } from "../contexts/AuthContext";
import { callsApi, CallRecord, CallType } from "./callsApi";
import LiveKitCallRoom from "./LiveKitCallRoom";
import { CallIcon } from "../components/CallIcon";
import { describeCallError } from "./callErrors";

type CallContextValue = {
  incomingCall: CallRecord | null;
  outgoingCall: CallRecord | null;
  activeCall: CallRecord | null;
  startCall: (participantIds: number[], type: CallType) => Promise<void>;
  acceptCall: (call: CallRecord) => Promise<void>;
  rejectCall: (call: CallRecord) => Promise<void>;
  endCall: () => Promise<void>;
  cancelOutgoingCall: () => Promise<void>;
};
const CallContext = createContext<CallContextValue | null>(null);
const noProviderCalls: CallContextValue = {
  incomingCall: null,
  outgoingCall: null,
  activeCall: null,
  startCall: async () => {},
  acceptCall: async () => {},
  rejectCall: async () => {},
  endCall: async () => {},
  cancelOutgoingCall: async () => {},
};
export const useCalls = () => useContext(CallContext) ?? noProviderCalls;

export const CallProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { token } = useAuth();
  const [callError, setCallError] = useState("");
  const [incomingCall, setIncomingCall] = useState<CallRecord | null>(null);
  const [outgoingCall, setOutgoingCall] = useState<CallRecord | null>(null);
  const [activeCall, setActiveCall] = useState<CallRecord | null>(null);
  const [connectError, setConnectError] = useState("");
  const roomRef = useRef<Room | null>(null);
  const endingRef = useRef(false);
  const outgoingRef = useRef<CallRecord | null>(null);
  const activeRef = useRef<CallRecord | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const wsClosedRef = useRef(false);
  const wsTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const reconnectAttemptsRef = useRef(0);

  const disconnect = useCallback(async () => {
    wsClosedRef.current = true;
    if (wsTimerRef.current) window.clearTimeout(wsTimerRef.current);
    wsRef.current?.close();
    wsRef.current = null;
    roomRef.current?.disconnect();
    roomRef.current = null;
  }, []);

  const join = useCallback(
    async (call: CallRecord) => {
      if (!token) throw new Error("Sign in to join a call");
      const joined = await callsApi.join(token, call.call_id);
      const room = new Room({ adaptiveStream: true, dynacast: true });
      try {
        // Hard timeout: LiveKit must connect within 20s or we surface an error
        // instead of leaving the user staring at a blank / white room.
        const connectPromise = room.connect(joined.server_url, joined.participant_token, {
          autoSubscribe: true,
        });
        let timeoutId: number | undefined;
        try {
          const timeoutPromise = new Promise<never>((_, reject) => {
            timeoutId = window.setTimeout(() => reject(new Error("call_connect_timeout")), 20_000);
          });
          await Promise.race([connectPromise, timeoutPromise]);
        } finally {
          if (timeoutId !== undefined) window.clearTimeout(timeoutId);
        }
      } catch (error) {
        room.disconnect();
        setConnectError(
          error instanceof Error && error.message === "call_connect_timeout"
            ? "The call is taking too long to connect. Check your internet and try again."
            : describeCallError(error, "Could not connect the call")
        );
        throw error;
      }

      // Mount the connected room before requesting devices. Permission denial
      // must not discard a successfully connected call or leave the page blank.
      roomRef.current = room;
      setOutgoingCall(null);
      setActiveCall({ ...joined.call, status: "active" });
      setConnectError("");

      const mediaErrors: string[] = [];
      try {
        await room.localParticipant.setMicrophoneEnabled(true);
      } catch (error) {
        mediaErrors.push(describeCallError(error, "Could not enable your microphone"));
      }
      if (call.call_type === "video") {
        try {
          await room.localParticipant.setCameraEnabled(true, {
            resolution: { width: 1280, height: 720 },
            frameRate: 24,
          });
        } catch (error) {
          mediaErrors.push(describeCallError(error, "Could not enable your camera"));
        }
      }
      if (mediaErrors.length) setConnectError(mediaErrors.join(" "));
    },
    [token]
  );

  const acceptCall = useCallback(
    async (call: CallRecord) => {
      if (!token) return;
      setCallError("");
      setConnectError("");
      try {
        const accepted = await callsApi.accept(token, call.call_id);
        setIncomingCall(null);
        await join(accepted);
      } catch (reason) {
        setIncomingCall(null);
        setCallError(describeCallError(reason, "Could not join the call"));
      }
    },
    [join, token]
  );

  const startCall = useCallback(
    async (participantIds: number[], type: CallType) => {
      if (!token) throw new Error("Sign in to call");
      setCallError("");
      setConnectError("");
      const call = await callsApi.start(token, participantIds, type);
      setOutgoingCall(call);
    },
    [token]
  );

  const cancelOutgoingCall = useCallback(async () => {
    const call = outgoingRef.current;
    setOutgoingCall(null);
    if (token && call) await callsApi.cancel(token, call.call_id).catch(() => undefined);
  }, [token]);

  const rejectCall = useCallback(async (call: CallRecord) => {
    if (token) await callsApi.reject(token, call.call_id);
    setIncomingCall(null);
  }, [token]);

  const endCall = useCallback(async () => {
    if (endingRef.current) return;
    endingRef.current = true;
    const callToEnd = activeRef.current;
    setActiveCall(null);
    setConnectError("");
    try {
      if (token && callToEnd) await callsApi.end(token, callToEnd.call_id).catch(() => undefined);
    } finally {
      await disconnect();
      endingRef.current = false;
    }
  }, [disconnect, token]);

  /** One signalling frame; shared by initial connect + every reconnect. */
  const handleSignal = useCallback(
    (event: { type?: string; call?: CallRecord }) => {
      const eventCall = event.call;
      if (!eventCall) return;
      if (event.type === "call.incoming" && eventCall.status === "ringing")
        setIncomingCall(eventCall);
      if (
        eventCall.call_id === incomingCall?.call_id &&
        ["call.cancelled", "call.rejected", "call.ended"].includes(String(event.type))
      )
        setIncomingCall(null);
      if (eventCall.call_id === outgoingRef.current?.call_id) {
        if (event.type === "call.accepted")
          join(eventCall).catch((reason) => {
            setOutgoingCall(null);
            setCallError(describeCallError(reason, "Could not connect the call"));
          });
        if (["call.cancelled", "call.rejected", "call.ended"].includes(String(event.type)))
          setOutgoingCall(null);
      }
      if (
        eventCall.call_id === activeRef.current?.call_id &&
        ["call.ended", "call.rejected", "call.cancelled"].includes(String(event.type))
      )
        void endCall();
    },
    [endCall, incomingCall?.call_id, join]
  );

  useEffect(() => {
    if (!token) return;
    let closed = false;
    const connect = () => {
      if (closed || wsClosedRef.current) return;
      if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
      wsRef.current = new WebSocket(callsApi.websocketUrl(token));
      wsRef.current.onopen = () => {
        reconnectAttemptsRef.current = 0;
      };
      wsRef.current.onclose = () => {
        if (closed || wsClosedRef.current) return;
        const delay = Math.min(30_000, 1_000 * 2 ** reconnectAttemptsRef.current);
        reconnectAttemptsRef.current += 1;
        wsTimerRef.current = window.setTimeout(connect, delay);
      };
      wsRef.current.onerror = () => {
        wsRef.current?.close();
      };
      wsRef.current.onmessage = (message) => {
        try {
          handleSignal(JSON.parse(String(message.data)) as { type?: string; call?: CallRecord });
        } catch {
          /* ignore malformed frames */
        }
      };
    };
    const wake = () => {
      if (closed || wsClosedRef.current || wsRef.current?.readyState === WebSocket.OPEN) return;
      window.clearTimeout(wsTimerRef.current);
      connect();
    };
    connect();
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      closed = true;
      wsClosedRef.current = true;
      window.clearTimeout(wsTimerRef.current);
      window.removeEventListener("online", wake);
      document.removeEventListener("visibilitychange", wake);
      wsRef.current?.close();
    };
  }, [handleSignal, token]);

  useEffect(() => {
    if (!outgoingCall) return;
    const timer = window.setTimeout(() => {
      void cancelOutgoingCall();
    }, 45_000);
    return () => window.clearTimeout(timer);
  }, [cancelOutgoingCall, outgoingCall]);

  useEffect(() => () => {
    void disconnect();
  }, [disconnect]);

  return (
    <CallContext.Provider
      value={{
        incomingCall,
        outgoingCall,
        activeCall,
        startCall,
        acceptCall,
        rejectCall,
        endCall,
        cancelOutgoingCall,
      }}
    >
      {children}
      {callError && (
        <div className="call-error-banner" role="alert">
          <span>{callError}</span>
          <button onClick={() => setCallError("")} aria-label="Dismiss call error">
            ✕
          </button>
        </div>
      )}
      {connectError && (
        <div className="call-error-banner" role="alert">
          <span>{connectError}</span>
          <button onClick={() => setConnectError("")} aria-label="Dismiss connect error">✕</button>
        </div>
      )}
      {incomingCall && (
        <IncomingCall call={incomingCall} onAccept={() => void acceptCall(incomingCall)} onReject={() => void rejectCall(incomingCall)} />
      )}
      {outgoingCall && <OutgoingCall call={outgoingCall} onCancel={() => void cancelOutgoingCall()} />}
      {activeCall && (
        roomRef.current ? (
          <LiveKitCallRoom call={activeCall} room={roomRef.current} token={token} onEnd={() => void endCall()} />
        ) : (
          <div className="call-room call-connecting" role="dialog" aria-label="Connecting call">
            <strong>{activeCall.call_type === "video" ? "Video call" : "Voice call"}</strong>
            <span>Connecting to call…</span>
          </div>
        )
      )}
    </CallContext.Provider>
  );
};

const IncomingCall: React.FC<{
  call: CallRecord;
  onAccept: () => void;
  onReject: () => void;
}> = ({ call, onAccept, onReject }) => (
  <div className="call-incoming" role="dialog" aria-label="Incoming call">
    <div className="call-incoming-icon"><CallIcon type={call.call_type} size={28} /></div>
    <strong>Incoming {call.call_type} call</strong>
    <span>Participants: {call.participant_ids.length}</span>
    <div>
      <button onClick={onAccept}>Accept</button>
      <button onClick={onReject}>Decline</button>
    </div>
  </div>
);

const OutgoingCall: React.FC<{ call: CallRecord; onCancel: () => void }> = ({
  call,
  onCancel,
}) => (
  <div className="call-room call-outgoing" role="dialog" aria-label="Outgoing call">
    <div className="call-outgoing-icon"><CallIcon type={call.call_type} size={30} /></div>
    <strong>{call.call_type === "video" ? "Video calling" : "Voice calling"}</strong>
    <span>Calling…</span>
    <span className="call-ringing">Ringing</span>
    <button className="call-end" onClick={onCancel}>
      Cancel call
    </button>
  </div>
);
