import React, { useEffect, useMemo, useState } from "react";
import { ControlBar, ParticipantTile, RoomAudioRenderer, RoomContext, useTracks } from "@livekit/components-react";
import { Room, RoomEvent, Track } from "livekit-client";
import "@livekit/components-styles";
import { CallMessage, CallRecord, callsApi } from "./callsApi";

type Props = { call: CallRecord; room: Room | null; token: string | null; onEnd: () => void };

const formatDuration = (seconds: number) => `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;

const CallVisualStage: React.FC<{ callType: CallRecord["call_type"]; room: Room; }> = ({ callType, room }) => {
  const cameras = useTracks([{ source: Track.Source.Camera, withPlaceholder: true }], { onlySubscribed: false });
  const screens = useTracks([Track.Source.ScreenShare], { onlySubscribed: false });
  const [focusedIdentity, setFocusedIdentity] = useState<string | null>(null);
  const localIdentity = room.localParticipant.identity;
  const visualTracks = [...screens, ...cameras];
  const remoteTracks = visualTracks.filter((track) => track.participant.identity !== localIdentity);
  const localTrack = visualTracks.find((track) => track.participant.identity === localIdentity);
  const defaultTrack = remoteTracks[0] || localTrack || visualTracks[0];
  const focusedTrack = focusedIdentity
    ? visualTracks.find((track) => track.participant.identity === focusedIdentity) || defaultTrack
    : defaultTrack;
  const thumbnailTracks = visualTracks.filter((track) => track !== focusedTrack && track.participant.identity !== focusedTrack?.participant.identity)
    .filter((track, index, all) => all.findIndex((candidate) => candidate.participant.identity === track.participant.identity) === index);
  const focusNext = () => {
    const next = visualTracks.find((track) => track.participant.identity !== focusedTrack?.participant.identity);
    setFocusedIdentity(next?.participant.identity || null);
  };

  return <div className={`call-visual-layout ${callType === "audio" ? "audio-only" : ""}`}>
    <div className="call-stage-tile" onDoubleClick={focusNext} title="Double-tap to switch focus">
      {focusedTrack ? <ParticipantTile trackRef={focusedTrack} /> : <div className="call-no-video">Waiting for video…</div>}
      {focusedTrack && <span className="call-stage-name">{focusedTrack.participant.name || focusedTrack.participant.identity}{focusedTrack.participant.identity === localIdentity ? " · You" : ""}</span>}
    </div>
    {thumbnailTracks.length > 0 && <div className="call-thumbnail-strip">{thumbnailTracks.map((track) => <button className="call-thumbnail" key={`${track.participant.identity}-${track.source}`} onDoubleClick={() => setFocusedIdentity(track.participant.identity)} onClick={() => setFocusedIdentity(track.participant.identity)} title="Make this video large"><ParticipantTile trackRef={track} /><span>{track.participant.name || track.participant.identity}{track.participant.identity === localIdentity ? " · You" : ""}</span></button>)}</div>}
  </div>;
};

const LiveKitCallRoom: React.FC<Props> = ({ call, room, token, onEnd }) => {
  const [connected, setConnected] = useState(room?.state === "connected");
  const [seconds, setSeconds] = useState(0);
  const [messages, setMessages] = useState<CallMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [chatOpen, setChatOpen] = useState(false);
  const [messageError, setMessageError] = useState("");
  const startedAt = useMemo(() => Date.parse(call.started_at || call.created_at), [call.created_at, call.started_at]);

  useEffect(() => {
    if (!room) return;
    const onConnected = () => setConnected(true);
    const onReconnecting = () => setConnected(false);
    const onReconnected = () => setConnected(true);
    const onDisconnected = () => { setConnected(false); onEnd(); };
    room.on(RoomEvent.Connected, onConnected).on(RoomEvent.Reconnecting, onReconnecting).on(RoomEvent.Reconnected, onReconnected).on(RoomEvent.Disconnected, onDisconnected);
    return () => { room.off(RoomEvent.Connected, onConnected).off(RoomEvent.Reconnecting, onReconnecting).off(RoomEvent.Reconnected, onReconnected).off(RoomEvent.Disconnected, onDisconnected); };
  }, [onEnd, room]);

  useEffect(() => {
    const timer = window.setInterval(() => setSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000))), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  useEffect(() => {
    if (!token) return;
    callsApi.messages(token, call.call_id).then(setMessages).catch(() => undefined);
    const socket = new WebSocket(callsApi.websocketUrl(), [token]);
    socket.onmessage = (event) => { try { const payload = JSON.parse(event.data); if (payload.type === "call.message" && payload.message?.call_id === call.call_id) setMessages((current) => current.some((item) => item.message_id === payload.message.message_id) ? current : [...current, payload.message]); } catch { /* ignore malformed signalling frames */ } };
    return () => socket.close();
  }, [call.call_id, token]);

  const sendMessage = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!token || !draft.trim()) return;
    try { const message = await callsApi.sendMessage(token, call.call_id, draft.trim()); setMessages((current) => current.some((item) => item.message_id === message.message_id) ? current : [...current, message]); setDraft(""); setMessageError(""); }
    catch (error) { setMessageError(error instanceof Error ? error.message : "Message could not be sent"); }
  };

  if (!room) return null;
  return <div className={`call-room call-room-live ${chatOpen ? "with-chat" : ""}`} role="dialog" aria-label="Active call">
    <div className="call-room-title"><span>{call.call_type === "video" ? "Video call" : "Audio call"} · {formatDuration(seconds)}</span><span className={connected ? "call-connection-good" : "call-connection-warn"}>{connected ? `${room.remoteParticipants.size + 1} connected` : "Reconnecting…"}</span></div>
    <div className="call-room-livekit"><RoomContext.Provider value={room}><RoomAudioRenderer room={room} /><CallVisualStage callType={call.call_type} room={room} /><div className="call-livekit-controls"><ControlBar controls={{ microphone: true, camera: call.call_type === "video", screenShare: true, chat: false, leave: false, settings: true }} /></div></RoomContext.Provider></div>
    {chatOpen && <aside className="call-chat-panel"><div className="call-chat-head"><strong>In-call chat</strong><button onClick={() => setChatOpen(false)}>×</button></div><div className="call-chat-messages">{messages.map((message) => <div className="call-chat-message" key={message.message_id}><strong>{message.sender_name}</strong><span>{message.content}</span><small>{new Date(message.created_at).toLocaleTimeString()}</small></div>)}</div><form onSubmit={sendMessage} className="call-chat-compose"><input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Message participants" /><button disabled={!draft.trim()}>Send</button></form>{messageError && <small className="error-text">{messageError}</small>}</aside>}
    <div className="call-controls"><button onClick={() => setChatOpen((open) => !open)}>Chat{messages.length ? ` (${messages.length})` : ""}</button><button className="call-end" onClick={onEnd}>End call</button></div>
  </div>;
};

export default LiveKitCallRoom;
