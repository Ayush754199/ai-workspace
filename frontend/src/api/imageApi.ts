import { apiBaseUrl } from "./client";

export async function runImageStudio(token: string, prompt: string, image?: File): Promise<Blob> {
  const form = new FormData();
  form.append("prompt", prompt);
  if (image) form.append("image", image);
  const response = await fetch(`${apiBaseUrl}/api/v1/chat/image`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Image workflow failed");
  }
  return response.blob();
}
