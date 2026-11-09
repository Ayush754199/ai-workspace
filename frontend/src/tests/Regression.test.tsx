import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import ImageWorkspace from "../pages/ImageWorkspace";
import { AuthContext } from "../contexts/AuthContext";

vi.mock("../components/AppShell", () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const user = {
  id: "regression-user",
  email: "regression@example.com",
  mobile_number: null,
  full_name: "Regression User",
  role: "user",
  is_active: true,
  is_admin: false,
};

const authValue = {
  user,
  token: "test-token",
  isLoading: false,
  login: async () => undefined,
  register: async () => undefined,
  logout: () => undefined,
};

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:test") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});

describe("regressions", () => {
  it("keeps the app loading until a stored auth token is verified", async () => {
    localStorage.setItem("auth_token", "stored-token");
    let resolveUser!: (response: Response) => void;
    const userResponse = new Promise<Response>((resolve) => { resolveUser = resolve; });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo) => {
      if (String(input).includes("/auth/me")) return userResponse;
      return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
    }));

    render(<App />);
    expect(await screen.findByRole("heading", { name: "Loading..." })).toBeTruthy();

    resolveUser(new Response(JSON.stringify(user), { status: 200 }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Chat" })).toBeTruthy());
  });

  it("rejects an invalid image before preview or provider actions are enabled", () => {
    render(
      <MemoryRouter>
        <AuthContext.Provider value={authValue}>
          <ImageWorkspace />
        </AuthContext.Provider>
      </MemoryRouter>,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["not an image"], "payload.txt", { type: "text/plain" })] } });

    expect(screen.getByRole("alert")).toHaveTextContent(/unsupported image/i);
    expect(screen.queryByAltText("Source")).toBeNull();
    expect(screen.getByRole("button", { name: /generate image/i })).toBeDisabled();
  });
});
