const root = document.getElementById("pysx-root");
const style = document.getElementById("pysx-style");
const socket = new WebSocket(`ws://${location.host}/ws`);

const EVENTS = ["click", "submit", "reset", "change", "input", "invalid"];
// value and checked must be set as properties; setAttribute does not move an
// input the user has already interacted with.
const PROPERTIES = new Set(["value", "checked", "selected", "muted"]);
const edits = new WeakMap();

function editState(el) {
  if (!edits.has(el)) edits.set(el, { rev: 0, composing: false, pending: null, lastSent: null });
  return edits.get(el);
}

function setValue(el, value) {
  if (el.value === value) return;
  const start = el.selectionStart;
  const end = el.selectionEnd;
  const direction = el.selectionDirection;
  el.value = value;
  if (start !== null && end !== null && typeof el.setSelectionRange === "function") {
    el.setSelectionRange(Math.min(start, value.length), Math.min(end, value.length), direction);
  }
}

function setSelected(el, values, defaults = false) {
  const selected = new Set(values);
  for (const option of el.options) {
    option.selected = selected.has(option.value);
    if (defaults) option.defaultSelected = option.selected;
  }
}

function hydrate(scope) {
  for (const el of scope.querySelectorAll("select[value], textarea[value]")) {
    el.value = el.getAttribute("value");
    if (el.tagName === "SELECT") setSelected(el, [el.value], true);
  }
  for (const el of scope.querySelectorAll("select[data-pysx-selected]")) {
    setSelected(el, JSON.parse(el.dataset.pysxSelected), true);
  }
}

socket.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.t === "init") {
    style.textContent = message.css;
    root.innerHTML = message.html;
    hydrate(root);
  } else if (message.t === "patch") {
    for (const op of message.ops) apply(op);
  }
};

function apply(op) {
  if (op.op === "text" || op.op === "html") {
    const slot = root.querySelector(`pysx-slot[id="${CSS.escape(op.id)}"]`);
    if (!slot) return;
    if (op.op === "text") slot.textContent = op.v;
    else { slot.innerHTML = op.v; hydrate(slot); }
    return;
  }
  if (op.op === "attr" || op.op === "prop") {
    const el = root.querySelector(`[data-pysx-el="${CSS.escape(op.id)}"]`);
    if (!el) return;
    const state = editState(el);
    if (op.rev !== undefined && op.rev !== state.rev) return;
    if (state.composing && op.name === "value") { state.pending = op; return; }
    state.lastSent = null;
    if (op.op === "prop") { setSelected(el, op.v); return; }
    if (PROPERTIES.has(op.name)) {
      if (op.name === "value") setValue(el, op.v ?? "");
      else el[op.name] = op.v !== null;
    } else if (op.v === null) el.removeAttribute(op.name);
    else el.setAttribute(op.name, op.v);
    return;
  }
  if (op.op === "list") reconcile(op);
}

function reconcile(op) {
  const list = root.querySelector(`pysx-list[id="${CSS.escape(op.id)}"]`);
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
    hydrate(fresh);
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

function bindingValue(el) {
  if (el.dataset.pysxBind === "checked") return el.checked;
  if (el.dataset.pysxBind === "selected") return [...el.selectedOptions].map(option => option.value);
  return el.value;
}

function edit(el) {
  const state = editState(el);
  state.rev += 1;
  const value = bindingValue(el);
  state.lastSent = JSON.stringify(value);
  return { h: el.dataset.pysxBinding, v: value, rev: state.rev };
}

function send(payload) {
  if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
}

function submitPayload(form, submitter = null) {
  const data = new FormData(form, submitter);
  return {
    entries: [...data.entries()].map(([name, value]) => [name, typeof value === "string" ? value :
      { name: value.name, size: value.size, type: value.type }]),
    valid: form.checkValidity(),
    submitter: submitter ? { name: submitter.name, value: submitter.value } : null,
  };
}

document.addEventListener("compositionstart", event => {
  if (event.target.dataset?.pysxBinding) editState(event.target).composing = true;
});

document.addEventListener("compositionend", event => {
  const el = event.target;
  if (!el.dataset?.pysxBinding) return;
  const state = editState(el);
  state.composing = false;
  const payload = { t: "event", ...edit(el) };
  const handler = el.getAttribute("data-pysx-input");
  if (handler && handler !== payload.h) payload.after = handler;
  send(payload);
  if (state.pending) { const pending = state.pending; state.pending = null; apply(pending); }
});

for (const type of EVENTS) {
  document.addEventListener(type, (event) => {
    const bound = event.target.closest?.("[data-pysx-binding]");
    if ((type === "input" || type === "change") && bound?.dataset.pysxBindEvent === type) {
      if (bound.disabled || editState(bound).composing || event.isComposing) return;
      if (bound.dataset.pysxBind === "radio" && !bound.checked) return;
      if (JSON.stringify(bindingValue(bound)) === editState(bound).lastSent) return;
      const payload = { t: "event", ...edit(bound) };
      const handler = bound.getAttribute(`data-pysx-${type}`);
      if (handler && handler !== payload.h) payload.after = handler;
      send(payload);
      return;
    }
    const attribute = `data-pysx-${type}`;
    const target = event.target.closest?.(`[${attribute}]`);
    if (type === "reset") {
      const form = event.target;
      if (form.tagName !== "FORM" || event.defaultPrevented) return;
      setTimeout(() => {
        if (event.defaultPrevented || !root.contains(form)) return;
        const updates = [...form.elements].filter(el =>
          el.dataset?.pysxBinding && !el.disabled &&
          (el.dataset.pysxBind !== "radio" || el.checked)).map(edit);
        send({ t: "event", h: target?.getAttribute(attribute) ?? "", edits: updates,
          v: submitPayload(form) });
      }, 0);
      return;
    }
    if (!target) return;
    if (target.matches(":disabled")) return;
    if (type === "submit") event.preventDefault();
    const payload = { t: "event", h: target.getAttribute(attribute) };
    if (type === "submit") {
      if (!target.checkValidity() && !target.noValidate && !event.submitter?.formNoValidate) return;
      payload.v = submitPayload(target, event.submitter);
    }
    if (type === "input" || type === "change") {
      payload.v = target.type === "checkbox" ? target.checked : target.value;
    }
    if (type === "invalid") payload.v = { value: target.value, valid: target.validity.valid };
    send(payload);
  }, type === "invalid");
}
