import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import InviteMemberModal from "../components/InviteMemberModal";

// The SPA and the API are separate deployments. An invite link must point at the
// app, never at the API origin: the API returns 404 for /rooms, so a recipient
// clicking such a link lands on an error page instead of the join screen.
describe("InviteMemberModal invite link", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_APP_ORIGIN", "");
  });

  it("builds the link from the app origin, not the API base url", async () => {
    const createLink = vi.fn().mockResolvedValue("tok_abc123");
    render(<InviteMemberModal onClose={() => { }} onSubmit={async () => null} onCreateLink={createLink} onSearchUsers={async () => []} />);

    fireEvent.click(screen.getByRole("button", { name: /create invite link/i }));

    await waitFor(() => expect(screen.getByLabelText(/room invite link/i)).toBeTruthy());
    const value = (screen.getByLabelText(/room invite link/i) as HTMLInputElement).value;
    expect(value).toBe(`${window.location.origin}/rooms?invite=tok_abc123`);
    expect(value).not.toContain("ai-workspace-gm23.onrender.com");
  });

  it("honours an explicit VITE_APP_ORIGIN for split deployments", async () => {
    vi.stubEnv("VITE_APP_ORIGIN", "https://ai-workspace-frontend-907h.onrender.com/");
    vi.resetModules();
    const { appOrigin } = await import("../api/client");
    expect(appOrigin).toBe("https://ai-workspace-frontend-907h.onrender.com");
  });
});
