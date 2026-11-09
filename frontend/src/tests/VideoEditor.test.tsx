import React from "react";
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import VideoEditor from "../video/VideoEditor";
import { createProject, editorReducer } from "../video/editorState";
import { AuthContext } from "../contexts/AuthContext";
import { MemoryRouter } from "react-router-dom";

const authValue = {
  user: {
    id: "qa",
    email: "alex@vortex.studio",
    mobile_number: null,
    full_name: "Alex Reed",
    role: "user",
    is_active: true,
    is_admin: false,
  },
  token: "token",
  isLoading: false,
  login: async () => undefined,
  register: async () => undefined,
  logout: () => undefined,
};

describe("VideoEditor UI", () => {
  it("renders the reference layout: media bin, monitor, inspector, timeline, export and mixer", () => {
    const { container } = render(
      <MemoryRouter>
        <AuthContext.Provider value={authValue}>
          <VideoEditor />
        </AuthContext.Provider>
      </MemoryRouter>,
    );
    expect(container.querySelector(".vd-media-panel")).toBeTruthy();
    expect(container.querySelector(".vd-monitor")).toBeTruthy();
    expect(container.querySelector(".vd-inspector")).toBeTruthy();
    expect(container.querySelectorAll(".vd-track-row").length).toBe(7);
    expect(container.querySelectorAll(".vd-export-grid select").length).toBe(5);
    expect(container.querySelectorAll(".vd-fader").length).toBe(5);
    expect(container.textContent).toContain("Color Grade");
    expect(container.textContent).toContain("Export settings");
  });
});

describe("editor reduce", () => {
  it("adds media, appends clips, splits and ripple-deletes", () => {
    let state = createProject();
    state = editorReducer(state, {
      type: "addMedia",
      assets: [{ id: "a1", name: "clip.mp4", kind: "video", duration: 10, url: "blob:x", objectUrl: true, favorite: false }],
    });
    state = editorReducer(state, { type: "appendClip", assetId: "a1" });
    state = editorReducer(state, { type: "appendClip", assetId: "a1" });
    expect(state.clips).toHaveLength(2);
    // second clip lands on the next video track instead of overlapping
    expect(new Set(state.clips.map((c) => c.track)).size).toBe(2);

    state = editorReducer(state, { type: "addTitle", text: "Hello" });
    const title = state.clips.find((c) => c.track === "T1")!;
    expect(title.text).toBe("Hello");

    state = editorReducer(state, { type: "splitClip", id: title.id, at: title.start + 2 });
    expect(state.clips.filter((c) => c.track === "T1")).toHaveLength(2);

    const first = state.clips.find((c) => c.track === "T1")!;
    const before = state.clips.length;
    state = editorReducer(state, { type: "rippleDelete", id: first.id });
    expect(state.clips).toHaveLength(before - 1);
  });

  it("clamps zoom and playhead", () => {
    let state = createProject();
    state = editorReducer(state, { type: "setZoom", zoom: 99 });
    expect(state.zoom).toBe(5);
    state = editorReducer(state, { type: "setPlayhead", at: -4 });
    expect(state.playhead).toBe(0);
  });
});
