import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// The deployed static host serves dist/<route>/index.html and has no SPA rewrite,
// so every client route must be copied at build time. /rooms and /calls were
// missing, which 404'd invite links and the calls page on hard load.
const read = (path: string) => readFileSync(resolve(__dirname, "..", path), "utf8");

function routedPaths(source: string): string[] {
  return [...source.matchAll(/path="\/([^":]*)"/g)].map((match) => match[1]).filter(Boolean);
}

describe("SPA deep-link copies", () => {
  it("every App.tsx route is copied into dist by the build", () => {
    const appRoutes = routedPaths(read("App.tsx"));
    const viteConfig = read("../vite.config.ts");
    const copied = [...viteConfig.matchAll(/for \(const route of \[([^\]]+)\]/g)]
      .flatMap((match) => match[1].split(","))
      .map((value) => value.trim().replace(/^['"]|['"]$/g, ""))
      .filter(Boolean);

    const missing = appRoutes.filter((route) => !copied.includes(route));
    expect(missing).toEqual([]);
  });

  it("copies the invite-link and call routes specifically", () => {
    const viteConfig = read("../vite.config.ts");
    expect(viteConfig).toContain("'rooms'");
    expect(viteConfig).toContain("'calls'");
  });
});
