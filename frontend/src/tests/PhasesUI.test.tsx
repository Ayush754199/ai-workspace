import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ChatWorkspace from "../pages/ChatWorkspace";
import Documents from "../pages/Documents";
import Office from "../pages/Office";
import ImageWorkspace from "../pages/ImageWorkspace";
import Research from "../pages/Research";
import { AuthContext } from "../contexts/AuthContext";
import renderMarkdown from "../components/Markdown";

vi.mock("../components/AppShell", () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="shell">{children}</div>,
}));

const authValue = {
  user: {
    id: "ui-tester",
    email: "ui@example.com",
    mobile_number: null,
    full_name: "UI Tester",
    role: "user",
    is_active: true,
    is_admin: false,
  },
  token: "test-token",
  isLoading: false,
  login: async () => undefined,
  register: async () => undefined,
  logout: () => undefined,
};

function withAuth(node: React.ReactNode) {
  return (
    <MemoryRouter>
      <AuthContext.Provider value={authValue}>{node}</AuthContext.Provider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.includes("/chat/v2/models")) {
        return new Response(
          JSON.stringify({
            models: [
              { id: "auto", label: "Auto", provider: "auto", capabilities: ["chat"], free: true, selectable: true, reason: null },
              { id: "groq:llama", label: "Groq · llama", provider: "groq", capabilities: ["chat"], free: true, selectable: false, reason: "credentials missing" },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/voice/status")) {
        return new Response(JSON.stringify({ stt: { groq: true, gemini: false }, tts: { edge_tts: true } }), { status: 200 });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
});

describe("Phase 1-7 UI", () => {
  it("renders the chat workspace with starters and a model picker", async () => {
    render(withAuth(<ChatWorkspace />));
    expect(await screen.findByText(/What would you like to get done/i)).toBeTruthy();
    expect(screen.getByRole("combobox")).toBeTruthy();
    expect(screen.getByPlaceholderText(/Message the assistant/i)).toBeTruthy();
    expect(await screen.findByRole("option", { name: /Groq · llama — unavailable/i })).toBeTruthy();
  });

  it("renders the documents page with upload, summarise and generation", () => {
    render(withAuth(<Documents />));
    expect(screen.getByText("PDF & document workspace")).toBeTruthy();
    expect(screen.getByText(/Drop a document here/i)).toBeTruthy();
    expect(screen.getByText(/Summarise document/i)).toBeTruthy();
    expect(screen.getByText(/Generate file/i)).toBeTruthy();
  });

  it("renders office tools for every format", () => {
    render(withAuth(<Office />));
    for (const label of ["Excel spreadsheet", "Word document", "PowerPoint deck", "CSV dataset", "PDF report"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it("renders the image AI page with background removal and vision actions", () => {
    render(withAuth(<ImageWorkspace />));
    expect(screen.getByText("Remove background")).toBeTruthy();
    expect(screen.getByText("Enhance")).toBeTruthy();
    expect(screen.getByText("Ask vision model")).toBeTruthy();
  });

  it("renders research with the query form", () => {
    render(withAuth(<Research />));
    expect(screen.getByPlaceholderText(/What do you want to research/i)).toBeTruthy();
    expect(screen.getByText(/Answers grounded in live web sources/i)).toBeTruthy();
  });
});

describe("Markdown renderer", () => {
  it("renders headings, bold, code and lists without HTML injection", () => {
    const { container } = render(<div>{renderMarkdown("# Title\n\n**bold** and `code`\n\n- one\n- two\n\n<script>x</script>")}</div>);
    expect(container.querySelector("h3")?.textContent).toBe("Title");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("code")?.textContent).toBe("code");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("script")).toBeNull();
  });
});
