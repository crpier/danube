// Bearer-token auth for the SPA.
//
// The Master protects write/read routes with OIDC-issued JWTs (`danube.auth`).
// A static SPA cannot complete an OIDC redirect/PKCE flow without runtime issuer
// config, so this MVP consumes a JWT directly: the operator pastes a token
// obtained from their IdP, it is kept in `localStorage`, and attached as
// `Authorization: Bearer` on every API call (including the log stream, which is
// why the stream is read over `fetch` rather than `EventSource` — the latter
// cannot set headers). A full OIDC login flow is future work; see
// `docs/architecture/components.md`.

const STORAGE_KEY = "danube.token";

type Listener = (token: string | null) => void;
const listeners = new Set<Listener>();

export function getToken(): string | null {
  return localStorage.getItem(STORAGE_KEY);
}

export function setToken(token: string | null): void {
  if (token) {
    localStorage.setItem(STORAGE_KEY, token);
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
  for (const listener of listeners) {
    listener(token);
  }
}

export function onTokenChange(listener: Listener): void {
  listeners.add(listener);
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
