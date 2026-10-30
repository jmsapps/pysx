(() => {
"use strict";

const root = document.getElementById("pysx-root");
const style = document.getElementById("pysx-style");
const browserLocation = () => location.pathname + location.search + location.hash;
const adoption = root.dataset.pysxAdoption;
let ownerHint = root.dataset.pysxOwner ?? null;
let recoveryToken = root.dataset.pysxRecovery ?? null;
const validCapsule = value => typeof value === "string" && value.length > 0 && value.length <= 36864 &&
  !/[^A-Za-z0-9_=-]/.test(value) && /^[A-Za-z0-9_-]+={0,2}$/.test(value);
const protocolVersion = root.dataset.pysxProtocol === "4" ? 4 : 3;
const capsuleDocument = root.dataset.pysxDocument ?? null;
let capsule = root.dataset.pysxCapsule ?? null;
if (capsuleDocument?.length !== 32 || !/^[A-Za-z0-9_-]{32}$/.test(capsuleDocument ?? "") ||
    !validCapsule(capsule)) capsule = null;
delete root.dataset.pysxCapsule;
delete root.dataset.pysxDocument;
delete root.dataset.pysxProtocol;
const endpoint = new URL(document.currentScript?.src ?? "/client.js", location.href);
endpoint.protocol = location.protocol === "https:" ? "wss:" : "ws:";
endpoint.pathname = endpoint.pathname.replace(/client\.js$/, "ws");
endpoint.search = `protocol=${protocolVersion}&location=${encodeURIComponent(browserLocation())}${adoption ? `&adoption=${encodeURIComponent(adoption)}` : ""}${ownerHint ? `&owner=${encodeURIComponent(ownerHint)}` : ""}`;
let socket = new WebSocket(endpoint);
let resumeToken = null;
let developing = false;
let connected = false;
let retryAttempt = 0;
let transportTimer = null;
let stopped = false;
let freshSession = false;
let restoredSession = false;
let partialReset = false;
let connectionStatus = null;
let retryButton = null;
let epoch = null;
let stateRevision = 0;
let eventSequence = 0;
let repairing = false;
let routing = false;
let navigationRevision = 0;
let observedLocation = browserLocation();
let committedLocation = observedLocation;
let historyEntry = null;
let restorePosition = null;
let previousRestoration = null;
const historyPositions = new Map();
let pendingScroll = null;
let scrollTimer = null;

function rememberPosition() {
  if (!routing || !historyEntry) return;
  const focus = root.contains(document.activeElement) ? document.activeElement.id : "";
  historyPositions.set(historyEntry, {x: scrollX, y: scrollY, focus, url: observedLocation});
  while (historyPositions.size > 128) historyPositions.delete(historyPositions.keys().next().value);
}

function newHistoryEntry() {
  const key = crypto.randomUUID();
  return {key, state: {...history.state, pysxEntry: key}};
}

function enableRouting() {
  if (routing) return;
  routing = true;
  previousRestoration = history.scrollRestoration;
  history.scrollRestoration = "manual";
  const entry = newHistoryEntry();
  history.replaceState(entry.state, "", location.href);
  historyEntry = entry.key;
}

function focusRoute(target) {
  if (!target || !root.contains(target)) return;
  if (!target.hasAttribute("tabindex")) {
    target.setAttribute("tabindex", "-1");
    target.addEventListener("blur", () => {
      if (target.getAttribute("tabindex") === "-1") target.removeAttribute("tabindex");
    }, {once: true});
  }
  target.focus({preventScroll: true});
}

function cancelRouteScroll() {
  clearTimeout(scrollTimer);
  scrollTimer = null;
  pendingScroll = null;
}

function attemptRouteScroll() {
  clearTimeout(scrollTimer);
  const plan = pendingScroll;
  if (!plan || plan.rev !== navigationRevision || browserLocation() !== plan.url) {
    cancelRouteScroll();
    return;
  }
  if (plan.restore) {
    window.scrollTo({left: plan.restore.x, top: plan.restore.y, behavior: "instant"});
    const focus = plan.restore.focus && root.querySelector(`#${CSS.escape(plan.restore.focus)}`);
    focusRoute(focus || root.querySelector("main") || root);
    cancelRouteScroll();
    return;
  }
  let id = "";
  try { id = decodeURIComponent(new URL(plan.url, location.origin).hash.slice(1)); }
  catch { /* Invalid observations are rejected by the server; no target is selected. */ }
  const target = id && (root.querySelector(`#${CSS.escape(id)}`) ?? root.querySelector(`[name="${CSS.escape(id)}"]`));
  if (target && root.contains(target)) {
    target.scrollIntoView({block: "start", behavior: "instant"});
    focusRoute(target);
    cancelRouteScroll();
    return;
  }
  if (!plan.fallback && (!id || plan.crossRoute)) {
    window.scrollTo({left: 0, top: 0, behavior: "instant"});
    focusRoute(root.querySelector("main") ?? root);
    plan.fallback = true;
  }
  if (!id || performance.now() >= plan.deadline) {
    cancelRouteScroll();
    return;
  }
  scrollTimer = setTimeout(attemptRouteScroll, 50);
}

function queueRouteScroll(url, restore = null, crossRoute = false) {
  cancelRouteScroll();
  pendingScroll = {url, rev: navigationRevision, restore, crossRoute,
    fallback: false, deadline: performance.now() + 2000};
  scrollTimer = setTimeout(attemptRouteScroll, 0);
}

function commitNavigation(message) {
  if (message.rev !== navigationRevision) return;
  if (message.mode === "external") {
    location.assign(message.url);
    return;
  }
  const crossRoute = new URL(committedLocation, location.origin).pathname !==
    new URL(message.url, location.origin).pathname;
  if (message.mode === "push" || message.mode === "replace") {
    const entry = newHistoryEntry();
    history[message.mode === "push" ? "pushState" : "replaceState"](entry.state, "", message.url);
    historyEntry = entry.key;
    restorePosition = null;
  }
  observedLocation = browserLocation();
  committedLocation = observedLocation;
  queueRouteScroll(observedLocation, restorePosition, crossRoute);
  restorePosition = null;
}

function observeLocation() {
  if (!connected) return;
  const url = browserLocation();
  if (!routing || (url === observedLocation && history.state?.pysxEntry === historyEntry)) return;
  rememberPosition();
  let key = history.state?.pysxEntry;
  if (!key || key === historyEntry) {
    const entry = newHistoryEntry();
    history.replaceState(entry.state, "", location.href);
    key = entry.key;
  }
  historyEntry = key;
  const saved = historyPositions.get(key);
  restorePosition = saved?.url === url ? saved : null;
  observedLocation = url;
  navigationRevision += 1;
  send({t: "location", url, rev: navigationRevision});
}
window.addEventListener("popstate", observeLocation);
window.addEventListener("hashchange", observeLocation);

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
    if (message.version !== protocolVersion) throw new Error("unsupported command version");
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
        if (!/^(--)?[a-z][a-z0-9-]{0,63}$/.test(args.name) || /[{}<>\\\x00]|\/\*|url\s*\(|expression\s*\(|@import|behavior\s*:|-moz-binding/i.test(args.value ?? "")) throw new Error("unsafe style");
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
          if (blockInteraction(event, true)) return;
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
  if (blockInteraction(event)) return;
  let path = composed;
  if (!event.bubbles && phase === "bubble") path = path.slice(0, 1);
  if (phase === "capture") path = [...path].reverse();
  for (const target of path) {
    const encoded = target.getAttribute(`data-pysx-policy-${event.type}`);
    if (!encoded) continue;
    const policy = JSON.parse(encoded);
    if (policy.phase !== phase || !eligible(event, target, policy)) continue;
    const navigation = policy.navigation && eligible(event, target, {...policy, link: true});
    const anchor = target.closest("a[href]");
    if (policy.navigation && !navigation && !event.defaultPrevented && event.button === 0 &&
        !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey &&
        !anchor.hasAttribute("download") && (!anchor.target || anchor.target === "_self") &&
        anchor.getAttribute("href") === location.hash && location.hash) {
      queueMicrotask(() => queueRouteScroll(browserLocation()));
    }
    if (navigation) navigationRevision += 1;
    if (policy.prevent || event.type === "submit" || navigation) event.preventDefault();
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
      if (policy.navigation) {
        payload.navigation = !!navigation;
        payload.event.value = target.closest("a[href]")?.getAttribute("href") ?? "";
      }
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
    if (blockInteraction(event)) return;
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

function registerTyped(scope) {
  for (const el of within(scope, "[data-pysx-typed]")) {
    for (const type of el.dataset.pysxTyped.split(" ")) if (type) registerEvent(type);
  }
}

function hydrate(scope) {
  registerTyped(scope);
  for (const el of within(scope, "select[value], textarea[value]")) {
    el.value = el.getAttribute("value");
    if (el.tagName === "SELECT") setSelected(el, [el.value], true);
  }
  for (const el of within(scope, "select[data-pysx-selected]")) {
    setSelected(el, JSON.parse(el.dataset.pysxSelected), true);
  }
}

function validOperation(op) {
  if (!op || typeof op !== "object" || Array.isArray(op)) return false;
  const fields = {css: ["v"], text: ["id", "v"], html: ["id", "v"],
    children: ["id", "v", "range", "kind"], attr: ["id", "name", "v", "rev"],
    prop: ["id", "name", "v", "rev"], list: ["id", "keys", "html"]};
  if (typeof op.op !== "string" || !Object.hasOwn(fields, op.op) || Object.keys(op).some(key => key !== "op" && !fields[op.op].includes(key))) return false;
  if (op.rev !== undefined && (!Number.isSafeInteger(op.rev) || op.rev < 0)) return false;
  if (op.op === "css") return typeof op.v === "string";
  if (typeof op.id !== "string" || !op.id.length) return false;
  if (op.op === "children") return typeof op.v === "string" && typeof op.range === "boolean" && ["slot", "list", "row"].includes(op.kind);
  if (["text", "html"].includes(op.op)) return typeof op.v === "string";
  if (op.op === "attr") return typeof op.name === "string" && (op.v === null || typeof op.v === "string");
  if (op.op === "prop") return op.name === "selectedValues" && Array.isArray(op.v) && op.v.every(v => typeof v === "string");
  if (op.op === "list") return Array.isArray(op.keys) && op.keys.every(v => typeof v === "string") &&
    new Set(op.keys).size === op.keys.length && op.html && typeof op.html === "object" && !Array.isArray(op.html) &&
    Object.entries(op.html).every(([key, value]) => op.keys.includes(key) && typeof value === "string");
  return false;
}

function validMessage(message) {
  if (protocolVersion === 3 && ["capsule", "document", "reset", "navigations"].some(key => key in message)) return false;
  if ("capsule" in message && (message.document !== capsuleDocument ||
      (message.capsule !== null && !validCapsule(message.capsule)) ||
      typeof message.reset !== "string" || message.reset.length > 128)) return false;
  if (("document" in message || "reset" in message) && !("capsule" in message)) return false;
  if ("navigations" in message && (!Array.isArray(message.navigations) || message.navigations.length > 1024 ||
      !message.navigations.every(command => command.t === "navigation" && validMessage(command)))) return false;
  if (message.owner !== undefined && (typeof message.owner !== "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(message.owner))) return false;
  if (message.recovery !== undefined && (typeof message.recovery !== "string" || !/^[A-Za-z0-9_-]{32,128}$/.test(message.recovery))) return false;
  if (message.recovered !== undefined && (message.t !== "init" || message.recovered !== true)) return false;
  if (message.fresh !== undefined && (message.t !== "init" || message.fresh !== true)) return false;
  if (message.fresh && message.recovered) return false;
  if (message.t === "checkpoint") return protocolVersion === 4;
  if (["init", "repair"].includes(message.t)) return typeof message.html === "string" && typeof message.css === "string" &&
    (message.t !== "repair" || (Number.isSafeInteger(message.expected) && message.expected > 0));
  if (message.t === "adopted") return typeof message.routing === "boolean";
  if (message.t === "patch") return Array.isArray(message.ops) && message.ops.every(validOperation);
  if (message.t === "result") return typeof message.h === "string" &&
    ["success", "error", "invalid", "stale"].includes(message.status) && Array.isArray(message.ops) && message.ops.every(validOperation) &&
    (message.status !== "error" || (message.error && typeof message.error === "object" &&
      typeof message.error.id === "string" && typeof message.error.message === "string" &&
      (message.error.owner === null || typeof message.error.owner === "string") &&
      ["render", "handler", "computed", "effect", "cleanup", "mount", "async"].includes(message.error.phase)));
  if (message.t === "ack") return Number.isSafeInteger(message.seq) && Number.isSafeInteger(message.expected) &&
    ["success", "duplicate", "gap", "epoch", "revision"].includes(message.status);
  if (message.t === "mount") return Array.isArray(message.ids) && message.ids.length <= 1024 && message.ids.every(id => typeof id === "string");
  if (message.t === "navigation") return typeof message.url === "string" && Number.isSafeInteger(message.rev) &&
    ["push", "replace", "observe", "external"].includes(message.mode);
  if (message.t === "routing") return typeof message.on === "boolean";
  if (message.t === "dom") return ["id", "target", "root", "op"].every(key => typeof message[key] === "string") &&
    message.args && typeof message.args === "object" && !Array.isArray(message.args);
  return false;
}

function receiveMessage(event) {
  try { acceptMessage(event); }
  catch { capsule = null; socket.close(4002, "frame application failed"); }
}

function protocolFailure(reason) {
  capsule = null;
  socket.close(4002, reason);
}

function acceptMessage(event) {
  let message;
  try { message = JSON.parse(event.data); } catch { protocolFailure("invalid JSON"); return; }
  if (!message || typeof message !== "object" || Array.isArray(message) || message.version !== protocolVersion ||
      typeof message.epoch !== "string" || !Number.isSafeInteger(message.state_rev) ||
      !Number.isSafeInteger(message.base_rev) || message.state_rev !== message.base_rev + 1 ||
      !["init", "adopted", "result", "patch", "dom", "mount", "navigation", "routing", "ack", "repair", "checkpoint"].includes(message.t) ||
      message.base_rev < 0 || !validMessage(message)) {
    protocolFailure("invalid envelope"); return;
  }
  if (message.t === "init" && (message.recovered === true || message.fresh === true)) {
    if (!recoveryToken && !capsule && !developing) { protocolFailure("unexpected reconstruction"); return; }
    resetSession(message.recovered === true);
    partialReset = message.recovered === true && Boolean(message.reset);
  }
  if (["init", "adopted"].includes(message.t)) {
    if (message.development === true) developing = true;
    if (epoch !== null) { protocolFailure("repeated attachment"); return; }
    epoch = message.epoch;
  } else if (message.epoch !== epoch) { protocolFailure("wrong epoch"); return; }
  if (message.t === "repair" && message.state_rev < stateRevision) return;
  if (message.t !== "repair" && !["init", "adopted"].includes(message.t) && message.base_rev !== stateRevision) {
    if (!repairing) { repairing = true; send({t: "repair"}); }
    return;
  }
  stateRevision = message.state_rev;
  if (typeof message.resume === "string") resumeToken = message.resume;
  if (typeof message.recovery === "string") recoveryToken = message.recovery;
  if (typeof message.owner === "string") ownerHint = message.owner;
  if (message.t === "repair") {
    if (message.routing) enableRouting();
    if (Number.isSafeInteger(message.nav_rev)) navigationRevision = message.nav_rev;
    const parser = document.createRange(); parser.selectNodeContents(root);
    const fresh = parser.createContextualFragment(message.html);
    hydrate(fresh);
    preserveInteraction(() => morphChildren(root, [...root.childNodes], [...fresh.childNodes], null, true));
    style.textContent = message.css;
    registerTyped(root); syncDom();
    eventSequence = message.expected - 1;
    repairing = false;
    root.dataset.pysxAttached = "true";
    attachmentReady();
    window.dispatchEvent(new CustomEvent("pysx:repair"));
  } else if (message.t === "ack") {
    if (["gap", "epoch", "revision"].includes(message.status)) repairing = true;
  } else
  if (message.t === "init") {
    if (message.routing) enableRouting();
    style.textContent = message.css;
    if (message.recovered === true) {
      const parser = document.createRange(); parser.selectNodeContents(root);
      const fresh = parser.createContextualFragment(message.html);
      hydrate(fresh);
      preserveInteraction(() => morphChildren(root, [...root.childNodes], [...fresh.childNodes], null, true, true));
    } else {
      root.innerHTML = message.html;
      hydrate(root);
    }
    root.dataset.pysxAttached = "true";
    attachmentReady();
    syncDom();
    if (routing) queueRouteScroll(browserLocation());
  } else if (message.t === "adopted") {
    if (message.routing) enableRouting();
    registerTyped(root);
    syncDom();
    delete root.dataset.pysxAdoption;
    root.dataset.pysxAttached = "true";
    attachmentReady();
    if (routing) queueRouteScroll(browserLocation());
  } else if (message.t === "result") {
    for (const op of message.ops) apply(op);
    syncDom();
    if (pendingScroll) attemptRouteScroll();
    window.dispatchEvent(new CustomEvent("pysx:result", {detail: {
      handler: message.h, status: message.status, error: message.error ?? null,
    }}));
  } else if (message.t === "patch") {
    for (const op of message.ops) apply(op);
    syncDom();
    if (pendingScroll) attemptRouteScroll();
  } else if (message.t === "dom") {
    domCommand(message);
  } else if (message.t === "mount") {
    send({t: "mounted", ids: message.ids});
  } else if (message.t === "navigation") {
    commitNavigation(message);
  } else if (message.t === "routing") {
    if (message.on) enableRouting();
  }
  for (const navigation of message.navigations ?? []) commitNavigation(navigation);
  if ("capsule" in message) capsule = message.capsule;
}

function openSocket() {
  const recovery = {...(recoveryToken ? {recovery: recoveryToken} : {}),
    ...(protocolVersion === 4 ? {document: capsuleDocument, ...(capsule ? {capsule} : {})} : {})};
  if (resumeToken) {
    socket.send(JSON.stringify({t: "hello", version: protocolVersion, resume: resumeToken, epoch, state_rev: stateRevision, ...recovery}));
    return;
  }
  if (!adoption || freshSession || endpoint.searchParams.get("recover") === "1") {
    socket.send(JSON.stringify({t: "hello", version: protocolVersion, edits: [], ...recovery})); return;
  }
  // HTTP controls already have their browser values; never hydrate over edits.
  const values = [...root.querySelectorAll("[data-pysx-binding]")]
    .filter(el => {
      if (el.dataset.pysxBind === "radio") return el.checked && !el.defaultChecked;
      if (el.dataset.pysxBind === "checked") return el.checked !== el.defaultChecked;
      if (el.tagName === "SELECT") {
        let defaults = [...el.options].filter(option => option.defaultSelected).map(option => option.value);
        if (!el.multiple) {
          const first = [...el.options].find(option => !option.disabled && !option.closest("optgroup[disabled]"));
          defaults = defaults.length ? [defaults.at(-1)] : first ? [first.value] : [""];
        }
        const current = el.multiple ? bindingValue(el) : [el.value];
        return JSON.stringify(current) !== JSON.stringify(defaults);
      }
      return el.value !== el.defaultValue;
    }).map(edit);
  socket.send(JSON.stringify({t: "hello", version: protocolVersion, edits: values, ...recovery}));
}

function clearTransportTimer() {
  clearTimeout(transportTimer);
  transportTimer = null;
}

// Built-in connection UI belongs only to development sessions.
function connectionElements() {
  if (connectionStatus !== null) return;
  connectionStatus = document.createElement("p");
  connectionStatus.id = "pysx-connection-status";
  connectionStatus.setAttribute("role", "status");
  retryButton = document.createElement("button");
  retryButton.type = "button";
  retryButton.textContent = "Retry connection";
  retryButton.hidden = true;
  retryButton.addEventListener("click", () => reconnect());
  root.after(connectionStatus, retryButton);
}

function connectionState(state, retryMs = 0) {
  root.dataset.pysxConnection = state;
  const hidden = !developing;
  const text = state === "connected" ? (restoredSession ? (partialReset ?
    "Connected. Session state was restored; some fields restarted with their defaults and uncommitted edits were discarded." :
    "Connected. Session state was restored; uncommitted edits were discarded.") :
    freshSession ? "Connected. A fresh session started; previous session state was lost." : "Connected.") :
    state === "reconnecting" ? "Disconnected. Reconnecting; offline edits are rejected." :
    state === "fresh" ? "Previous session expired. Starting a fresh session." :
    state === "stopped" ? "Disconnected by a protocol failure. Offline edits are rejected. Reload the page to continue." :
    "Disconnected. Offline edits are rejected. Retry to connect.";
  if (!hidden) connectionElements();

  if (connectionStatus !== null) {
    connectionStatus.hidden = hidden;
    connectionStatus.textContent = text;
    retryButton.hidden = state !== "disconnected";
  }
  window.dispatchEvent(new CustomEvent("pysx:connection", {detail: {
    state, attempt: retryAttempt, retryMs, fresh: freshSession, partialReset,
    timers: transportTimer === null ? 0 : 1,
    listeners: registeredEvents.size * 2 + domListeners.size,
    pendingBytes: socket.bufferedAmount,
  }}));
}

function attachmentReady() {
  connected = true;
  clearTransportTimer();
  retryAttempt = 0;
  connectionState("connected");
}

function rejectInteraction() {
  window.dispatchEvent(new CustomEvent("pysx:rejected", {detail: {reason: "disconnected"}}));
}

function blockInteraction(event, owned = false) {
  if ((connected && !repairing) || root.dataset.pysxAttached === undefined) return false;
  if (!owned && (!(event.target instanceof Node) || !root.contains(event.target))) return false;
  if (event.cancelable) event.preventDefault();
  rejectInteraction();
  return true;
}

function resetSession(restored = false) {
  resumeToken = null; epoch = null; stateRevision = 0; eventSequence = 0;
  recoveryToken = null;
  capsule = null;
  partialReset = false;
  repairing = false; freshSession = !restored; restoredSession = restored;
  for (const listener of domListeners.values()) listener.target.removeEventListener(listener.type, listener.fn, listener.capture);
  domListeners.clear(); domNodes.clear();
  for (const [type, listeners] of registeredEvents) {
    window.removeEventListener(type, listeners.capture, true);
    window.removeEventListener(type, listeners.bubble);
  }
  registeredEvents.clear();
  for (const control of root.querySelectorAll("[data-pysx-binding]")) edits.delete(control);
  if (previousRestoration !== null) history.scrollRestoration = previousRestoration;
  routing = false; navigationRevision = 0;
  historyPositions.clear();
  observedLocation = browserLocation(); committedLocation = observedLocation;
}

function scheduleReconnect() {
  if (stopped || transportTimer !== null) return;
  if (retryAttempt >= 8) { connectionState("disconnected"); return; }
  const ceiling = Math.min(10000, 250 * 2 ** retryAttempt++);
  const delay = Math.round(ceiling * (0.5 + Math.random() * 0.5));
  transportTimer = setTimeout(() => { transportTimer = null; reconnect(false); }, delay);
  connectionState("reconnecting", delay);
}

function closeSocket(event) {
  connected = false;
  clearTransportTimer();
  cancelRouteScroll();
  root.dataset.pysxAttached = "false";
  window.dispatchEvent(new CustomEvent("pysx:disconnected"));
  if (stopped) return;
  if (developing && event.code === 4000 && event.reason === "development reload") {
    retryAttempt = 0;
    reconnect(false);
    return;
  }
  if (event.code === 4010 && /^[A-Za-z0-9_-]{1,64}$/.test(event.reason)) ownerHint = event.reason;
  if (event.code === 4004 || event.code === 4009 || (event.code === 1008 && epoch === null)) {
    resetSession();
    connectionState("fresh");
  } else if ([4002, 1002, 1008, 1009].includes(event.code)) {
    capsule = null;
    stopped = true;
    connectionState("stopped");
    return;
  }
  scheduleReconnect();
}

function attachSocket() {
  const owner = socket;
  socket.onmessage = event => { if (socket === owner) receiveMessage(event); };
  socket.addEventListener("open", () => { if (socket === owner) openSocket(); });
  socket.addEventListener("close", event => { if (socket === owner) closeSocket(event); });
  clearTransportTimer();
  transportTimer = setTimeout(() => {
    transportTimer = null;
    if (socket === owner && !connected) owner.close(4000, "attachment deadline");
  }, 5000);
}

function reconnect(manual = true) {
  if (socket.readyState !== WebSocket.CLOSED || stopped) return;
  clearTransportTimer();
  if (manual) retryAttempt = 0;
  endpoint.search = `protocol=${protocolVersion}${resumeToken ? "&resume=1" : recoveryToken || capsule ? "&recover=1" : ""}&location=${encodeURIComponent(browserLocation())}${ownerHint ? `&owner=${encodeURIComponent(ownerHint)}` : ""}`;
  socket = new WebSocket(endpoint);
  attachSocket();
  connectionState(freshSession ? "fresh" : "reconnecting");
}

attachSocket();
window.addEventListener("pysx:reconnect", () => reconnect());
let resetting = false;
window.addEventListener("pysx:reset", async () => {
  if (resetting) return;
  resetting = true; stopped = true;
  clearTransportTimer();
  resetSession();
  socket.close();
  const target = new URL(endpoint);
  target.protocol = location.protocol;
  target.pathname = target.pathname.replace(/ws$/, "__pysx__/reset");
  target.search = "";
  try {
    const response = await fetch(target, {method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"x-pysx-reset": "1"}, signal: AbortSignal.timeout(5000)});
    if (response.ok) location.reload();
    else connectionState("stopped");
  } catch { connectionState("stopped"); }
  finally { resetting = false; }
});
window.addEventListener("pagehide", () => {
  stopped = true;
  clearTransportTimer();
  cancelRouteScroll();
  socket.close();
});
window.addEventListener("pageshow", event => {
  if (event.persisted) { stopped = false; reconnect(false); }
});

function apply(op) {
  if (op.op === "css") { style.textContent = op.v; return; }
  if (op.op === "children") {
    const range = op.range ? ownedRange(op.kind, op.id) : null;
    const element = op.range ? null : root.querySelector(`[data-pysx-el="${CSS.escape(op.id)}"], pysx-slot[id="${CSS.escape(op.id)}"]`);
    if (!range && !element) return;
    const parser = document.createRange();
    if (range) parser.setStartBefore(range.end);
    else parser.selectNodeContents(element);
    const fresh = parser.createContextualFragment(op.v);
    preserveInteraction(() => morphChildren(range ? range.start.parentNode : element, range ? rangeNodes(range) : [...element.childNodes], [...fresh.childNodes], range?.end ?? null));
    hydrate(range ? range.start.parentElement : element);
    return;
  }
  if (op.op === "text" || op.op === "html") {
    const slot = root.querySelector(`pysx-slot[id="${CSS.escape(op.id)}"]`);
    if (slot) {
      if (op.op === "text") slot.textContent = op.v;
      else { slot.innerHTML = op.v; hydrate(slot); }
    } else {
      const range = ownedRange("slot", op.id);
      if (!range) return;
      clearRange(range);
      range.end.before(op.op === "text" ? document.createTextNode(op.v) : contextual(range, op.v));
      hydrate(range.start.parentElement);
    }
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
    if (op.name.startsWith("data-pysx-")) registerTyped(el);
    return;
  }
  if (op.op === "list") preserveInteraction(() => reconcileRanges(op));
}

function hex(value) {
  return [...new TextEncoder().encode(value)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function rowPath(slot, key) {
  const segment = /^[a-zA-Z0-9_]+$/.test(key) ? key : "~" + hex(key);
  return `${slot}:${segment}:`;
}

function ownedRange(kind, id) {
  const token = `pysx:${kind}:${hex(id)}:`;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_COMMENT);
  let start = null;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.data === token + "start") start = node;
    else if (start && node.data === token + "end") return {start, end: node};
  }
  return null;
}

function rangeNodes(range, inclusive = false) {
  const nodes = [];
  for (let node = inclusive ? range.start : range.start.nextSibling;
       node && node !== range.end; node = node.nextSibling) nodes.push(node);
  if (inclusive) nodes.push(range.end);
  return nodes;
}

function clearRange(range) { for (const node of rangeNodes(range)) node.remove(); }

function contextual(range, markup) {
  const parser = document.createRange();
  parser.setStartBefore(range.end);
  return parser.createContextualFragment(markup);
}

function nodeGroups(nodes) {
  const groups = [];
  for (let index = 0; index < nodes.length; index++) {
    const node = nodes[index];
    const match = node.nodeType === Node.COMMENT_NODE && /^pysx:(slot|list|row):([a-f0-9]+):start$/.exec(node.data);
    if (match) {
      const end = `pysx:${match[1]}:${match[2]}:end`;
      const members = [node];
      while (++index < nodes.length) {
        members.push(nodes[index]);
        if (nodes[index].nodeType === Node.COMMENT_NODE && nodes[index].data === end) break;
      }
      groups.push({key: `range:${match[1]}:${match[2]}`, kind: match[1], nodes: members});
    } else {
      const id = node.nodeType === Node.ELEMENT_NODE && (node.getAttribute("data-pysx-el") || (node.tagName.toLowerCase() === "pysx-slot" && node.id));
      groups.push({key: id ? `element:${id}` : null, nodes: [node]});
    }
  }
  return groups;
}

function compatible(old, fresh) {
  if (old.nodeType !== fresh.nodeType) return false;
  if (old.nodeType !== Node.ELEMENT_NODE) return true;
  if (old.localName !== fresh.localName || old.namespaceURI !== fresh.namespaceURI) return false;
  const token = old.getAttribute("data-pysx-ref");
  const nextToken = fresh.getAttribute("data-pysx-ref");
  return token === nextToken;
}

function morphNode(old, fresh, complete = false, resetDefaults = false) {
  if (old.nodeType !== Node.ELEMENT_NODE) {
    if (old.data !== fresh.data) old.data = fresh.data;
    return;
  }
  const formState = resetDefaults ? {
    value: fresh.value, checked: fresh.checked, selected: fresh.selected,
    defaultValue: fresh.defaultValue,
    options: fresh.tagName === "SELECT" ? [...fresh.options].map(option => option.selected) : null,
  } : null;
  const incoming = new Map([...fresh.attributes].map(attr => [attr.name, attr.value]));
  const controlled = new Set([...old.attributes].filter(attr => PROPERTIES.has(attr.name)).map(attr => attr.name));
  for (const attr of [...old.attributes]) if (!incoming.has(attr.name) && !PROPERTIES.has(attr.name)) old.removeAttribute(attr.name);
  for (const [name, value] of incoming) {
    if (PROPERTIES.has(name)) {
      if (resetDefaults) old.setAttribute(name, value);
      if (name === "value") setValue(old, value);
      else old[name] = true;
    } else if (old.getAttribute(name) !== value) old.setAttribute(name, value);
  }
  for (const name of controlled) if (!incoming.has(name)) {
    if (resetDefaults) old.removeAttribute(name);
    if (name === "value") { if (!resetDefaults) setValue(old, ""); }
    else old[name] = false;
  }
  if (!old.hasAttribute("data-pysx-imperative")) morphChildren(old, [...old.childNodes], [...fresh.childNodes], null, complete, resetDefaults);
  if (formState) {
    if (old.tagName === "INPUT") { setValue(old, formState.value); old.checked = formState.checked; }
    if (old.tagName === "TEXTAREA") { old.defaultValue = formState.defaultValue; setValue(old, formState.value); }
    if (old.tagName === "OPTION") old.selected = formState.selected;
    if (formState.options) for (let index = 0; index < old.options.length; index++) old.options[index].selected = formState.options[index];
  }
  if (complete && old.tagName === "SELECT") {
    if (fresh.hasAttribute("data-pysx-selected")) setSelected(old, JSON.parse(fresh.dataset.pysxSelected));
    else if (fresh.hasAttribute("value")) setSelected(old, [fresh.getAttribute("value")]);
  }
}

function morphChildren(parent, previousNodes, nextNodes, boundary = null, complete = false, resetDefaults = false) {
  const oldGroups = nodeGroups(previousNodes);
  const freshGroups = nodeGroups(nextNodes);
  const keyed = new Map(oldGroups.filter(group => group.key).map(group => [group.key, group]));
  const used = new Set();
  const resulting = [];
  for (let index = 0; index < freshGroups.length; index++) {
    const fresh = freshGroups[index];
    let old = fresh.key ? keyed.get(fresh.key) : oldGroups[index];
    if (old && (used.has(old) || old.key !== fresh.key || !compatible(old.nodes[0], fresh.nodes[0]))) old = null;
    if (old) {
      used.add(old);
      if (old.kind) {
        if (old.kind !== "list" || complete) morphChildren(parent, old.nodes.slice(1, -1), fresh.nodes.slice(1, -1), old.nodes.at(-1), complete, resetDefaults);
        // Nested reconciliation may have changed the live range's length.
        const members = [];
        for (let node = old.nodes[0]; node; node = node.nextSibling) {
          members.push(node);
          if (node === old.nodes.at(-1)) break;
        }
        resulting.push(...members);
      } else { morphNode(old.nodes[0], fresh.nodes[0], complete, resetDefaults); resulting.push(old.nodes[0]); }
    } else resulting.push(...fresh.nodes);
  }
  for (const old of oldGroups) if (!used.has(old)) for (const node of old.nodes) node.remove();
  let target = boundary;
  for (let index = resulting.length - 1; index >= 0; index--) {
    const node = resulting[index];
    if (node.nextSibling !== target || node.parentNode !== parent) parent.insertBefore(node, target);
    target = node;
  }
}

function preserveInteraction(change) {
  const active = document.activeElement;
  const caret = active && typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd, active.selectionDirection] : null;
  const selection = window.getSelection();
  const selected = selection?.rangeCount ? [selection.anchorNode, selection.anchorOffset, selection.focusNode, selection.focusOffset] : null;
  const scroll = [];
  for (let node = active; node instanceof Element; node = node.parentElement) scroll.push([node, node.scrollLeft, node.scrollTop]);
  change();
  if (active?.isConnected && document.activeElement !== active) active.focus({preventScroll: true});
  if (caret && active.isConnected && typeof active.selectionStart === "number") {
    active.setSelectionRange(Math.min(caret[0], active.value.length), Math.min(caret[1], active.value.length), caret[2]);
  }
  if (!caret && selected && selected[0]?.isConnected && selected[2]?.isConnected) {
    const extent = node => node.nodeType === Node.TEXT_NODE ? node.data.length : node.childNodes.length;
    selection.setBaseAndExtent(selected[0], Math.min(selected[1], extent(selected[0])), selected[2], Math.min(selected[3], extent(selected[2])));
  }
  for (const [node, left, top] of scroll) if (node.isConnected) { node.scrollLeft = left; node.scrollTop = top; }
}

function reconcileRanges(op) {
  const list = ownedRange("list", op.id);
  if (!list) return;
  const nodes = new Map();
  const walker = document.createTreeWalker(list.start.parentNode, NodeFilter.SHOW_COMMENT);
  const prefix = `pysx:row:${hex(op.id + ":")}`;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.data.startsWith(prefix) && node.data.endsWith(":start")) {
      const token = node.data.slice("pysx:row:".length, -":start".length);
      const path = new TextDecoder().decode(Uint8Array.from(token.match(/../g), byte => parseInt(byte, 16)));
      // Only rows directly owned by this list belong in this reconciliation.
      const segment = path.slice(op.id.length + 1, -1);
      if (segment.includes(":")) continue;
      const key = segment.startsWith("~") ? new TextDecoder().decode(Uint8Array.from(segment.slice(1).match(/../g) ?? [], byte => parseInt(byte, 16))) : segment;
      nodes.set(key, ownedRange("row", path));
    }
  }
  for (const [key, markup] of Object.entries(op.html)) {
    const existing = nodes.get(key);
    if (existing) for (const node of rangeNodes(existing, true)) node.remove();
    list.end.before(contextual(list, markup));
    nodes.set(key, ownedRange("row", rowPath(op.id, key)));
  }
  const keep = new Set(op.keys);
  for (const [key, range] of nodes) {
    if (!keep.has(key)) {
      for (const node of rangeNodes(range, true)) node.remove();
      nodes.delete(key);
    }
  }
  let target = list.start.nextSibling;
  for (const key of op.keys) {
    const range = nodes.get(key);
    if (!range) continue;
    if (range.start !== target) {
      for (const node of rangeNodes(range, true)) target.before(node);
    }
    target = range.end.nextSibling;
  }
  hydrate(list.start.parentElement);
}

function resyncBindings(form) {
  return [...form.elements].filter(el =>
    el.dataset?.pysxBinding && !el.disabled &&
    (el.dataset.pysxBind !== "radio" || el.checked)).map(edit);
}

// The browser restores defaults after dispatch, so the resynchronised bindings
// and the handler snapshot travel together in one message on the next tick.
function resetEvent(event, handler, typed = true) {
  if (blockInteraction(event)) return;
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !root.contains(form)) return;
  const snap = typed ? snapshot(event, handler) : null;
  setTimeout(() => {
    if (blockInteraction(event)) return;
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
  if (!connected || (repairing && ["event", "location", "mounted"].includes(payload.t))) {
    if (["event", "location"].includes(payload.t)) rejectInteraction();
    return;
  }
  if (routing && payload.t === "event") rememberPosition();
  if (routing && payload.t === "event") payload.nav_rev = navigationRevision;
  if (socket.readyState === WebSocket.OPEN && epoch) {
    if (["event", "location", "mounted"].includes(payload.t)) payload.seq = ++eventSequence;
    payload.version = protocolVersion; payload.epoch = epoch; payload.state_rev = stateRevision;
    const encoded = JSON.stringify(payload);
    if (new TextEncoder().encode(encoded).length + socket.bufferedAmount > 65536) {
      connected = false;
      rejectInteraction();
      socket.close(4009, "outbound queue limit");
      return;
    }
    socket.send(encoded);
  }
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
  if (blockInteraction(event)) return;
  if (event.target.dataset?.pysxBinding) editState(event.target).composing = true;
});

document.addEventListener("compositionend", event => {
  if (blockInteraction(event)) return;
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
    if (blockInteraction(event)) return;
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

})();
