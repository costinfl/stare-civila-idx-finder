// Interface languages shared by every page: the list, the default choice and the
// flag dropdown. Each page keeps its own strings and re-renders when told to.
window.SiteLang = (() => {
  const S = (body) => `<svg viewBox="0 0 30 20" width="21" height="14" aria-hidden="true" focusable="false" preserveAspectRatio="none">${body}</svg>`;
  const FLAGS = {
    ro: S('<path fill="#002B7F" d="M0 0h10v20H0z"/><path fill="#FCD116" d="M10 0h10v20H10z"/><path fill="#CE1126" d="M20 0h10v20H20z"/>'),
    hu: S('<path fill="#CE2939" d="M0 0h30v7H0z"/><path fill="#fff" d="M0 7h30v6H0z"/><path fill="#477050" d="M0 13h30v7H0z"/>'),
    de: S('<path fill="#000" d="M0 0h30v7H0z"/><path fill="#DD0000" d="M0 7h30v6H0z"/><path fill="#FFCE00" d="M0 13h30v7H0z"/>'),
    en: S('<path fill="#012169" d="M0 0h30v20H0z"/><path stroke="#fff" stroke-width="4" d="M0 0l30 20M30 0L0 20"/><path stroke="#C8102E" stroke-width="1.6" d="M0 0l30 20M30 0L0 20"/>'
      + '<path stroke="#fff" stroke-width="6" d="M15 0v20M0 10h30"/><path stroke="#C8102E" stroke-width="3.5" d="M15 0v20M0 10h30"/>'),
    tr: S('<path fill="#E30A17" d="M0 0h30v20H0z"/><circle cx="11" cy="10" r="5" fill="#fff"/><circle cx="12.3" cy="10" r="4" fill="#E30A17"/>'
      + '<polygon fill="#fff" points="15.00,10.00 16.75,9.38 16.80,7.53 17.92,9.00 19.70,8.47 18.65,10.00 19.70,11.53 17.92,11.00 16.80,12.47 16.75,10.62"/>'),
    // Crimean Tatar national flag: sky blue with the golden tarak tamga at the top left.
    crh: S('<path fill="#00A3DD" d="M0 0h30v20H0z"/><g fill="none" stroke="#F8D20F" stroke-width="1.2" stroke-linecap="round">'
      + '<path d="M3 2.5h7M3 2.5v2.4M10 2.5v2.4M6.5 2.5v5.5M5 6.2h3"/></g>'),
    // Romani flag (1971 World Romani Congress): blue over green with a red sixteen-spoke chakra.
    rom: S('<path fill="#2E58A6" d="M0 0h30v10H0z"/><path fill="#3C9A3C" d="M0 10h30v10H0z"/><g stroke="#E02424" fill="none">'
      + '<circle cx="15" cy="10" r="6" stroke-width="1.1"/><circle cx="15" cy="10" r="1.2" stroke-width="1"/>'
      + Array.from({ length: 16 }, (_, i) => { const a = i * Math.PI / 8; return `<path stroke-width=".6" d="M${(15 + 1.2 * Math.cos(a)).toFixed(2)} ${(10 + 1.2 * Math.sin(a)).toFixed(2)}L${(15 + 6 * Math.cos(a)).toFixed(2)} ${(10 + 6 * Math.sin(a)).toFixed(2)}"/>`; }).join("")
      + '</g>'),
    he: S('<path fill="#fff" d="M0 0h30v20H0z"/><path fill="#0038B8" d="M0 2h30v2.6H0zM0 15.4h30V18H0z"/>'
      + '<g fill="none" stroke="#0038B8" stroke-width=".9"><path d="M15 6.2l3.3 5.7h-6.6z"/><path d="M15 13.8l-3.3-5.7h6.6z"/></g>'),
  };
  const LANGS = [
    { code: "ro", name: "Română", locale: "ro-RO" },
    { code: "hu", name: "Magyar", locale: "hu-HU" },
    { code: "de", name: "Deutsch", locale: "de-DE" },
    { code: "en", name: "English", locale: "en-GB" },
    { code: "tr", name: "Türkçe", locale: "tr-TR" },
    { code: "crh", name: "Qırımtatarca", locale: "tr-TR", review: { text: "Tercime tüzetilmekte — tüzetüv teklif etiñiz", link: "Tüzetüv teklif et" } },
    { code: "rom", name: "Romani čhib", locale: "ro-RO", review: { text: "O anglipen si ande lačharipen — sikav amenqe so si te lačharas", link: "Bičhal jekh lačharipen" } },
    { code: "he", name: "עברית", locale: "he-IL", dir: "rtl" },
  ];
  const CODES = LANGS.map(l => l.code);
  const BY = Object.fromEntries(LANGS.map(l => [l.code, l]));
  const ALIASES = { iw: "he", rmy: "rom", rmn: "rom", rmc: "rom", rml: "rom", rmo: "rom", rmf: "rom", rmw: "rom", rmu: "rom" };
  const store = {
    get() { try { return localStorage.getItem("lang"); } catch { return null; } },
    set(v) { try { localStorage.setItem("lang", v); } catch {} },
  };

  /** Hash first, then the remembered choice, then the browser's languages, else Romanian. */
  function pick() {
    const fromHash = new URLSearchParams(location.hash.slice(1)).get("lang");
    if (CODES.includes(fromHash)) return fromHash;
    const stored = store.get();
    if (CODES.includes(stored)) return stored;
    for (const tag of navigator.languages || [navigator.language || ""]) {
      const base = String(tag).toLowerCase().split("-")[0];
      const code = ALIASES[base] || base;
      if (CODES.includes(code)) return code;
    }
    return "ro";
  }
  const valid = (code) => (CODES.includes(code) ? code : "ro");
  const locale = (code) => (BY[code] || BY.ro).locale;

  /** Set <html lang/dir>, remember the choice and show the review notice where needed. */
  function apply(code) {
    code = valid(code);
    const root = document.documentElement;
    root.lang = code;
    root.dir = BY[code].dir || "ltr";
    store.set(code);
    for (const menu of menus) menu.show(code);
    let note = document.getElementById("lang-review");
    const review = BY[code].review;
    if (!review) { if (note) note.hidden = true; return code; }
    if (!note) {
      note = document.createElement("p");
      note.id = "lang-review";
      note.className = "lang-review";
      document.querySelector("main")?.prepend(note);
    }
    const url = "https://github.com/costinfl/stare-civila-idx-finder/issues/new?" + new URLSearchParams({
      title: `Translation (${BY[code].name}): `, body: `Page: ${location.pathname.split("/").pop() || "index.html"}\n\nText now:\n\nSuggested text:\n` });
    note.replaceChildren(review.text + " · ");
    const a = document.createElement("a"); a.href = url; a.target = "_blank"; a.rel = "noopener"; a.textContent = review.link;
    note.append(a);
    note.hidden = false;
    return code;
  }

  const menus = [];
  /** Replace `el` with a flag dropdown; onPick(code) is called when the visitor chooses. */
  function mount(el, onPick) {
    const wrap = document.createElement("div");
    wrap.className = "lang-menu";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "lang-button";
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    const list = document.createElement("ul");
    list.className = "lang-list";
    list.setAttribute("role", "listbox");
    list.tabIndex = -1;
    list.hidden = true;
    for (const l of LANGS) {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.id = `lang-opt-${l.code}`;
      li.dataset.code = l.code;
      li.lang = l.code;
      if (l.dir) li.dir = l.dir;
      li.innerHTML = FLAGS[l.code];
      li.append(" " + l.name);
      li.addEventListener("click", () => choose(l.code));
      list.append(li);
    }
    wrap.append(button, list);
    el.replaceWith(wrap);
    let current = "ro", active = 0;
    const options = [...list.children];
    function mark(i) {
      active = (i + options.length) % options.length;
      options.forEach((o, k) => o.classList.toggle("active", k === active));
      list.setAttribute("aria-activedescendant", options[active].id);
      options[active].scrollIntoView({ block: "nearest" });
    }
    function open() {
      list.hidden = false;
      button.setAttribute("aria-expanded", "true");
      mark(CODES.indexOf(current));
      list.focus();
    }
    function close(focus = true) {
      list.hidden = true;
      button.setAttribute("aria-expanded", "false");
      if (focus) button.focus();
    }
    function choose(code) { close(); if (code !== current) onPick(code); }
    button.addEventListener("click", () => (list.hidden ? open() : close()));
    button.addEventListener("keydown", (e) => { if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(); } });
    list.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") mark(active + 1);
      else if (e.key === "ArrowUp") mark(active - 1);
      else if (e.key === "Home") mark(0);
      else if (e.key === "End") mark(options.length - 1);
      else if (e.key === "Enter" || e.key === " ") choose(options[active].dataset.code);
      else if (e.key === "Escape" || e.key === "Tab") { close(e.key === "Escape"); return; }
      else return;
      e.preventDefault();
    });
    document.addEventListener("click", (e) => { if (!list.hidden && !wrap.contains(e.target)) close(false); });
    const menu = {
      show(code) {
        current = code;
        const l = BY[code];
        button.innerHTML = FLAGS[code];
        const name = document.createElement("span"); name.className = "lang-name"; name.lang = code; name.textContent = l.name;
        button.append(name);
        button.setAttribute("aria-label", l.name);
        options.forEach((o) => o.setAttribute("aria-selected", o.dataset.code === code));
      },
    };
    menus.push(menu);
    return menu;
  }

  // Styles live here so every page gets the same menu; colours come from each page's variables.
  const style = document.createElement("style");
  style.textContent = `
.lang-menu { position:relative; }
.lang-button { display:inline-flex; align-items:center; gap:6px; border:0; border-radius:7px; background:var(--tab); color:var(--ink); padding:4px 9px; min-height:26px; font:inherit; font-size:12.5px; font-weight:650; cursor:pointer; }
.lang-button:hover { background:var(--line); }
.lang-button::after { content:""; border:4px solid transparent; border-top-color:currentColor; margin-top:4px; }
.lang-menu svg { flex:none; border-radius:2px; box-shadow:0 0 0 1px #0002; }
.lang-list { position:absolute; inset-inline-end:0; top:calc(100% + 4px); z-index:20; list-style:none; margin:0; padding:4px; min-width:190px; background:var(--paper); color:var(--ink); border:1px solid var(--line); border-radius:9px; box-shadow:0 8px 24px #0003; outline:none; }
.lang-list li { display:flex; align-items:center; gap:9px; padding:6px 10px; border-radius:6px; cursor:pointer; font-size:14px; white-space:nowrap; }
.lang-list li:hover, .lang-list li.active { background:var(--tab); }
.lang-list li[aria-selected="true"] { font-weight:700; }
.lang-review { margin:0 0 10px; padding:6px 10px; border-radius:8px; background:var(--tab); color:var(--muted); font-size:12.5px; }
:root[dir="rtl"] td, :root[dir="rtl"] .sub { unicode-bidi:plaintext; }
`;
  document.head.append(style);

  return { LANGS, CODES, FLAGS, pick, valid, locale, apply, mount };
})();
