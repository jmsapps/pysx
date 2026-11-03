// This control interrupts the actual transport; all state still belongs to the server.
(() => {
  "use strict";
  const root = document.getElementById("pysx-root");
  const update = () => {
    const state = root.dataset.pysxConnection;
    const status = document.getElementById("recovery-connection");
    const button = document.getElementById("recovery-disconnect");
    const message = state === "connected" ? "Connected" :
      state === "reconnecting" ? "Disconnected. Reconnecting…" :
      state ? `Connection: ${state}` : "Connecting…";
    if (status && status.textContent !== message) status.textContent = message;
    if (button) button.disabled = state !== "connected";
  };
  window.addEventListener("pysx:connection", update);
  // Lesson navigation and reset insert new controls without reloading the document.
  new MutationObserver(update).observe(root, {childList: true, subtree: true});
  update();
  document.addEventListener("click", event => {
    if (!(event.target instanceof Element) || !event.target.closest("#recovery-disconnect")) return;
    window.dispatchEvent(new CustomEvent("pysx:disconnect", {detail: {durationMs: 5000}}));
  });
})();
