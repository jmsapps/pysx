;(() => {
  if (window.pysxExampleStatePanel) return;
  window.pysxExampleStatePanel = true;
  function render(status) {
    const panel = document.getElementById("pysx-dev-state");
    if (!panel) return;
    const reset = Array.isArray(status.reset) ? status.reset.slice(0, 64) : [];
    panel.querySelector("#pysx-dev-state-text").textContent =
      `Workspace: preserved ${status.preserved ?? 0} | carried ${status.carried_slots ?? 0}` +
      ` | dropped ${status.dropped_slots ?? 0} | reset ${reset.length}` +
      (status.bounded ? ` | cold reset: ${status.bounded}` : "");
    const list = panel.querySelector("#pysx-dev-state-resets");
    list.replaceChildren();
    for (const entry of reset) {
      const item = document.createElement("li");
      item.textContent = `${entry.slot}: ${entry.reason} `;
      if (entry.source?.uri?.startsWith("vscode://file/")) {
        const link = document.createElement("a");
        link.href = entry.source.uri;
        link.textContent = `${entry.source.file}:${entry.source.line}`;
        item.append(link);
      }
      list.append(item);
    }
  }
  async function refresh() {
    try {
      const response = await fetch("/__pysx_dev__/status", {cache: "no-store"});
      if (response.ok) render(await response.json());
    } catch (_) { /* Keep the last report while the supervisor is unavailable. */ }
  }
  addEventListener("pysx:development", event => render(event.detail));
  addEventListener("pysx:connection", event => {
    if (event.detail.state === "connected") void refresh();
  });
  document.addEventListener("click", async event => {
    if (!(event.target instanceof Element) || !event.target.closest("#pysx-dev-cold")) return;
    event.preventDefault();
    try {
      const response = await fetch("/__pysx_dev__/cold", {cache: "no-store"});
      if (response.ok) location.reload();
    } catch (_) { /* A failed discard leaves the current application usable. */ }
  });
  void refresh();
})();
