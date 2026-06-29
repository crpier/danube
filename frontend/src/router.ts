// Minimal History-API router. The Master serves `index.html` for every
// non-API path (SPA fallback), so deep links and reloads work. A view may
// return a teardown function (e.g. to abort an in-flight log stream); it runs
// before the next navigation.

export type Teardown = () => void;
export type View = (
  root: HTMLElement,
  params: Record<string, string>,
) => void | Teardown | Promise<void | Teardown>;

interface Route {
  pattern: RegExp;
  keys: string[];
  view: View;
}

const routes: Route[] = [];
let root: HTMLElement;
let teardown: Teardown | null = null;

export function route(path: string, view: View): void {
  const keys: string[] = [];
  const pattern = new RegExp(
    "^" +
      path.replace(/:[^/]+/g, (match) => {
        keys.push(match.slice(1));
        return "([^/]+)";
      }) +
      "/?$",
  );
  routes.push({ pattern, keys, view });
}

export function navigate(path: string): void {
  if (path !== location.pathname) {
    history.pushState({}, "", path);
  }
  void render();
}

export function startRouter(mount: HTMLElement): void {
  root = mount;
  window.addEventListener("popstate", () => void render());
  document.addEventListener("click", onLinkClick);
  void render();
}

function onLinkClick(event: MouseEvent): void {
  if (event.defaultPrevented || event.button !== 0) return;
  const target = (event.target as HTMLElement | null)?.closest("a[data-link]");
  if (!(target instanceof HTMLAnchorElement)) return;
  if (target.target === "_blank" || target.origin !== location.origin) return;
  event.preventDefault();
  navigate(target.pathname);
}

async function render(): Promise<void> {
  if (teardown) {
    teardown();
    teardown = null;
  }
  const path = location.pathname;
  for (const entry of routes) {
    const match = entry.pattern.exec(path);
    if (!match) continue;
    const params: Record<string, string> = {};
    entry.keys.forEach((key, index) => {
      params[key] = decodeURIComponent(match[index + 1] ?? "");
    });
    const result = await entry.view(root, params);
    teardown = typeof result === "function" ? result : null;
    return;
  }
  root.replaceChildren();
  root.append(
    Object.assign(document.createElement("p"), { textContent: "Not found." }),
  );
}
