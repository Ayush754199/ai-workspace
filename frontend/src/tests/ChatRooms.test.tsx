import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Dashboard from "../pages/Dashboard";
import ChatAuditTab from "../components/ChatAuditTab";
import MessageBubble from "../components/MessageBubble";
import { AuthContext } from "../contexts/AuthContext";

vi.mock("../components/AppShell", () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="shell">{children}</div>,
}));

function authValue(role: "user" | "admin") {
  return {
    user: { id: 1, email: "alice@example.com", mobile_number: null, full_name: "Dr. Ramesh Sharma", role, is_active: true, is_admin: role === "admin" },
    token: "test-token",
    isLoading: false,
    login: async () => undefined,
    register: async () => undefined,
    logout: () => undefined,
  };
}

const rooms = [
  { id: 10, name: "Alpha review", room_type: "chat", members: [1, 2], member_details: [{ id: 1, name: "Dr. Ramesh Sharma", email: "alice@example.com", role: "doctor" }, { id: 2, name: "Priya Nair", email: "bob@example.com", role: "ca" }], last_message_at: null, created_by: 1, is_pinned: false, is_archived: false },
  { id: 11, name: "Beta review", room_type: "chat", members: [2], member_details: [{ id: 2, name: "Priya Nair", email: "bob@example.com", role: "ca" }], last_message_at: null, created_by: 2, is_pinned: false, is_archived: false },
];

const messages = [
  { id: 1, room_id: 10, sender_id: 1, sender_name: "Dr. Ramesh Sharma", sender_role: "doctor", content: "Hello <script>alert(1)</script> 🎉 नमस्ते", created_at: "2026-11-04T10:00:00Z" },
  { id: 2, room_id: 10, sender_id: 2, sender_name: "Priya Nair", sender_role: "ca", content: "Second message", created_at: "2026-11-04T10:01:00Z" },
];

function stubFetch(handler?: (url: string, method: string) => Response | null) {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, method, body: init?.body ? String(init.body) : undefined });
      const custom = handler?.(url, method);
      if (custom) return custom;
      if (url.endsWith("/api/v1/rooms") && method === "GET") return new Response(JSON.stringify(rooms), { status: 200 });
      if (url.includes("/messages") && method === "GET") return new Response(JSON.stringify(messages), { status: 200 });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }),
  );
  return calls;
}

function renderDashboard(role: "user" | "admin" = "user") {
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={authValue(role)}>
        <Dashboard />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

beforeEach(() => stubFetch());

describe("ROOM-03 / MSG-01 / MSG-02 / MSG-07", () => {
  it("lists only the rooms the API returns for this member", async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByText("Alpha review")).toBeTruthy());
    expect(screen.getByText("Beta review")).toBeTruthy();
  });

  it("shows the sender tag as 'Name (Title-Cased Role)'", () => {
    render(<MessageBubble message={{ ...messages[0], room_id: 10 } as never} own={false} />);
    expect(screen.getByText("Dr. Ramesh Sharma (Doctor)")).toBeTruthy();
  });

  it("title-cases unknown and multi-word roles", () => {
    render(<MessageBubble message={{ ...messages[1], sender_role: "admin" } as never} own={false} />);
    expect(screen.getByText("Priya Nair (Admin)")).toBeTruthy();
  });

  it("renders script payload as text, never as an element", async () => {
    const { container } = renderDashboard();
    await waitFor(() => expect(screen.getByText("Alpha review")).toBeTruthy());
    fireEvent.click(screen.getByText("Alpha review"));
    await waitFor(() => expect(container.textContent).toContain("<script>alert(1)</script>"));
    expect(container.querySelector("script")).toBeNull();
    // The markup carries escaped text, never a live <script> element.
    expect(container.querySelector(".room-messages")!.innerHTML).toContain("&lt;script&gt;");
  });

  it("keeps Hindi and emoji text intact", async () => {
    const { container } = renderDashboard();
    await waitFor(() => expect(screen.getByText("Alpha review")).toBeTruthy());
    fireEvent.click(screen.getByText("Alpha review"));
    await waitFor(() => expect(container.textContent).toContain("🎉 नमस्ते"));
  });

  it("keeps message order from the API", async () => {
    const { container } = renderDashboard();
    await waitFor(() => expect(screen.getByText("Alpha review")).toBeTruthy());
    fireEvent.click(screen.getByText("Alpha review"));
    await waitFor(() => {
      const tags = Array.from(container.querySelectorAll(".sender-tag")).map((node) => node.textContent);
      expect(tags).toEqual(["Dr. Ramesh Sharma (Doctor)", "Priya Nair (Ca)"]);
    });
  });
});

describe("ADM-CR-01..08 admin surveillance UI", () => {
  it("sends the combined filters and clears them", async () => {
    const calls: { url: string; method: string }[] = [];
    stubFetch((url) => {
      calls.push({ url, method: "GET" });
      if (url.includes("/api/v1/admin/chats")) return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
      return null;
    });
    render(<ChatAuditTab token="test-token" />);
    fireEvent.change(screen.getByPlaceholderText("Username or email"), { target: { value: "sharma" } });
    fireEvent.change(screen.getByPlaceholderText("Room name"), { target: { value: "alpha" } });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "doctor" } });
    await waitFor(() => {
      const last = calls.filter((call) => call.url.includes("/api/v1/admin/chats")).pop();
      expect(last?.url).toContain("username=sharma");
      expect(last?.url).toContain("role=doctor");
      expect(last?.url).toContain("room_name=alpha");
    });
    expect(await screen.findByText("No matching rooms.")).toBeTruthy();
    fireEvent.click(screen.getByText("Clear"));
    await waitFor(() => {
      const last = calls.filter((call) => call.url.includes("/api/v1/admin/chats")).pop();
      expect(last?.url).not.toContain("username=");
    });
  });

  it("shows an audit notice, title-cased sender tags, and read-only transcript", async () => {
    stubFetch((url) => {
      if (url.match(/\/api\/v1\/admin\/chats\/10$/)) {
        return new Response(JSON.stringify({ ...rooms[0], messages, member_count: 2 }), { status: 200 });
      }
      if (url.includes("/api/v1/admin/chats")) return new Response(JSON.stringify({ items: [rooms[0]], total: 1 }), { status: 200 });
      return null;
    });
    render(<ChatAuditTab token="test-token" />);
    expect(await screen.findByText(/Admin access to chats is logged/i)).toBeTruthy();
    fireEvent.click(await screen.findByText("Alpha review"));
    expect(await screen.findByText(/Read-only transcript/i)).toBeTruthy();
    expect(screen.getByText(/Dr. Ramesh Sharma \(Doctor\)/)).toBeTruthy();
    expect(screen.getByText(/Priya Nair \(Ca\)/)).toBeTruthy();
  });
});
