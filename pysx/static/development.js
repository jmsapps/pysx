;(() => {
  const script = document.currentScript;
  if (!script || new URL(script.src).searchParams.get("pysx-inspect") !== "1") return;
  const panel = document.createElement("details");
  panel.id = "pysx-inspector";
  panel.style.cssText = "position:fixed;bottom:0;right:0;z-index:2147483647;max-height:60vh;overflow:auto;width:min(44rem,100vw);background:#fff;color:#171717;border:1px solid #888;padding:.6rem;font:13px monospace";
  const summary = document.createElement("summary"); summary.textContent = "pysx inspector";
  const refresh = document.createElement("button"); refresh.type = "button"; refresh.textContent = "Refresh inspector";
  const content = document.createElement("div"); content.id = "pysx-inspector-content";
  panel.append(summary, refresh, content); document.body.append(panel);
  let stopped = false, timer = null, loading = false;
  addEventListener("pagehide", () => { stopped = true; clearTimeout(timer); }, {once: true});
  const text = (parent, tag, value) => { const node = document.createElement(tag); node.textContent = value; parent.append(node); return node; };
  const link = (parent, source, label) => {
    const node = text(parent, "a", label);
    if (source?.uri?.startsWith("vscode://file/")) node.href = source.uri;
  };
  async function load() {
    if (loading || stopped || !panel.open) return;
    loading = true;
    try {
      const response = await fetch("/__pysx_dev__/inspect", {cache: "no-store"});
      content.replaceChildren();
      if (!response.ok) { text(content, "p", response.status === 403 ? "Inspector access denied" : "Inspector unavailable"); return; }
      const data = await response.json();
      for (const session of data.sessions) {
        text(content, "h3", `Session ${session.id}`);
        text(content, "p", `${session.owners} owners · ${session.resources} resources · ${session.tasks} tasks · ${session.handlers} handlers · ${session.dropped} events dropped`);
        text(content, "pre", JSON.stringify(session.counts, null, 2));
        const events = document.createElement("ol"); events.dataset.pysxTraceEvents = "true";
        for (const event of session.events) {
          const item = text(events, "li", `${event.sequence} ${event.kind} ${event.owner} ${event.value} `);
          if (event.source) link(item, event.source, `${event.source.file}:${event.source.line}`);
        }
        content.append(events);
      }
      text(content, "h3", "Source diagnostics");
      if (data.analysis.error) text(content, "p", data.analysis.error);
      if (data.analysis.pending) text(content, "p", "Analyzing current source…");
      for (const file of data.analysis.files ?? []) for (const diagnostic of file.diagnostics) {
        const row = text(content, "p", `${diagnostic.severity} ${diagnostic.code}: ${diagnostic.message} `);
        const source = {...file.source, uri: file.source.uri.replace(/:\d+$/, `:${diagnostic.line + 1}`)};
        link(row, source, `${file.source.file}:${diagnostic.line + 1}:${diagnostic.startChar + 1}`);
      }
    } catch (_) { content.replaceChildren(); text(content, "p", "Inspector unavailable"); }
    finally {
      loading = false;
      if (!stopped && panel.open) timer = setTimeout(load, 1000);
    }
  }
  panel.addEventListener("toggle", () => { clearTimeout(timer); if (panel.open) void load(); });
  refresh.addEventListener("click", () => { clearTimeout(timer); void load(); });
})();
