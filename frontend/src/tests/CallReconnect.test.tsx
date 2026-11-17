import React from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, act } from "@testing-library/react";
import { CallProvider, useCalls } from "../calls/CallProvider";
import { describeCallError } from "../calls/callErrors";

const callMocks = vi.hoisted(() => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
  setMicrophoneEnabled: vi.fn(),
  setCameraEnabled: vi.fn(),
  accept: vi.fn(),
  join: vi.fn(),
  websocketUrl: vi.fn(() => "ws://localhost/api/v1/calls/ws"),
}));
vi.mock("livekit-client", () => ({
  Room: class {
    connect = callMocks.connect;
    disconnect = callMocks.disconnect;
    localParticipant = {
      setMicrophoneEnabled: callMocks.setMicrophoneEnabled,
      setCameraEnabled: callMocks.setCameraEnabled,
    };
  },
}));
vi.mock("@livekit/components-react", () => ({ ControlBar: () => null, ParticipantTile: () => null, RoomAudioRenderer: () => null, RoomContext: ({ children }: { children: React.ReactNode }) => <>{children}</>, useTracks: () => [] }));
vi.mock("@livekit/components-styles", () => ({}));
vi.mock("../contexts/AuthContext", () => ({ useAuth: () => ({ token: "jwt-token", user: { id: 1 } }) }));
vi.mock("../calls/callsApi", () => ({
  callsApi: {
    accept: callMocks.accept,
    join: callMocks.join,
    websocketUrl: callMocks.websocketUrl,
  },
}));
vi.mock("../calls/LiveKitCallRoom", () => ({
  default: ({ call }: { call: { call_id: string } }) =>
    React.createElement("div", { "data-testid": "active-call-room" }, call.call_id),
}));

class FakeSocket {
  static instances: FakeSocket[] = [];
  static OPEN = 1;
  static CLOSED = 3;
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(public url: string) { FakeSocket.instances.push(this); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.(); }
  drop() { this.readyState = 3; this.onclose?.(); }
  deliver(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }); }
}

// Losing the signalling socket used to be silent and permanent: the user simply
// never received another call until a full page reload.
describe("CallProvider signalling socket", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
    vi.useFakeTimers();
  });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("reconnects after the socket closes", () => {
    render(<CallProvider><div /></CallProvider>);
    expect(FakeSocket.instances).toHaveLength(1);

    act(() => { FakeSocket.instances[0].drop(); });
    act(() => { vi.advanceTimersByTime(1_000); });

    expect(FakeSocket.instances.length).toBe(2);
    expect(FakeSocket.instances[1].url).toContain("/api/v1/calls/ws");
  });

  it("backs off between failed retries instead of hammering the server", () => {
    render(<CallProvider><div /></CallProvider>);
    act(() => { FakeSocket.instances[0].drop(); });
    act(() => { vi.advanceTimersByTime(1_000); });
    expect(FakeSocket.instances).toHaveLength(2);

    // Second consecutive failure waits 2s (1s * 2^1), not another 1s.
    act(() => { FakeSocket.instances[1].drop(); });
    act(() => { vi.advanceTimersByTime(1_900); });
    expect(FakeSocket.instances).toHaveLength(2); // 2s delay not elapsed yet
    act(() => { vi.advanceTimersByTime(200); });
    expect(FakeSocket.instances).toHaveLength(3);
  });

  it("reconnects when the browser comes back online", () => {
    render(<CallProvider><div /></CallProvider>);
    act(() => { FakeSocket.instances[0].drop(); });
    act(() => { window.dispatchEvent(new Event("online")); });
    expect(FakeSocket.instances.length).toBe(2);
  });

  it("does not open a second socket once connected", () => {
    render(<CallProvider><div /></CallProvider>);
    act(() => { FakeSocket.instances[0].open(); });
    act(() => { window.dispatchEvent(new Event("online")); });
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("shows an incoming call that arrives on the reconnected socket", () => {
    render(<CallProvider><div /></CallProvider>);
    act(() => { FakeSocket.instances[0].drop(); });
    act(() => { vi.advanceTimersByTime(1_000); });
    act(() => {
      FakeSocket.instances[1].deliver({
        type: "call.incoming",
        call: { call_id: "c1", room_name: "r1", initiated_by: 2, participant_ids: [1, 2], call_type: "audio", status: "ringing", missed: false, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
      });
    });
    expect(screen.getByLabelText(/incoming call/i)).toBeTruthy();
  });

  it("ignores malformed frames without dropping the socket", () => {
    render(<CallProvider><div /></CallProvider>);
    act(() => { FakeSocket.instances[0].onmessage?.({ data: "not json" }); });
    expect(FakeSocket.instances[0].readyState).not.toBe(3);
  });
});

describe("describeCallError", () => {
  it("hides raw LiveKit configuration details", () => {
    const error = new Error("LiveKit calling is not configured on this server. Missing server variables: LIVEKIT_URL, LIVEKIT_API_KEY") as Error & { status?: number };
    error.status = 503;
    const message = describeCallError(error);
    expect(message).not.toContain("LIVEKIT_API_KEY");
    expect(message).toMatch(/not available right now/i);
  });

  it("explains blocked microphone access", () => {
    const error = new Error("Permission denied");
    error.name = "NotAllowedError";
    expect(describeCallError(error)).toMatch(/microphone or camera/i);
  });

  it("explains a busy participant", () => {
    const error = new Error("Participant is busy in another call") as Error & { status?: number };
    error.status = 409;
    expect(describeCallError(error)).toMatch(/already on another call/i);
  });

  it("falls back to a plain message for unknown errors", () => {
    expect(describeCallError(new Error("Traceback (most recent call last)"), "Could not start the call")).toBe("Could not start the call");
  });
});

describe("CallProvider media permission fallback", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    callMocks.connect.mockResolvedValue(undefined);
    callMocks.setMicrophoneEnabled.mockRejectedValue(
      Object.assign(new Error("Permission denied"), { name: "NotAllowedError" }),
    );
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("keeps a connected call visible when microphone permission is denied", async () => {
    const call = {
      call_id: "c1",
      room_name: "r1",
      initiated_by: 2,
      participant_ids: [1, 2],
      call_type: "audio" as const,
      status: "accepted" as const,
      missed: false,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };
    callMocks.accept.mockResolvedValue(call);
    callMocks.join.mockResolvedValue({
      call,
      server_url: "wss://calls.example.test",
      participant_token: "participant-token",
    });

    const Actions = () => {
      const { acceptCall } = useCalls();
      return <button onClick={() => void acceptCall(call)}>Accept call</button>;
    };
    render(
      <CallProvider>
        <Actions />
      </CallProvider>,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Accept call" }));
    });

    expect(await screen.findByTestId("active-call-room")).toHaveTextContent("c1");
    expect(screen.getByRole("alert")).toHaveTextContent(/allow access/i);
    expect(callMocks.disconnect).not.toHaveBeenCalled();
  });
});
