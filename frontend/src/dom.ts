// Tiny DOM helpers so the views stay readable without a framework.

type Attrs = Record<string, string | ((event: Event) => void) | boolean>;

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  children: (Node | string)[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (typeof value === "function") {
      node.addEventListener(key.replace(/^on/, "").toLowerCase(), value);
    } else if (typeof value === "boolean") {
      if (value) node.setAttribute(key, "");
    } else if (key === "class") {
      node.className = value;
    } else {
      node.setAttribute(key, value);
    }
  }
  for (const child of children) {
    node.append(child);
  }
  return node;
}

export function clear(node: HTMLElement): void {
  node.replaceChildren();
}

export function statusBadge(status: string): HTMLElement {
  return el("span", { class: `badge badge-${status}` }, [status]);
}

export function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
