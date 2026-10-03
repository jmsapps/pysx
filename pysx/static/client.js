const root = document.getElementById("pysx-root");
const style = document.getElementById("pysx-style");
const socket = new WebSocket(`ws://${location.host}/ws`);

const EVENTS = ["click", "submit", "change", "input"];
// value and checked must be set as properties; setAttribute does not move an
// input the user has already interacted with.
const PROPERTIES = new Set(["value", "checked"]);

socket.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.t === "init") {
    style.textContent = message.css;
    root.innerHTML = message.html;
  } else if (message.t === "patch") {
    for (const op of message.ops) apply(op);
  }
};

function apply(op) {
  if (op.op === "text" || op.op === "html") {
    const slot = root.querySelector(`pysx-slot[id="${op.id}"]`);
    if (!slot) return;
    if (op.op === "text") slot.textContent = op.v;
    else slot.innerHTML = op.v;
    return;
  }
  if (op.op === "attr") {
    const el = root.querySelector(`[data-pysx-el="${op.id}"]`);
    if (!el) return;
    if (op.v === null) {
      el.removeAttribute(op.name);
      if (PROPERTIES.has(op.name)) el[op.name] = op.name === "checked" ? false : "";
    } else if (PROPERTIES.has(op.name)) {
      el[op.name] = op.name === "checked" ? true : op.v;
    } else {
      el.setAttribute(op.name, op.v);
    }
    return;
  }
  if (op.op === "list") reconcile(op);
}

function reconcile(op) {
  const list = root.querySelector(`pysx-list[id="${op.id}"]`);
  if (!list) return;

  const nodes = new Map();
  for (const node of [...list.children]) nodes.set(node.dataset.pysxKey, node);

  // Replace or create only the items the server actually sent.
  for (const [key, markup] of Object.entries(op.html)) {
    const template = document.createElement("template");
    template.innerHTML = markup.trim();
    const fresh = template.content.firstElementChild;
    if (!fresh) continue;
    const existing = nodes.get(key);
    if (existing) existing.replaceWith(fresh);
    nodes.set(key, fresh);
  }

  const keep = new Set(op.keys);
  for (const [key, node] of [...nodes]) {
    if (!keep.has(key)) {
      node.remove();
      nodes.delete(key);
    }
  }

  // Move surviving nodes rather than recreating them, so an untouched item
  // keeps its DOM identity (focus, selection, scroll).
  let previous = null;
  for (const key of op.keys) {
    const node = nodes.get(key);
    if (!node) continue;
    const target = previous ? previous.nextSibling : list.firstChild;
    if (node !== target) list.insertBefore(node, target);
    previous = node;
  }
}

for (const type of EVENTS) {
  document.addEventListener(type, (event) => {
    const attribute = `data-pysx-${type}`;
    const target = event.target.closest?.(`[${attribute}]`);
    if (!target) return;
    if (type === "submit") event.preventDefault();
    const payload = { t: "event", h: target.getAttribute(attribute) };
    if (type === "input" || type === "change") {
      payload.v = target.type === "checkbox" ? target.checked : target.value;
    }
    socket.send(JSON.stringify(payload));
  });
}
