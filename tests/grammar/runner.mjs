import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import textmate from "vscode-textmate";
import oniguruma from "vscode-oniguruma";
import { pylanceHost } from "../pylance_host.mjs";

const args = process.argv.slice(2);
let suite = null;
for (let i = 0; i < args.length; i += 2) {
  if (args[i] !== "--suite" || !args[i + 1]) throw new Error("invalid selectors");
  suite = args[i + 1];
}
if (suite && !["injection", "styling_diagnostics", "composition_recursive", "styled_authoring", "render_snapshot", "templates_example", "inline_siblings", "identity_cleanup"].includes(suite)) throw new Error("empty grammar selection");
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
  const fixtures = suite === "identity_cleanup" ? ["keyed_identity"] :
    suite === "templates_example" ? ["templates_example"] :
    suite === "inline_siblings" ? ["inline_siblings"] :
    suite === "render_snapshot" ? ["render_snapshot"] :
    suite === "styled_authoring" ? ["styled_authoring"] :
    suite === "composition_recursive" ? ["composition"] :
    suite === "styling_diagnostics" ? ["styling"] :
    suite === "injection" ? ["counter", "unclosed_paren", "odd_quote"] :
    ["counter", "unclosed_paren", "odd_quote", "styling", "composition", "styled_authoring", "render_snapshot", "templates_example", "inline_siblings", "keyed_identity"];
  for (const fixture of fixtures) {
    const source = fixture === "templates_example" ? "../../examples/templates.py" : `fixtures/${fixture}.txt`;
    const lines = readFileSync(new URL(source, import.meta.url), "utf8").split("\n");
    let stack = textmate.INITIAL;
    const result = [];
    for (const [index, line] of lines.entries()) {
      const current = grammar.tokenizeLine(line, stack);
      for (const token of current.tokens) result.push({ line: index, start: token.startIndex, end: token.endIndex, text: line.slice(token.startIndex, token.endIndex), scopes: token.scopes });
      stack = current.ruleStack;
    }
    tokens += result.length;
    if (fixture === "keyed_identity") {
      for (const word of ["table", "tbody", "tr", "td", "input", "span", "select", "option", "svg", "text"]) check(result.some(token => token.text === word && token.scopes.includes("entity.name.tag.pysx")), `keyed native ${word}`);
      for (const word of ["in", "key"]) check(result.some(token => token.text.trim() === word && token.scopes.includes("keyword.control.loop.pysx")), `keyed loop ${word}`);
      check(result.some(token => token.text.trim() === "for" && token.scopes.includes("keyword.control.conditional.pysx")), "keyed for keyword");
      for (const word of ["ref", "bindValue", "value"]) check(result.some(token => token.text === word && token.scopes.includes("entity.other.attribute-name.pysx")), `keyed attribute ${word}`);
      for (const word of ["rows", "draft", "children", "field"]) check(result.some(token => token.text === word && token.scopes.includes("meta.embedded.inline.python")), `keyed Python ${word}`);
      const sentinel = result.find(token => token.text.includes("AFTER_SENTINEL"));
      check(sentinel && !sentinel.scopes.some(scope => scope.includes("pysx")), "keyed scopes do not leak");
      continue;
    }
    if (fixture === "inline_siblings") {
      const at = (marker, word, occurrence = 0) => {
        const row = lines.findIndex(line => line.includes(marker));
        check(row >= 0, `row exists: ${marker}`);
        let column = -1;
        for (let i = 0; i <= occurrence; i++) column = lines[row].indexOf(word, column + 1);
        check(column >= 0, `occurrence exists: ${word}/${occurrence}`);
        return result.find(token => token.line === row && token.start <= column && token.end > column);
      };
      const scoped = (marker, word, occurrence, scope) => check(at(marker, word, occurrence)?.scopes.includes(scope), `${marker}: ${word}/${occurrence}: ${scope}`);
      scoped('h2: "Live"', "h2", 0, "entity.name.tag.pysx");
      for (const occurrence of [0, 1]) scoped('h2: "Live"', "Break", occurrence, "support.class.component.pysx");
      for (const occurrence of [0, 1, 2]) scoped('h2: "Live"', ";", occurrence, "punctuation.terminator.pysx");
      scoped('h2: "Live"', ":", 0, "punctuation.section.block.pysx");
      for (const occurrence of [0, 1]) scoped("br;; br;", "br", occurrence, "entity.name.tag.pysx");
      scoped('Icon(name=', "Icon", 0, "support.class.component.pysx");
      scoped('Icon(name=', "name", 0, "entity.other.attribute-name.pysx");
      scoped('Icon(name=', ";", 0, "string.quoted.double.pysx");
      scoped('Icon(name=', "span", 0, "entity.name.tag.pysx");
      scoped('"Label";', "br", 0, "entity.name.tag.pysx");
      scoped('"Label";', "span", 0, "entity.name.tag.pysx");
      for (const occurrence of [0, 1]) scoped("p: {value};", "Component", occurrence, "meta.embedded.inline.python");
      scoped("p: {value};", "other", 0, "meta.embedded.inline.python");
      scoped('title="quoted;', "br", 0, "string.quoted.double.pysx");
      scoped('onClick={', "handler", 0, "meta.embedded.inline.python");
      scoped("); br", "br", 0, "entity.name.tag.pysx");
      scoped('escaped', "br", 0, "string.quoted.double.pysx");
      scoped('escaped', "Card", 0, "string.quoted.double.pysx");
      scoped('escaped', "br", 1, "entity.name.tag.pysx");
      for (const name of ["_lower", "lower"]) scoped("_lower; lower", name, name === "lower" ? 1 : 0, "entity.name.tag.pysx");
      scoped('inline =', "br", 0, "entity.name.tag.pysx");
      scoped('inline =', "Break", 0, "support.class.component.pysx");
      scoped('inline =', "br", 1, "entity.name.tag.pysx");
      const sentinel = at("AFTER_SENTINEL", "AFTER_SENTINEL");
      check(sentinel && !sentinel.scopes.some(scope => scope.includes("pysx") || scope.includes("function-call")), "inline siblings do not leak scopes");
      continue;
    }
    if (fixture === "render_snapshot") {
      for (const word of ["in", "key"]) {
        check(result.some(token => token.text === word && token.scopes.includes("keyword.control.loop.pysx")), `${fixture}: ${word}`);
      }
      for (const word of ["let", "set"]) {
        const row = lines.findIndex(line => new RegExp(`^\\s*${word}\\s+`).test(line));
        check(result.some(token => token.line === row && token.text.trim() === word && token.scopes.includes("keyword.control.conditional.pysx")), `${fixture}: ${word} keyword`);
        const name = lines[row].match(/(?:let|set)\s+([\w-]+)/)[1];
        check(result.some(token => token.line === row && token.text === name && token.scopes.includes("variable.other.readwrite.pysx")), `${fixture}: ${word} binding`);
        check(result.some(token => token.line === row && token.text === "=" && token.scopes.includes("keyword.operator.assignment.pysx")), `${fixture}: ${word} assignment`);
      }
      check(result.some(token => token.text === "=" && token.scopes.includes("keyword.operator.assignment.pysx") && /\bkey\s*=/.test(lines[token.line])), `${fixture}: key assignment`);
    }
    // Anchoring on the sentinel line would leave the t-string terminator and the
    // blank line after it unchecked, which is exactly where leakage surfaces.
    const sentinel = result.find((token) => token.text.includes("AFTER_SENTINEL"));
    const terminator = result.findIndex((token, index) => index > 0 &&
      token.text === '"""' && result[index - 1].scopes.some((scope) => scope.includes("pysx")));
    if (fixture === "templates_example") {
      for (const word of ["if", "elif", "else", "match", "case"]) {
        check(result.some(token => token.text.trim() === word && token.scopes.includes("keyword.control.conditional.pysx")), `example ${word}`);
      }
      for (const word of ["TemplatePage", "Action", "GroupPanel", "GroupHeading", "Break"]) {
        check(result.some(token => token.text === word && token.scopes.includes("support.class.component.pysx")), `example bare ${word}`);
      }
      check(result.some(token => token.text === "title" && token.scopes.includes("entity.other.attribute-name.pysx")), "example multiline attrs");
      check(result.some(token => token.text.trim() === "tooltip" && token.scopes.includes("meta.embedded.inline.python")), "example Python attr");
      check(result.some(token => token.text.trim() === "snapshots" && token.scopes.includes("meta.embedded.inline.python")), "example snapshot hole");
      check(result.some(token => token.text === "each_indexed" && token.scopes.includes("meta.embedded.inline.python")), "example inline helper");
      check(result.some(token => token.text === "when" && !token.scopes.some(scope => scope.includes("pysx") || scope.includes("css"))), "example Python branch helper");
      const inlineRow = lines.findIndex(line => line.includes('GroupHeading(id="live-heading")'));
      check(inlineRow >= 0, "example inline heading row exists");
      const breaks = result.filter(token => token.line === inlineRow && token.text === "Break");
      check(breaks.length === 2 && breaks.every(token => token.scopes.includes("support.class.component.pysx")), "both styled break sibling occurrences");
      check(result.some(token => token.line === inlineRow && token.text === "Action" && token.scopes.includes("support.class.component.pysx")), "later sibling action tag");
      const handlerRow = lines.findIndex(line => line.includes("onClick={advance_heading}"));
      check(result.some(token => token.line === handlerRow && token.text === "onClick" && token.scopes.includes("entity.other.attribute-name.pysx")), "later sibling handler attribute");
      check(result.filter(token => token.line === inlineRow && token.text === ";" && token.scopes.includes("punctuation.terminator.pysx")).length === 3, "example sibling separators");
      continue;
    }
    if (fixture === "render_snapshot") {
      for (const word of ["if", "elif", "else", "for", "match", "case", "let", "set", "discard"]) {
        check(result.some(token => token.text.trim() === word && token.scopes.includes("keyword.control.conditional.pysx")), `${word}: control keyword`);
      }
      check(result.some(token => token.text === "Card" && token.scopes.includes("support.class.component.pysx")), "bare component tag");
      check(result.some(token => token.text === "title" && token.scopes.includes("entity.other.attribute-name.pysx")), "multiline attribute");
      check(result.some(token => token.text.includes("single quoted") && token.scopes.includes("string.quoted.single.pysx")), "single template/text quotes");
      check(result.some(token => token.text.includes("braces") && token.scopes.includes("string.quoted.double.pysx")), "literal braces stay text");
      check(result.some(token => token.text.trim() === "defer" && token.scopes.includes("meta.embedded.inline.python")), "deferred expression returns to Python");
      check(result.some(token => token.text.trim() === "tuple" && token.scopes.includes("meta.embedded.inline.python")), "snapshot expression returns to Python");
      check(sentinel && !sentinel.scopes.some(scope => scope.includes("pysx") || scope.includes("css") || scope.includes("function-call")), "no snapshot scope leakage");
      continue;
    }
    if (fixture === "styled_authoring") {
      for (const [value, scope] of [
        ["padding", "support.type.property-name.css"],
        ["background", "support.type.property-name.css"],
        ["&", "entity.other.attribute-name.parent-selector.css"],
        ["letter-spacing", "support.type.property-name.css"],
        ["outline-offset", "support.type.property-name.css"],
        ["min-height", "support.type.property-name.css"],
        ["opacity", "support.type.property-name.css"],
        ["@media", "keyword.control.at-rule.css"],
        ["label", "entity.other.attribute-name.pysx"],
        ["Child", "string.quoted.double.pysx"],
      ]) check(result.some(token => token.text.includes(value) && token.scopes.includes(scope)), `${value}: ${scope}`);
      for (const value of ["TreePanel", "Card"]) {
        check(result.some(token => token.text === value && token.scopes.includes("support.class.component.pysx")), `${value}: literal component tag`);
      }
      check(result.some(token => token.text.trim() === "shared" && token.scopes.includes("meta.embedded.inline.python")), "attribute expression tag returns to Python");
      check(result.some(token => token.text === "use" && token.scopes.includes("source.python") && !token.scopes.some(scope => scope.includes("pysx"))), "use references remain Python");
      check(result.some(token => token.text === "ghost" && !token.scopes.some(scope => scope.includes("css"))), "mapping keys stay Python");
      check(sentinel && !sentinel.scopes.some(scope => scope.includes("pysx") || scope.includes("css") || scope.includes("function-call")), "authoring has no scope leakage");
      continue;
    }
    if (fixture === "styling") {
      const valueScope = "support.constant.property-value.css.pysx";
      for (const value of ["wrap", "baseline", "ui-monospace", "SFMono-Regular", "Menlo", "monospace", "sans-serif", "NovelFont", "balance", "grab", "contents", "FutureFont", "OtherFont", "InlineFont", "rebeccapurple"]) {
        check(result.some(token => token.text === value && token.scopes.includes(valueScope)), `${value}: declaration value`);
      }
      for (const [value, scope] of [
        ["Times New Roman", "string.quoted.double.css"],
        ["Fira Code", "string.quoted.single.css"],
        [".5ch", "constant.numeric.css"],
        ["#123abc", "constant.other.color.rgb-value.css"],
        ["NovelFont", valueScope],
        ["VALUE_COMMENT", "comment.block.css"],
      ]) check(result.some(token => token.text.includes(value) && token.scopes.includes(scope)), `${value}: specialized CSS scope`);
      for (const value of ["family", "spacing"]) {
        check(result.some(token => token.text === value && token.scopes.includes("meta.embedded.inline.python") && !token.scopes.includes(valueScope)), `${value}: CSS hole stays Python`);
      }
      for (const marker of [".baseline:", "span:hover:", "@supports"]) {
        const row = lines.findIndex(line => line.includes(marker));
        check(!result.some(token => token.line === row && token.scopes.includes(valueScope)), `${marker}: selector/at-rule is not a declaration value`);
      }
      const selectorRow = lines.findIndex(line => line.includes("a:hover {"));
      check(!result.some(token => token.line === selectorRow && /\b(?:a|hover)\b/.test(token.text) && token.scopes.includes(valueScope)), "CSS block selector is not a declaration value");
      check(result.some(token => token.text === "scope-check" && !token.scopes.some(scope => scope.includes("css"))), "inline CSS does not consume the following markup attribute");
      for (const [value, scope] of [
        ["color", "support.type.property-name.css"],
        ["margin", "support.type.property-name.css"],
        ["background", "support.type.property-name.css"],
        ["border-radius", "support.type.property-name.css"],
        ["border-width", "support.type.property-name.css"],
        ["--accent", "variable.other.custom-property.css"],
        ["var", "support.function.css.pysx"],
        ["calc", "support.function.css.pysx"],
        ["solid", "support.constant.property-value.css.pysx"],
        ["CSS_COMMENT", "comment.block.css"],
        ["Panel", "support.class.component.pysx"],
      ]) {
        check(result.some(token => token.text.includes(value) && token.scopes.includes(scope)), `${value}: ${scope}`);
      }
      check(result.some(token => token.text.trim() === "snapshot" && token.scopes.includes("meta.embedded.inline.python")), "DSL hole retains Python scope");
      check(sentinel && !sentinel.scopes.some(scope => scope.includes("pysx") || scope.includes("css") || scope.includes("function-call")), "styling has no scope leakage");
      check(result.some(token => token.text === "styled" && token.scopes.includes("meta.function-call.python")), "styled call retains Python call scope");
      continue;
    }
    check(sentinel && !sentinel.scopes.includes("meta.function-call.python") &&
      terminator > 0 && sentinel.line > result[terminator].line &&
      !result.slice(terminator).some((token) => token.scopes.some((scope) => scope.includes("pysx"))),
      `${fixture}: no scope leakage`);
    if (fixture === "counter") {
      for (const [value, scope] of [["Page", "support.class.component.pysx"], ["onClick", "entity.other.attribute-name.pysx"]]) {
        check(result.some((token) => token.text === value && token.scopes.includes(scope)), `${value}: ${scope}`);
      }
      check(result.some((token) => token.text.trim() === "count" && token.scopes.some((scope) => scope.includes("meta.embedded.inline.python"))), "hole body returns to Python");
    }
    if (fixture === "composition") {
      for (const name of ["Panel", "Branch"]) {
        check(result.some(token => token.text === name && token.scopes.includes("support.class.component.pysx")), `${name}: callable component scope`);
      }
      check(result.some(token => token.text === "node" && token.scopes.includes("entity.other.attribute-name.pysx")), "component prop scope");
      check(result.some(token => token.text.trim() === "children" && token.scopes.includes("meta.embedded.inline.python")), "caller children hole uses Python scope");
      check(result.filter(token => token.line === 10).every(token => !token.scopes.some(scope => scope.includes("pysx"))), "explicit namespace retains Python scope");
    }
  }
  const injection = JSON.parse(readFileSync(injectionPath, "utf8"));
  console.log(`Pylance ${host.version}; host ${host.grammarPath}; sha256 ${host.sha256}; injection ${injection.scopeName}`);
  console.log(`GRAMMAR VERIFICATION PASSED: ${suite ?? "all selected"} (${fixtures.length} fixtures, ${assertions} assertions, ${tokens} tokens)`);
} finally { registry.dispose(); }
