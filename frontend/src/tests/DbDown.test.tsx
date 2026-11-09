import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Login from "../pages/Login";
import ChatWorkspace from "../pages/ChatWorkspace";
import { AuthContext } from "../contexts/AuthContext";

vi.mock("../components/VoiceControls", () => ({ default: () => null }));

const authValue = {
  user: { id: 1, email: "a@b.com", mobile_number: null, full_name: "Tester", role: "user", is_active: true, is_admin: false },
  token: "test-token",
  isLoading: false,
  login: async () => undefined,
  register: async () => undefined,
  logout: () => undefined,
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify({ detail: "Database temporarily unavailable. Please try again in a few seconds." }), {
        status: 503,
        headers: { "Retry-After": "3" },
      }),
    ),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe("NET-06: database-down messaging", () => {
  it("login shows the backend's retry guidance instead of a generic error", async () => {
    render(
      <MemoryRouter>
        <AuthContext.Provider value={{ ...authValue, login: async () => { throw Object.assign(new Error("Database temporarily unavailable. Please try again in a few seconds."), { status: 503 }); } }}>
          <Login />
        </AuthContext.Provider>
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText(/email or mobile number/i), { target: { value: "a@b.com" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "whatever" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Database temporarily unavailable");
    expect(alert.textContent).toContain("try again in a few seconds");
    // The form stays usable for a retry.
    expect(screen.getByRole("button", { name: /sign in/i })).toBeTruthy();
  });

  it("chat surfaces the same 503 message and keeps the composer editable", async () => {
    render(
      <MemoryRouter>
        <AuthContext.Provider value={authValue}>
          <ChatWorkspace />
        </AuthContext.Provider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/database temporarily unavailable/i)).toBeTruthy());
    const box = screen.getByPlaceholderText(/message the assistant/i) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "still here" } });
    expect(box.value).toBe("still here");
  });
});
