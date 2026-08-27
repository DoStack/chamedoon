import type {
  ApiErrorBody,
  Category,
  ItemRequest,
  Locations,
  Match,
  OpenRequest,
  RequestType,
  User,
} from "@/lib/types";

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody;

  constructor(status: number, body: ApiErrorBody, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

const TOKEN_KEY = "koolbar_token";

export function getApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export function getHealthUrl(): string {
  return `${getApiBaseUrl()}/api/health/`;
}

export function getStoredToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return sessionStorage.getItem(TOKEN_KEY);
}

export function storeToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function formatApiError(body: ApiErrorBody, fallback: string): string {
  if (typeof body.detail === "string") {
    return body.detail;
  }
  const parts: string[] = [];
  for (const [field, value] of Object.entries(body)) {
    if (field === "detail") {
      continue;
    }
    if (Array.isArray(value)) {
      parts.push(`${field}: ${value.map(String).join(" ")}`);
    } else if (typeof value === "string") {
      parts.push(`${field}: ${value}`);
    }
  }
  return parts.join(" ") || fallback;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const token = getStoredToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 12000);
  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}/api${path}`, {
      ...init,
      headers,
      signal: init.signal ?? controller.signal,
    });
  } catch (cause) {
    if (cause instanceof Error && cause.name === "AbortError") {
      throw new ApiError(0, { detail: "Request timed out." }, "Request timed out.");
    }
    throw cause;
  } finally {
    clearTimeout(timeoutId);
  }

  const raw = await response.text();
  let body: unknown = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = { detail: raw };
    }
  }

  if (!response.ok) {
    const errorBody = (body && typeof body === "object" ? body : {}) as ApiErrorBody;
    throw new ApiError(response.status, errorBody, formatApiError(errorBody, "Request failed"));
  }

  return (body ?? {}) as T;
}

export function authTelegram(payload: {
  init_data?: string;
  dev_user?: {
    telegram_user_id: number;
    first_name: string;
    last_name?: string;
    telegram_username?: string;
  };
}): Promise<{ token: string; user: User }> {
  return request("/auth/telegram/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchMe(): Promise<User> {
  return request("/me/");
}

export function fetchCategories(): Promise<Category[]> {
  return request("/categories/");
}

export function fetchLocations(): Promise<Locations> {
  return request("/locations/");
}

export function fetchRequests(): Promise<ItemRequest[]> {
  return request("/requests/");
}

export function fetchRequest(id: number): Promise<ItemRequest> {
  return request(`/requests/${id}/`);
}

export function createRequest(payload: Record<string, unknown>): Promise<ItemRequest> {
  return request("/requests/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateRequest(
  id: number,
  payload: Record<string, unknown>,
): Promise<ItemRequest> {
  return request(`/requests/${id}/`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function cancelRequest(id: number): Promise<ItemRequest> {
  return request(`/requests/${id}/cancel/`, { method: "POST" });
}

export function fetchMatches(): Promise<Match[]> {
  return request("/matches/");
}

export function fetchMatch(id: number): Promise<Match> {
  return request(`/matches/${id}/`);
}

export function acceptMatch(id: number): Promise<Match> {
  return request(`/matches/${id}/accept/`, { method: "POST" });
}

export function rejectMatch(id: number): Promise<Match> {
  return request(`/matches/${id}/reject/`, { method: "POST" });
}

export type ExploreFilters = {
  type?: RequestType | "";
  origin_country?: string;
  origin_city?: string;
  destination_country?: string;
  destination_city?: string;
  date_from?: string;
  date_to?: string;
  category?: string;
};

export function fetchExplore(filters: ExploreFilters = {}): Promise<OpenRequest[]> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return request(`/explore/${query ? `?${query}` : ""}`);
}

export function connectExplore(id: number, myRequestId?: number): Promise<Match> {
  return request(`/explore/${id}/connect/`, {
    method: "POST",
    body: JSON.stringify(myRequestId ? { my_request_id: myRequestId } : {}),
  });
}
