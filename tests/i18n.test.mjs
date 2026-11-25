// Every interface language must have exactly the Romanian keys and the same {placeholders},
// on every page; the About page needs one article per language (run: node tests/i18n.test.mjs).
import { readFileSync } from "node:fs";

const root = new URL("..", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8");

// The language list, from site/lang.js (run without a DOM: only the data is needed).
const langSource = read("site/lang.js");
const codes = [...langSource.matchAll(/\{ code: "([a-z]+)"/g)].map((m) => m[1]);
let failed = 0;
const fail = (message) => { failed++; console.log(`FAIL ${message}`); };
if (codes.length < 8) fail(`expected 8 languages in site/lang.js, found ${codes.join(", ")}`);

function tables(page) {
  const html = read(`site/${page}`);
  const start = html.indexOf("const I18N = {");
  if (start < 0) throw new Error(`no I18N in ${page}`);
  let depth = 0, end = start + "const I18N = ".length;
  for (; end < html.length; end++) {
    if (html[end] === "{") depth++;
    else if (html[end] === "}" && --depth === 0) break;
  }
  return new Function(`return ${html.slice(start + "const I18N = ".length, end + 1)};`)();
}
const placeholders = (text) => [...String(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");

let checked = 0;
for (const page of ["index.html", "places.html"]) {
  const I18N = tables(page);
  const base = I18N.ro;
  for (const code of codes) {
    const table = I18N[code];
    if (!table) { fail(`${page}: no strings for ${code}`); continue; }
    for (const key of Object.keys(base)) {
      if (!(key in table)) fail(`${page} ${code}: missing ${key}`);
      else if (placeholders(table[key]) !== placeholders(base[key])) fail(`${page} ${code}.${key}: placeholders {${placeholders(table[key])}} ≠ {${placeholders(base[key])}}`);
      else if (!String(table[key]).trim()) fail(`${page} ${code}.${key}: empty`);
      checked++;
    }
    for (const key of Object.keys(table)) if (!(key in base)) fail(`${page} ${code}: extra key ${key}`);
  }
}

const about = read("site/about.html");
for (const code of codes) if (!about.includes(`<article lang="${code}" id="content-${code}"`)) fail(`about.html: no article for ${code}`);
const theme = read("site/theme.js");
for (const code of codes) if (!new RegExp(`\\n\\s+${code}: \\{ system:`).test(theme)) fail(`theme.js: no labels for ${code}`);

console.log(`${failed ? "FAILED" : "ok"}: ${codes.length} languages, ${checked} strings checked on 2 pages, About articles and theme labels`);
process.exit(failed ? 1 : 0);
