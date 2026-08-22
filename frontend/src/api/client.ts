import type { ApiErrorBody } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly fieldErrors: Record<string, string[]> | null;

  constructor(status: number, body: ApiErrorBody = {}) {
    super(body.message ?? body.detail ?? `请求失败（${status}）`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code ?? null;
    this.fieldErrors = body.field_errors ?? null;
  }
}

export class AuthExpiredError extends ApiError {
  constructor(body: ApiErrorBody = {}) {
    super(401, body);
    this.name = "AuthExpiredError";
  }
}

type AuthExpiredListener = () => void;

async function readError(response: Response): Promise<ApiErrorBody> {
  try {
    const value: unknown = await response.json();
    if (value && typeof value === "object") {
      return value as ApiErrorBody;
    }
  } catch {
    // The status code still provides a stable fallback for non-JSON responses.
  }
  return {};
}

class ApiClient {
  private readonly authExpiredListeners = new Set<AuthExpiredListener>();

  onAuthExpired(listener: AuthExpiredListener): () => void {
    this.authExpiredListeners.add(listener);
    return () => this.authExpiredListeners.delete(listener);
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    if (!path.startsWith("/") || path.startsWith("//")) {
      throw new TypeError("API requests must use a same-origin absolute path");
    }

    const headers = new Headers(init.headers);
    if (init.body != null && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (!headers.has("Accept")) {
      headers.set("Accept", "application/json");
    }

    const response = await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers,
    });
    if (!response.ok) {
      const body = await readError(response);
      if (response.status === 401) {
        this.authExpiredListeners.forEach((listener) => listener());
        throw new AuthExpiredError(body);
      }
      throw new ApiError(response.status, body);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  async requestText(path: string): Promise<string> {
    if (!path.startsWith("/") || path.startsWith("//")) {
      throw new TypeError("API requests must use a same-origin absolute path");
    }
    const response = await fetch(path, { credentials: "same-origin", headers: { Accept: "text/plain" } });
    if (!response.ok) {
      const body = await readError(response);
      if (response.status === 401) {
        this.authExpiredListeners.forEach((listener) => listener());
        throw new AuthExpiredError(body);
      }
      throw new ApiError(response.status, body);
    }
    return response.text();
  }
}

export const api = new ApiClient();
