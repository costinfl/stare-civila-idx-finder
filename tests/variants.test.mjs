// Check the variantKey copy in site/index.html against tests/variants.json (run: node tests/variants.test.mjs).
import { readFileSync } from "node:fs";

const root = new URL("..", import.meta.url);
const html = readFileSync(new URL("site/index.html", root), "utf8");
const block = /\/\/ BEGIN variantKey[^\n]*\n([\s\S]*?)\/\/ END variantKey/.exec(html);
if (!block) throw new Error("variantKey block not found in site/index.html");
const variantKey = new Function(`${block[1]}; return variantKey;`)();
const fold = (s) => String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const cases = JSON.parse(readFileSync(new URL("tests/variants.json", root), "utf8"));
let failed = 0;
for (const [word, expected] of Object.entries(cases)) {
  const got = variantKey(fold(word));
  if (got !== expected) { failed++; console.log(`FAIL ${word}: expected ${expected}, got ${got}`); }
}
console.log(`${Object.keys(cases).length - failed}/${Object.keys(cases).length} variant keys match (JavaScript)`);
process.exit(failed ? 1 : 0);
