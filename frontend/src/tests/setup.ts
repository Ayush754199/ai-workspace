import "@testing-library/jest-dom/vitest";

// Provide a stable storage implementation even when jsdom is launched without
// its optional --localstorage-file runtime flag.
if (!window.localStorage) {
  const values = new Map<string, string>();
  const storage: Storage = {
    get length() { return values.size; },
    clear: () => values.clear(),
    getItem: (key) => values.has(String(key)) ? values.get(String(key))! : null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    removeItem: (key) => { values.delete(String(key)); },
    setItem: (key, value) => { values.set(String(key), String(value)); },
  };
  Object.defineProperty(window, "localStorage", { configurable: true, value: storage });
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage });
}

// jsdom does not implement media playback; the editor calls play()/pause()
// only when a real clip is on the timeline, but guard anyway.
Object.defineProperty(window.HTMLMediaElement.prototype, "play", {
  configurable: true,
  value: () => Promise.resolve(),
});
Object.defineProperty(window.HTMLMediaElement.prototype, "pause", {
  configurable: true,
  value: () => undefined,
});

if (!window.matchMedia) {
  // @ts-expect-error test shim
  window.matchMedia = () => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined });
}

// jsdom has no layout engine, so scrollIntoView is missing on elements.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
