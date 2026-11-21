// Theme switch shared by every page: System → Light → Dark, one button.
// Loaded synchronously in <head> so the chosen theme applies before the page paints.
(() => {
  const ORDER = ["system", "light", "dark"];
  const read = () => { try { const v = localStorage.getItem("theme"); return ORDER.includes(v) ? v : "system"; } catch { return "system"; } };
  const save = (v) => { try { localStorage.setItem("theme", v); } catch {} };
  const apply = (mode) => {
    if (mode === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = mode;
  };
  let mode = read();
  apply(mode);

  const NAMES = {
    ro: { system: "sistem", light: "luminoasă", dark: "întunecată", label: "Temă: {mode} (clic pentru {next})" },
    hu: { system: "rendszer", light: "világos", dark: "sötét", label: "Téma: {mode} (kattintson: {next})" },
    en: { system: "system", light: "light", dark: "dark", label: "Theme: {mode} (click for {next})" },
  };
  const svg = (body) => `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
  const ICONS = {
    // Half-filled circle: follows the operating system.
    system: svg('<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/>'),
    light: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
    dark: svg('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
  };

  function render() {
    const names = NAMES[document.documentElement.lang] || NAMES.en;
    const next = ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];
    const label = names.label.replace("{mode}", names[mode]).replace("{next}", names[next]);
    for (const button of document.querySelectorAll("button.theme-toggle")) {
      button.innerHTML = ICONS[mode];
      button.title = label;
      button.setAttribute("aria-label", label);
      button.dataset.mode = mode;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    for (const button of document.querySelectorAll("button.theme-toggle")) {
      button.addEventListener("click", () => {
        mode = ORDER[(ORDER.indexOf(mode) + 1) % ORDER.length];
        save(mode);
        apply(mode);
        render();
      });
    }
    render();
    // The pages switch language by changing <html lang>; keep the label in step.
    new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
  });
  // Another tab changed the theme.
  window.addEventListener("storage", (event) => { if (event.key === "theme") { mode = read(); apply(mode); render(); } });
})();
