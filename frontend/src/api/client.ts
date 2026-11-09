export const apiBaseUrl =
  import.meta.env.VITE_API_BASE_URL || `${window.location.protocol}//${window.location.hostname}:8000`;

/** FastAPI validation errors arrive as a list of {loc, msg}; flatten to one line. */
export function readErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item?.msg === "string" ? item.msg : ""))
      .filter(Boolean);
    if (messages.length) return messages.join(", ");
  }
  return fallback;
}

export async function requestJson<T>(
  url: string,
  init: RequestInit,
  fallbackError = "Something went wrong",
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    // A CORS rejection also surfaces here as a TypeError; keep the cause visible
    // instead of always blaming a stopped backend.
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(
      `Cannot reach the API at ${new URL(url).origin}. Is the backend running and is this origin allowed by CORS_ORIGINS? (${detail})`,
    );
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(readErrorDetail((data as { detail?: unknown }).detail, fallbackError)) as Error & {
      status?: number;
    };
    error.status = response.status;
    throw error;
  }
  return data as T;
}

export async function apiRequest<T>(token: string, path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || 'Request failed') as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return data as T;
}
