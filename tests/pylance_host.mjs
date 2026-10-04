import { readFileSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";

export function pylanceHost() {
  let directory = process.env.PYSX_PYLANCE_EXTENSION;
  if (!directory) {
    const extensions = process.env.PYSX_EXTENSIONS_DIR ?? path.join(homedir(), ".vscode", "extensions");
    const registryPath = path.join(extensions, "extensions.json");
    if (!existsSync(registryPath)) throw new Error("missing Pylance registry; set PYSX_PYLANCE_EXTENSION");
    const registered = JSON.parse(readFileSync(registryPath, "utf8"))
      .filter((entry) => entry.identifier.id === "ms-python.vscode-pylance")
      .sort((a, b) => b.version.localeCompare(a.version, undefined, { numeric: true }));
    if (!registered.length) throw new Error("missing registered Pylance extension");
    directory = path.join(extensions, registered[0].relativeLocation);
  }
  const manifest = JSON.parse(readFileSync(path.join(directory, "package.json"), "utf8"));
  if (manifest.publisher !== "ms-python" || manifest.name !== "vscode-pylance") {
    throw new Error("incompatible host: expected actual Pylance extension metadata");
  }
  const contribution = manifest.contributes?.grammars?.find((item) => item.scopeName === "source.python");
  if (!contribution) throw new Error("incompatible Pylance: no source.python contribution");
  const grammarPath = path.resolve(directory, contribution.path);
  const source = readFileSync(grammarPath, "utf8");
  if (!source.includes("tstring-triple") || !source.includes("tstring-interpolation")) {
    throw new Error("incompatible Pylance: PEP 750 grammar required");
  }
  return { directory, grammarPath, version: manifest.version,
    sha256: createHash("sha256").update(source).digest("hex") };
}
