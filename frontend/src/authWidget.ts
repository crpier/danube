// Auth control in the top bar: shows whether a token is set and lets the
// operator paste/clear their JWT. Kept intentionally small — a full OIDC login
// redirect is future work (see auth.ts).

import { getToken, setToken, onTokenChange } from "./auth.ts";
import { el } from "./dom.ts";

export function mountAuthWidget(slot: HTMLElement): void {
  const render = (): void => {
    slot.replaceChildren();
    slot.append(getToken() ? signedIn() : signedOut());
  };
  onTokenChange(render);
  render();
}

function signedIn(): HTMLElement {
  const button = el("button", { class: "btn btn-small", type: "button" }, [
    "Clear token",
  ]);
  button.addEventListener("click", () => setToken(null));
  return el("div", { class: "auth" }, [
    el("span", { class: "muted" }, ["token set"]),
    button,
  ]);
}

function signedOut(): HTMLElement {
  const input = el("input", {
    type: "password",
    placeholder: "Bearer token",
    class: "token-input",
  });
  const button = el("button", { class: "btn btn-small", type: "button" }, [
    "Set token",
  ]);
  const apply = (): void => {
    const value = input.value.trim();
    if (value) setToken(value);
  };
  button.addEventListener("click", apply);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") apply();
  });
  return el("div", { class: "auth" }, [input, button]);
}
