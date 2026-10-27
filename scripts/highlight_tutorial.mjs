// Generate browser code spans using the same grammar injection as the editor.
import {spawnSync} from "node:child_process";
import {readFileSync, writeFileSync} from "node:fs";
import {createRequire} from "node:module";
import {fileURLToPath} from "node:url";
import {pylanceHost} from "../tests/pylance_host.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(new URL("../tests/package.json", import.meta.url));
const textmate = require("vscode-textmate");
const oniguruma = require("vscode-oniguruma");
const args = process.argv.slice(2);
if (args.some(arg => arg !== "--check")) throw new Error("expected --check or no arguments");
const exported = spawnSync("uv", ["run", "--project", root, "python", "-c", `
import json,sys
sys.path.insert(0, 'tutorial')
from pysx.compiler.loader import install_loader
install_loader(packages=('tutorial',))
from tutorial.catalog import LESSONS
from tutorial.demos.foundations import CARD_SOURCES
from tutorial.demos.operations import FROZEN_SOURCE, ATTRIBUTE_SOURCE, CORRECT_SOURCE
print(json.dumps([lesson.code for lesson in LESSONS] + list(CARD_SOURCES) +
    [FROZEN_SOURCE, FROZEN_SOURCE.replace('count()', 'count'), ATTRIBUTE_SOURCE, CORRECT_SOURCE]))
`], {cwd: root, encoding: "utf8"});
if (exported.status !== 0) throw new Error(exported.stderr);
const host = pylanceHost();
const wasm = readFileSync(require.resolve("vscode-oniguruma/release/onig.wasm"));
await oniguruma.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));
const injection = fileURLToPath(new URL("../editor/syntaxes/pysx.injection.tmLanguage.json", import.meta.url));
const read = filename => textmate.parseRawGrammar(readFileSync(filename, "utf8"), filename);
const registry = new textmate.Registry({
  onigLib: Promise.resolve({createOnigScanner: sources => new oniguruma.OnigScanner(sources), createOnigString: source => new oniguruma.OnigString(source)}),
  loadGrammar: async scope => scope === "source.python" ? read(host.grammarPath) : scope === "source.pysx.injection" ? read(injection) : null,
  getInjections: scope => scope === "source.python" ? ["source.pysx.injection"] : undefined,
});
const kind = scopes => {
  if (scopes.includes("entity.name.tag.pysx")) return "tag";
  if (scopes.includes("support.class.component.pysx")) return "component";
  if (scopes.includes("entity.other.attribute-name.pysx")) return "attribute";
  if (scopes.some(scope => scope.startsWith("comment"))) return "comment";
  if (scopes.some(scope => scope.startsWith("keyword") || scope.startsWith("storage.type"))) return "keyword";
  if (scopes.some(scope => scope.startsWith("constant.numeric"))) return "number";
  if (scopes.includes("meta.embedded.inline.python")) return "expression";
  if (scopes.some(scope => scope.startsWith("string"))) return "string";
  return "plain";
};
try {
  const grammar = await registry.loadGrammar("source.python");
  const output = {};
  for (const source of new Set(JSON.parse(exported.stdout))) {
    let stack = textmate.INITIAL;
    const parts = [];
    const lines = source.split("\n");
    for (const [index, line] of lines.entries()) {
      const result = grammar.tokenizeLine(line, stack);
      for (const token of result.tokens) {
        const text = line.slice(token.startIndex, token.endIndex);
        if (text) parts.push([kind(token.scopes), text]);
      }
      if (index < lines.length - 1) parts.push(["plain", "\n"]);
      stack = result.ruleStack;
    }
    if (parts.map(part => part[1]).join("") !== source) throw new Error("highlighting changed source text");
    output[source] = parts;
  }
  const filename = fileURLToPath(new URL("../tutorial/src/assets/code-tokens.json", import.meta.url));
  const serialized = JSON.stringify(output) + "\n";
  if (args.includes("--check")) {
    if (readFileSync(filename, "utf8") !== serialized) throw new Error("stale tutorial highlighting; run node scripts/highlight_tutorial.mjs");
  } else writeFileSync(filename, serialized);
  console.log(`Tutorial highlighting ${args.includes("--check") ? "checked" : "generated"}: ${Object.keys(output).length} sources; Pylance ${host.version}; first-party pysx injection`);
} finally {registry.dispose();}
