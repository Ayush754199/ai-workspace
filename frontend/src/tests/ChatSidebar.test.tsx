import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ChatWorkspace from "../pages/ChatWorkspace";
import { AuthContext } from "../contexts/AuthContext";

vi.mock("../components/VoiceControls", () => ({
  default: () => null,
}));

const authValue = {
  user: { id: "u1", email: "a@b.com", mobile_number: null, full_name: "Aditya", role: "user", is_active: true, is_admin: false },
  token: "test-token",
  isLoading: false,
  login: async () => undefined,
  register: async () => undefined,
  logout: () => undefined,
};

type Call = { url: string; method: string; body: unknown };

function mockApi() {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, method, body: init?.body ? String(init.body) : undefined });
      if (url.endsWith("/chat/v2/models")) return new Response(JSON.stringify({ models: [] }), { status: 200 });
      if (url.includes("/chat/v2/conversations") && method === "GET") {
        return new Response(
          JSON.stringify([
            { id: 1, title: "First chat", model: null, provider: null, mode: "chat", system_prompt: null, is_pinned: false, is_archived: false },
            { id: 2, title: "Second chat", model: null, provider: null, mode: "chat", system_prompt: null, is_pinned: false, is_archived: false },
          ]),
          { status: 200 },
        );
      }
      if (url.includes("/conversations/1/stream") && method === "POST") {
        const body = new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('data: {"chunk":"answer"}\\n\\ndata: [DONE]\\n\\n'));
            controller.close();
          },
        });
        return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }
      if (url.includes("/conversations/1") && method === "GET") {
        return new Response(JSON.stringify({ id: 1, title: "First chat", messages: [] }), { status: 200 });
      }
      if (url.match(/\/conversations\/\d+$/) && method === "DELETE") {
        return new Response(JSON.stringify({ deleted: 1 }), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }),
  );
  return calls;
}

function renderChat() {
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={authValue}>
        <ChatWorkspace />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

describe("Chat sidebar", () => {
  beforeEach(() => mockApi());

  it("pins the profile at the bottom with a scrollable history list", async () => {
    const { container } = renderChat();
    await waitFor(() => expect(screen.getByText("First chat")).toBeTruthy());
    const sidebar = container.querySelector(".cw-sidebar");
    const list = container.querySelector(".cw-conv-list");
    const foot = container.querySelector(".cw-sidebar-foot");
    expect(list && foot && sidebar).toBeTruthy();
    // The footer must be a sibling that comes after the scrolling list.
    expect(Array.from(sidebar!.children).indexOf(foot!)).toBeGreaterThan(Array.from(sidebar!.children).indexOf(list!));
    expect(foot!.querySelector(".cw-profile")).toBeTruthy();
    expect(foot!.textContent).toContain("Aditya");
    // Footer is outside the scroll container so it never moves away.
    expect(list!.contains(foot!)).toBe(false);
  });

  it("does not delete a chat without confirmation", async () => {
    const calls = mockApi();
    renderChat();
    await waitFor(() => expect(screen.getByText("First chat")).toBeTruthy());
    const deleteButton = screen.getAllByLabelText(/Delete First chat/i)[0];
    fireEvent.click(deleteButton);
    expect(screen.getByText(/Delete this chat\?/i)).toBeTruthy();
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
  });

  it("deletes the chat after the user confirms", async () => {
    const calls = mockApi();
    renderChat();
    await waitFor(() => expect(screen.getByText("Second chat")).toBeTruthy());
    fireEvent.click(screen.getAllByLabelText(/Delete Second chat/i)[0]);
    fireEvent.click(screen.getByRole("button", { name: /Delete chat/i }));
    await waitFor(() => expect(calls.some((call) => call.method === "DELETE" && call.url.includes("/conversations/2"))).toBe(true));
    await waitFor(() => expect(screen.queryByText("Second chat")).toBeNull());
  });
});

describe("Chat composer image mode", () => {
  beforeEach(() => mockApi());

  it("switches to image generation inside the chat", async () => {
    renderChat();
    await waitFor(() => expect(screen.getByText("First chat")).toBeTruthy());
    const imageTab = screen.getByRole("tab", { name: /Image/i });
    fireEvent.click(imageTab);
    expect(screen.getByPlaceholderText(/mountain lake at sunrise/i)).toBeTruthy();
  });
});


describe("Chat web-search toggle", () => {
  beforeEach(() => mockApi());

  it("includes the web_search flag in stream requests", async () => {
    const calls = mockApi();
    renderChat();
    await waitFor(() => expect(screen.getByText("First chat")).toBeTruthy());
    fireEvent.click(screen.getByLabelText(/Search the web for this answer/i));
    fireEvent.change(screen.getByPlaceholderText(/Message the assistant/i), { target: { value: "latest Python release" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/stream") && JSON.parse(String(call.body)).web_search === true)).toBe(true));
  });
});
