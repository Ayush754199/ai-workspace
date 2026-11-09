import React, { useEffect, useRef, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { voiceApi } from "../api/workspaceApi";

/**
 * Microphone -> text (speech-to-text) and "read aloud" (text-to-speech).
 * Both call the backend; when the server has no STT/TTS provider the error is
 * shown as-is instead of pretending the feature works.
 */
const VoiceControls: React.FC<{ onTranscript: (text: string) => void; textToRead?: string }> = ({ onTranscript, textToRead }) => {
  const { token } = useAuth();
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [available, setAvailable] = useState<{ stt: boolean; tts: boolean } | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    if (!token) return;
    voiceApi
      .status(token)
      .then((data) =>
        setAvailable({
          stt: Boolean(data.stt.groq || data.stt.gemini),
          tts: Boolean(data.tts.edge_tts || data.tts.system_say || data.tts.gemini),
        }),
      )
      .catch(() => setAvailable({ stt: false, tts: false }));
  }, [token]);

  useEffect(() => {
    if (!status) return;
    const id = window.setTimeout(() => setStatus(""), 4000);
    return () => window.clearTimeout(id);
  }, [status]);

  const startRecording = async () => {
    if (!token) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "" });
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        if (!blob.size) return;
        setBusy(true);
        setStatus("Transcribing…");
        try {
          const result = await voiceApi.transcribe(token, blob);
          if (result.text) {
            onTranscript(result.text.trim());
            setStatus(`Transcribed with ${result.engine}`);
          } else setStatus("No speech detected");
        } catch (error: any) {
          setStatus(error?.message || "Transcription failed");
        } finally {
          setBusy(false);
        }
      };
      recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
      setStatus("Recording… press stop to transcribe");
    } catch {
      setStatus("Microphone permission was denied");
    }
  };

  const stopRecording = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  };

  const speak = async () => {
    if (!token || !textToRead) return;
    setBusy(true);
    setStatus("Generating speech…");
    try {
      const blob = await voiceApi.speak(token, textToRead.slice(0, 1500));
      if (audioRef.current) audioRef.current.pause();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
      setStatus("");
    } catch (error: any) {
      setStatus(error?.message || "Speech synthesis unavailable");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="voice-controls">
      <button
        type="button"
        className={`cw-icon ${recording ? "recording" : ""}`}
        title={recording ? "Stop recording" : available?.stt === false ? "Speech-to-text is not configured on the server" : "Dictate with your voice"}
        onClick={recording ? stopRecording : startRecording}
        disabled={busy || available?.stt === false}
      >
        {recording ? "■" : "🎙"}
      </button>
      {textToRead !== undefined && (
        <button
          type="button"
          className="cw-icon"
          title={available?.tts === false ? "Text-to-speech is not configured on the server" : "Read aloud"}
          onClick={speak}
          disabled={busy || !textToRead || available?.tts === false}
        >
          🔊
        </button>
      )}
      {status && <span className="voice-status">{status}</span>}
    </div>
  );
};

export default VoiceControls;
