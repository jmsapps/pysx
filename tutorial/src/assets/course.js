// This control interrupts the actual transport; all state still belongs to the server.
(() => {
  "use strict";
  const root = document.getElementById("pysx-root");
  const positions = new Map();
  const articleKey = () => {
    const article = document.querySelector("#lesson-main article");
    return article?.dataset.lesson ?? article?.dataset.page ?? null;
  };
  let currentArticle = articleKey();
  let traversing = false;
  window.addEventListener("popstate", () => { traversing = true; });
  document.addEventListener("scroll", event => {
    if (event.target.id === "lesson-main" && currentArticle === articleKey()) {
      positions.set(currentArticle, event.target.scrollTop);
    }
  }, true);
  const updateScroll = () => {
    const next = articleKey();
    if (!next || next === currentArticle) return;
    const main = document.getElementById("lesson-main");
    if (matchMedia("(min-width: 851px)").matches && !location.hash) {
      main.scrollTo({top: traversing ? positions.get(next) ?? 0 : 0, behavior: "instant"});
    }
    currentArticle = next;
    traversing = false;
  };
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
  new MutationObserver(() => { update(); updateScroll(); })
    .observe(root, {childList: true, subtree: true});
  update();
  document.addEventListener("click", event => {
    if (event.target instanceof Element && event.target.closest("a[href]")) traversing = false;
    if (!(event.target instanceof Element) || !event.target.closest("#recovery-disconnect")) return;
    window.dispatchEvent(new CustomEvent("pysx:disconnect", {detail: {durationMs: 5000}}));
  });
})();
