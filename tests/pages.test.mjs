// Check the imagePage copy in site/index.html against tests/pages.json (run: node tests/pages.test.mjs).
import { readFileSync } from "node:fs";

const root = new URL("..", import.meta.url);
const html = readFileSync(new URL("site/index.html", root), "utf8");
const block = /\/\/ BEGIN pages[^\n]*\n([\s\S]*?)\/\/ END pages/.exec(html);
if (!block) throw new Error("pages block not found in site/index.html");
const imagePage = new Function(`${block[1]}; return imagePage;`)();
const cases = JSON.parse(readFileSync(new URL("tests/pages.json", root), "utf8"));
let failed = 0;
for (const [starts, image, expected] of cases) {
  const got = imagePage(starts, image);
  if (JSON.stringify(got) !== JSON.stringify(expected)) { failed++; console.log(`FAIL ${JSON.stringify(starts)} image ${image}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(got)}`); }
}
console.log(`${cases.length - failed}/${cases.length} image pages match (JavaScript)`);
process.exit(failed ? 1 : 0);
