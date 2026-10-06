const root = document.getElementById("pysx-root");
const style = document.getElementById("pysx-style");
const socket = new WebSocket(`ws://${location.host}/ws`);

const EVENTS = [
  "abort", "afterprint", "animationend", "animationiteration", "animationstart", "auxclick",
  "beforeinput", "beforeprint", "beforeunload", "blur", "cancel", "canplay", "canplaythrough",
  "change", "click", "close", "compositionend", "compositionstart", "compositionupdate",
  "contextmenu", "copy", "cuechange", "cut", "dblclick", "drag", "dragend", "dragenter",
  "dragleave", "dragover", "dragstart", "drop", "durationchange", "emptied", "ended", "error",
  "focus", "focusin", "focusout", "formdata", "fullscreenchange", "gotpointercapture",
  "hashchange", "input", "invalid", "keydown", "keypress", "keyup", "load", "loadeddata",
  "loadedmetadata", "loadstart", "lostpointercapture", "mousedown", "mouseenter", "mouseleave",
  "mousemove", "mouseout", "mouseover", "mouseup", "mousewheel", "paste", "pause", "play",
  "playing", "pointercancel", "pointerdown", "pointerenter", "pointerleave", "pointermove",
  "pointerout", "pointerover", "pointerup", "popstate", "progress", "ratechange", "reset",
  "resize", "scroll", "scrollend", "securitypolicyviolation", "seeked", "seeking", "select",
  "selectionchange", "selectstart", "show", "slotchange", "stalled", "submit", "suspend",
  "timeupdate", "toggle", "touchcancel", "touchend", "touchmove", "touchstart", "transitionend",
  "unload", "volumechange", "waiting", "wheel",
];
// value and checked must be set as properties; setAttribute does not move an
// input the user has already interacted with.
const PROPERTIES = new Set(["value", "checked", "selected", "muted"]);
const edits = new WeakMap();
const registeredEvents = new Map();
const domNodes = new Map();
const domListeners = new Map();

function syncDom() {
  for (const [type, listeners] of registeredEvents) {
    if (!root.querySelector(`[data-pysx-policy-${CSS.escape(type)}]`)) {
      window.removeEventListener(type, listeners.capture, true);
      window.removeEventListener(type, listeners.bubble);
      registeredEvents.delete(type);
    }
  }
  for (const el of root.querySelectorAll("[data-pysx-ref]")) {
    const token = el.dataset.pysxRef;
    if (!domNodes.has(token)) domNodes.set(token, {node: el, root: token, detached: false});
  }
  for (const [token, entry] of domNodes) {
    const owner = domNodes.get(entry.root)?.node;
    if (!owner?.isConnected || !root.contains(owner) || owner.dataset.pysxRef !== entry.root ||
        (!entry.detached && entry.node !== owner && !owner.contains(entry.node))) {
      domNodes.delete(token);
    }
  }
  for (const [hid, listener] of domListeners) {
    if (!domNodes.has(listener.token)) {
      listener.target.removeEventListener(listener.type, listener.fn, listener.capture);
      domListeners.delete(hid);
    }
  }
}

function nodeToken(node, owner, detached = false) {
  if (!node) return null;
  const zone = domNodes.get(owner)?.node;
  if (!detached && node !== zone && !zone?.contains(node)) return null;
  for (const [token, entry] of domNodes) {
    if (entry.node === node && entry.root === owner) return token;
  }
  if (domNodes.size >= 256) throw new Error("node limit exceeded");
  const token = crypto.randomUUID().replaceAll("-", "");
  domNodes.set(token, {node, root: owner, detached});
  return token;
}

function domCommand(message) {
  try {
    syncDom();
    const originalTokens = [...domNodes.keys()];
    if (message.version !== 1) throw new Error("unsupported command version");
    const entry = domNodes.get(message.target);
    const owner = domNodes.get(message.root)?.node;
    if (!entry || entry.root !== message.root || !owner) throw new Error("stale DOM handle");
    const el = entry.node, args = message.args;
    const mutations = new Set(["create", "create_text", "create_fragment", "insert", "remove", "set_attr", "set_prop", "style"]);
    if (mutations.has(message.op) && owner.dataset.pysxImperative !== "true") {
      throw new Error("mutation requires an imperative zone");
    }
    let value = null;
    switch (message.op) {
      case "focus": el.focus({preventScroll: args.prevent_scroll}); break;
      case "blur": el.blur(); break;
      case "active": value = nodeToken(document.activeElement, message.root); break;
      case "query": value = nodeToken(el.querySelector(args.selector), message.root); break;
      case "get_by_id": value = nodeToken(el.id === args.id ? el : el.querySelector(`#${CSS.escape(args.id)}`), message.root); break;
      case "get_attr": value = el.getAttribute(args.name); break;
      case "set_attr": {
        if (!/^[a-z][a-z0-9:-]{0,63}$/.test(args.name) || /^(on|data-pysx-|style$|srcdoc$)/.test(args.name)) throw new Error("unsafe attribute");
        if (["href", "src", "action", "formaction", "xlink:href"].includes(args.name) && args.value !== null &&
            !["http:", "https:"].includes(new URL(args.value, location.href).protocol)) throw new Error("unsafe URL");
        if (args.value === null) el.removeAttribute(args.name); else el.setAttribute(args.name, args.value);
        break;
      }
      case "get_prop": case "set_prop": {
        if (!["value", "checked", "selected", "disabled", "tabIndex", "textContent"].includes(args.name)) throw new Error("unsupported property");
        if (message.op === "get_prop") value = el[args.name] ?? null;
        else el[args.name] = args.value;
        break;
      }
      case "node_prop": {
        if (!["parentNode", "firstChild", "nextSibling"].includes(args.name)) throw new Error("unsupported node property");
        value = nodeToken(el[args.name], message.root); break;
      }
      case "style": {
        if (!/^(--)?[a-z][a-z0-9-]{0,63}$/.test(args.name) || /url\s*\(|expression\s*\(/i.test(args.value ?? "")) throw new Error("unsafe style");
        if (args.value === null) el.style.removeProperty(args.name); else el.style.setProperty(args.name, args.value);
        break;
      }
      case "selection": el.setSelectionRange(args.start, args.end, args.direction); break;
      case "read_selection": value = [el.selectionStart, el.selectionEnd, el.selectionDirection]; break;
      case "scroll": el.scrollTo(args.x, args.y); break;
      case "measure": { const r = el.getBoundingClientRect(); value = [r.x, r.y, r.width, r.height]; break; }
      case "create": {
        if (!/^[a-z][a-z0-9-]{0,63}$/.test(args.tag) || ["script", "iframe", "object", "embed", "style", "link", "meta", "base"].includes(args.tag)) throw new Error("unsafe element");
        const namespaces = {html: "http://www.w3.org/1999/xhtml", svg: "http://www.w3.org/2000/svg", math: "http://www.w3.org/1998/Math/MathML"};
        if (!namespaces[args.namespace]) throw new Error("invalid namespace");
        value = nodeToken(document.createElementNS(namespaces[args.namespace], args.tag), message.root, true); break;
      }
      case "create_text": value = nodeToken(document.createTextNode(args.value), message.root, true); break;
      case "create_fragment": value = nodeToken(document.createDocumentFragment(), message.root, true); break;
      case "insert": {
        const child = domNodes.get(args.child), before = args.before ? domNodes.get(args.before) : null;
        if (!child || child.root !== message.root || (args.before && (!before || before.root !== message.root)) ||
            child.node === owner || (before && before.node.parentNode !== el)) throw new Error("foreign insertion");
        const moved = child.node.nodeType === Node.DOCUMENT_FRAGMENT_NODE ? [...child.node.childNodes] : [child.node];
        el.insertBefore(child.node, before?.node ?? null);
        for (const item of domNodes.values()) if (moved.some(node => node === item.node || node.contains(item.node))) item.detached = !owner.contains(item.node);
        break;
      }
      case "remove": {
        if (el.parentNode) el.parentNode.removeChild(el);
        for (const [token, item] of domNodes) if (item.node === el || el.contains(item.node)) domNodes.delete(token);
        break;
      }
      case "listen": {
        if (!(el instanceof Element)) throw new Error("listeners require an element ref");
        if (domListeners.size >= 128 || domListeners.has(args.h)) throw new Error("listener limit exceeded");
        const policy = args.policy, target = args.window ? window : el;
        const fn = event => {
          syncDom();
          if (!domNodes.has(message.target) || !eligible(event, el, policy)) return;
          if (policy.prevent) event.preventDefault();
          if (policy.stop) event.stopPropagation();
          send({t: "event", h: args.h, event: snapshot(event, args.h)});
        };
        const capture = policy.phase === "capture";
        target.addEventListener(policy.type, fn, capture);
        domListeners.set(args.h, {token: message.target, target, type: policy.type, fn, capture});
        break;
      }
      case "unlisten": {
        const item = domListeners.get(args.h);
        if (item && item.token === message.target) {
          item.target.removeEventListener(item.type, item.fn, item.capture);
          domListeners.delete(args.h);
        }
        break;
      }
      default: throw new Error("unsupported DOM operation");
    }
    syncDom();
    const revoked = originalTokens.filter(token => !domNodes.has(token));
    const reply = {t: "dom_reply", version: 1, id: message.id, value, revoked};
    if (JSON.stringify(reply).length > 16384) throw new Error("reply exceeds limit");
    send(reply);
  } catch (error) {
    send({t: "dom_reply", version: 1, id: message.id, error: String(error.message).slice(0, 256)});
  }
}

function snapshot(event, handler) {
  const target = event.target;
  return {
    type: event.type, handler, target: target?.id ?? "", value: String(target?.value ?? ""),
    checked: !!target?.checked, key: event.key ?? "", code: event.code ?? "",
    alt: !!event.altKey, ctrl: !!event.ctrlKey, meta: !!event.metaKey, shift: !!event.shiftKey,
    repeat: !!event.repeat, composing: !!event.isComposing,
    button: event.button ?? 0, buttons: event.buttons ?? 0,
    x: event.clientX ?? 0, y: event.clientY ?? 0,
    pointer_id: event.pointerId ?? 0, pointer_type: event.pointerType ?? "",
    related_target: event.relatedTarget?.id ?? "", submitter: event.submitter?.id ?? "",
  };
}

function eligible(event, target, policy) {
  if (target.matches(":disabled")) return false;
  if (policy.keys.length && !policy.keys.includes(event.key)) return false;
  if (!policy.link) return true;
  const anchor = target.closest("a[href]");
  const href = anchor?.getAttribute("href").trim() ?? "";
  if (!href || href.startsWith("#") || /^([a-z][a-z0-9+.-]*:|\/\/)/i.test(href)) return false;
  return !!anchor && event.button === 0 && !event.altKey && !event.ctrlKey &&
    !event.metaKey && !event.shiftKey && !event.defaultPrevented &&
    !anchor.hasAttribute("download") && (!anchor.target || anchor.target === "_self") &&
    new URL(anchor.href, location.href).origin === location.origin;
}

function typedEvents(event, phase, composed) {
  let path = composed;
  if (!event.bubbles && phase === "bubble") path = path.slice(0, 1);
  if (phase === "capture") path = [...path].reverse();
  for (const target of path) {
    const encoded = target.getAttribute(`data-pysx-policy-${event.type}`);
    if (!encoded) continue;
    const policy = JSON.parse(encoded);
    if (policy.phase !== phase || !eligible(event, target, policy)) continue;
    if (policy.prevent || event.type === "submit") event.preventDefault();
    if (policy.stop) event.stopPropagation();
    const handler = target.getAttribute(`data-pysx-${event.type}`);
    if (event.type === "reset") {
      resetEvent(event, handler);
      if (policy.stop) break;
      continue;
    }
    const bound = target.dataset.pysxBinding && target.dataset.pysxBindEvent === event.type;
    if (bound) {
      const quiet = target.disabled || editState(target).composing || event.isComposing ||
        (target.dataset.pysxBind === "radio" && !target.checked);
      if (!quiet && JSON.stringify(bindingValue(target)) !== editState(target).lastSent) {
        const payload = {t: "event", ...edit(target), after: handler, event: snapshot(event, handler)};
        send(payload);
      }
    } else {
      const payload = { t: "event", h: handler, event: snapshot(event, handler) };
      if (event.type === "submit") {
        if (!target.checkValidity() && !target.noValidate && !event.submitter?.formNoValidate) return;
        payload.v = submitPayload(target, event.submitter);
      }
      send(payload);
    }
    if (policy.stop) break;
  }
}

function customEventCount() {
  let count = 0;
  for (const type of registeredEvents.keys()) if (!EVENTS.includes(type)) count += 1;
  return count;
}

function composedTargets(event) {
  return event.composedPath().filter(el => el instanceof Element && root.contains(el));
}

function registerEvent(type) {
  if (registeredEvents.has(type)) return;
  if (!EVENTS.includes(type) && customEventCount() >= 128) {
    throw new Error("custom event limit exceeded");
  }
  const capture = event => {
    const path = composedTargets(event);
    if (!path.length) return;
    typedEvents(event, "capture", path);
    if (!event.bubbles && !event.cancelBubble) typedEvents(event, "bubble", path);
  };
  const bubble = event => {
    const path = composedTargets(event);
    if (path.length) typedEvents(event, "bubble", path);
  };
  registeredEvents.set(type, {capture, bubble});
  window.addEventListener(type, capture, true);
  window.addEventListener(type, bubble);
}

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

function within(scope, selector) {
  const found = [...scope.querySelectorAll(selector)];
  if (scope.matches?.(selector)) found.unshift(scope);
  return found;
}

function hydrate(scope) {
  for (const el of within(scope, "[data-pysx-typed]")) {
    for (const type of el.dataset.pysxTyped.split(" ")) if (type) registerEvent(type);
  }
  for (const el of within(scope, "select[value], textarea[value]")) {
    el.value = el.getAttribute("value");
    if (el.tagName === "SELECT") setSelected(el, [el.value], true);
  }
  for (const el of within(scope, "select[data-pysx-selected]")) {
    setSelected(el, JSON.parse(el.dataset.pysxSelected), true);
  }
}

socket.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.t === "init") {
    style.textContent = message.css;
    root.innerHTML = message.html;
    hydrate(root);
    syncDom();
  } else if (message.t === "patch") {
    for (const op of message.ops) apply(op);
    syncDom();
  } else if (message.t === "dom") {
    domCommand(message);
  } else if (message.t === "mount") {
    socket.send(JSON.stringify({t: "mounted", ids: message.ids}));
  }
};

socket.addEventListener("close", () => {
  for (const listener of domListeners.values()) listener.target.removeEventListener(listener.type, listener.fn, listener.capture);
  domListeners.clear(); domNodes.clear();
  for (const [type, listeners] of registeredEvents) {
    window.removeEventListener(type, listeners.capture, true);
    window.removeEventListener(type, listeners.bubble);
  }
  registeredEvents.clear();
});

function apply(op) {
  if (op.op === "css") { style.textContent = op.v; return; }
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

function resyncBindings(form) {
  return [...form.elements].filter(el =>
    el.dataset?.pysxBinding && !el.disabled &&
    (el.dataset.pysxBind !== "radio" || el.checked)).map(edit);
}

// The browser restores defaults after dispatch, so the resynchronised bindings
// and the handler snapshot travel together in one message on the next tick.
function resetEvent(event, handler, typed = true) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !root.contains(form)) return;
  const snap = typed ? snapshot(event, handler) : null;
  setTimeout(() => {
    if (!root.contains(form)) return;
    const prevented = event.defaultPrevented;
    if (prevented && !typed) return;
    const payload = { t: "event", h: handler ?? "", edits: prevented ? [] : resyncBindings(form),
      v: submitPayload(form) };
    if (snap) payload.event = snap;
    send(payload);
  }, 0);
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
  if (el.hasAttribute("data-pysx-policy-input")) payload.event = {...snapshot(event, handler), type: "input"};
  send(payload);
  if (state.pending) { const pending = state.pending; state.pending = null; apply(pending); }
});

for (const type of EVENTS) {
  window.addEventListener(type, (event) => {
    if (event.cancelBubble) return;
    const bound = event.target.closest?.("[data-pysx-binding]");
    if ((type === "input" || type === "change") && bound?.dataset.pysxBindEvent === type) {
      if (bound.hasAttribute(`data-pysx-policy-${type}`)) return;
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
    const target = event.target instanceof Element
      ? event.target.closest(`[${attribute}]`)
      : root.querySelector(`[${attribute}]`);
    if (type === "reset") {
      if (target?.hasAttribute(`data-pysx-policy-${type}`)) return;
      resetEvent(event, target?.getAttribute(attribute) ?? "", false);
      return;
    }
    if (!target) return;
    if (target.hasAttribute(`data-pysx-policy-${type}`)) return;
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
  }, true);
}
