import { readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const LIMIT_KB = 300;
const dist = new URL("../dist/", import.meta.url);
const html = readFileSync(new URL("index.html", dist), "utf8");
const initial = [...html.matchAll(/(?:src|href)="\/(assets\/[^"]+\.(?:js|css))"/g)].map(
  (match) => match[1],
);
const sizes = initial.map((path) => ({
  path,
  kb: gzipSync(readFileSync(new URL(path, dist))).length / 1024,
}));
const total = sizes.reduce((sum, item) => sum + item.kb, 0);
const lines = sizes.map((item) => `${item.kb.toFixed(1).padStart(7)} KB  ${item.path}`);
lines.push(`${total.toFixed(1).padStart(7)} KB  main route, gzip (limit ${LIMIT_KB} KB)`);
process.stdout.write(`${lines.join("\n")}\n`);
if (total > LIMIT_KB) process.exitCode = 1;
