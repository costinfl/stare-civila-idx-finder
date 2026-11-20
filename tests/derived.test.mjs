// Check the derived-value copies in site/index.html against tests/derived.json (run: node tests/derived.test.mjs).
import { readFileSync } from "node:fs";

const root = new URL("..", import.meta.url);
const html = readFileSync(new URL("site/index.html", root), "utf8");
const block = /\/\/ BEGIN derived[^\n]*\n([\s\S]*?)\/\/ END derived/.exec(html);
if (!block) throw new Error("derived block not found in site/index.html");
const fold = (s) => String(s).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const { normalizeDate, eventCode } = new Function("fold", `${block[1]}; return { normalizeDate, eventCode };`)(fold);
const cases = JSON.parse(readFileSync(new URL("tests/derived.json", root), "utf8"));
let failed = 0;
for (const [date, year, event, act, title, sortDate, code] of cases) {
  const got = [normalizeDate(date, year), eventCode(event, act, title)];
  if (got[0] !== sortDate || got[1] !== code) { failed++; console.log(`FAIL ${JSON.stringify([date, year, event, act, title])}: expected ${sortDate}/${code}, got ${got.join("/")}`); }
}
console.log(`${cases.length - failed}/${cases.length} derived values match (JavaScript)`);
process.exit(failed ? 1 : 0);
