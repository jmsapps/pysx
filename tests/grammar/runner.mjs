import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import textmate from "vscode-textmate";
import oniguruma from "vscode-oniguruma";
import { pylanceHost } from "../pylance_host.mjs";

const args = process.argv.slice(2);
let suite = "injection";
for (let i = 0; i < args.length; i += 2) {
  if (args[i] !== "--suite" || !args[i + 1]) throw new Error("invalid selectors");
  suite = args[i + 1];
}
if (suite !== "injection") throw new Error("empty grammar selection");
const host = pylanceHost();
const injectionPath = process.env.PYSX_INJECTION_GRAMMAR ??
  fileURLToPath(new URL("../../editor/syntaxes/pysx.injection.tmLanguage.json", import.meta.url));
const require = createRequire(import.meta.url);
const wasm = readFileSync(require.resolve("vscode-oniguruma/release/onig.wasm"));
await oniguruma.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));
const read = (filename) => textmate.parseRawGrammar(readFileSync(filename, "utf8"), filename);
const registry = new textmate.Registry({
  onigLib: Promise.resolve({
    createOnigScanner: (sources) => new oniguruma.OnigScanner(sources),
    createOnigString: (source) => new oniguruma.OnigString(source),
  }),
  loadGrammar: async (scope) => scope === "source.python" ? read(host.grammarPath) :
    scope === "source.pysx.injection" ? read(injectionPath) : null,
  getInjections: (scope) => scope === "source.python" ? ["source.pysx.injection"] : undefined,
});
try {
  const grammar = await registry.loadGrammar("source.python");
  if (!grammar) throw new Error("missing host grammar");
  let assertions = 0, tokens = 0;
  const check = (condition, label) => {
    if (!condition) throw new Error(`scope assertion failed: ${label}`);
    assertions++;
  };
  for (const fixture of ["counter", "unclosed_paren", "odd_quote"]) {
    const lines = readFileSync(new URL(`fixtures/${fixture}.txt`, import.meta.url), "utf8").split("\n");
    let stack = textmate.INITIAL;
    const result = [];
    for (const [index, line] of lines.entries()) {
      const current = grammar.tokenizeLine(line, stack);
      for (const token of current.tokens) result.push({ line: index, text: line.slice(token.startIndex, token.endIndex), scopes: token.scopes });
      stack = current.ruleStack;
    }
    tokens += result.length;
    // Anchoring on the sentinel line would leave the t-string terminator and the
    // blank line after it unchecked, which is exactly where leakage surfaces.
    const sentinel = result.find((token) => token.text.includes("AFTER_SENTINEL"));
    const terminator = result.findIndex((token, index) => index > 0 &&
      token.text === '"""' && result[index - 1].scopes.some((scope) => scope.includes("pysx")));
    check(sentinel && terminator > 0 && sentinel.line > result[terminator].line &&
      !result.slice(terminator).some((token) => token.scopes.some((scope) => scope.includes("pysx"))),
      `${fixture}: no scope leakage`);
    if (fixture === "counter") {
      for (const [value, scope] of [["Page", "support.class.component.pysx"], ["onClick", "entity.other.attribute-name.pysx"]]) {
        check(result.some((token) => token.text === value && token.scopes.includes(scope)), `${value}: ${scope}`);
      }
      check(result.some((token) => token.text.trim() === "count" && token.scopes.some((scope) => scope.includes("meta.embedded.inline.python"))), "hole body returns to Python");
    }
  }
  const injection = JSON.parse(readFileSync(injectionPath, "utf8"));
  console.log(`Pylance ${host.version}; host ${host.grammarPath}; sha256 ${host.sha256}; injection ${injection.scopeName}`);
  console.log(`GRAMMAR VERIFICATION PASSED: ${suite} (3 fixtures, ${assertions} assertions, ${tokens} tokens)`);
} finally { registry.dispose(); }
