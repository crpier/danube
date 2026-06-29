// Production build: bundle the HTML entrypoint (and the TS/CSS it references)
// into `dist/`, which the Master serves at `/`. Run with `bun run build`.

import { rm } from "node:fs/promises";

const OUTDIR = "dist";

await rm(OUTDIR, { recursive: true, force: true });

const result = await Bun.build({
  entrypoints: ["./index.html"],
  outdir: OUTDIR,
  minify: true,
  sourcemap: "linked",
  // Absolute asset URLs so the bundled JS/CSS resolve from the site root. With
  // relative URLs a deep link like `/jobs/123` (served the SPA shell via the
  // Master's fallback) would resolve assets under `/jobs/`, which 404s.
  publicPath: "/",
});

if (!result.success) {
  for (const log of result.logs) {
    console.error(log);
  }
  process.exit(1);
}

console.log(`Built ${result.outputs.length} file(s) into ${OUTDIR}/`);
