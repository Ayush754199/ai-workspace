/**
 * Turns raw call failures into something a user can act on.
 *
 * The backend answers 503 with a developer-facing message ("LiveKit calling is
 * not configured on this server. Missing server variables: LIVEKIT_URL, ..."),
 * and the browser throws DOMExceptions when a mic/camera is blocked. Neither
 * belongs on screen as-is, and both look like the app is simply broken.
 */
export function describeCallError(error: unknown, fallback = "Could not complete the call"): string {
  const raw = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  const status = (error as { status?: number } | null)?.status;
  const text = raw.toLowerCase();

  // Browser media errors surface as DOMException with a well-known name.
  const name = error instanceof Error ? (error as Error & { name?: string }).name : "";
  if (name === "NotAllowedError" || text.includes("permission denied")) {
    return "Your browser blocked the microphone or camera. Allow access in the address bar, then try the call again.";
  }
  if (name === "NotFoundError" || text.includes("no device found") || text.includes("requested device not found")) {
    return "No microphone or camera was found. Connect one and try again.";
  }
  if (name === "NotReadableError" || text.includes("could not start video source")) {
    return "Your microphone or camera is being used by another app. Close it and try again.";
  }

  if (status === 503 || text.includes("not configured") || text.includes("livekit")) {
    return "Calling is not available right now because the real-time service is not configured. Ask the administrator to enable it.";
  }
  if (status === 409 || text.includes("busy")) {
    return "That person is already on another call.";
  }
  if (status === 401 || text.includes("sign in") || text.includes("unauthorized")) {
    return "Please sign in again to start a call.";
  }
  if (/failed to fetch|network|cannot connect|websocket/i.test(text)) {
    return "Could not reach the call service. Check your connection and try again.";
  }

  // Never show an empty or obviously machine-shaped message.
  return raw && raw.length < 200 && !/^\{|traceback|missing server variables/i.test(raw) ? raw : fallback;
}
