"""Build a .vsix with the standard library only.

A .vsix is an OPC zip. Nothing validates the manifest, so vsce/npm are not
needed and the engines.vscode pin cannot fail the install.
"""

import json
import zipfile
from pathlib import Path
from typing import cast

HERE = Path(__file__).parent
raw_package: object = json.loads((HERE / "package.json").read_text())

if not isinstance(raw_package, dict):
    raise TypeError("extension manifest must be an object")
PKG: dict[str, str] = {}
package = cast("dict[str, object]", raw_package)

for key in ("name", "version", "publisher", "displayName", "description"):
    value = package.get(key)

    if not isinstance(value, str):
        raise TypeError(f"extension manifest {key!r} must be a string")
    PKG[key] = value

MANIFEST = f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{PKG["name"]}" Version="{PKG["version"]}"
      Publisher="{PKG["publisher"]}" />
    <DisplayName>{PKG["displayName"]}</DisplayName>
    <Description xml:space="preserve">{PKG["description"]}</Description>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json"
      Addressable="true" />
  </Assets>
</PackageManifest>
"""

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json" />
  <Default Extension="js" ContentType="application/javascript" />
  <Default Extension="vsixmanifest" ContentType="text/xml" />
  <Default Extension="xml" ContentType="text/xml" />
</Types>
"""

INCLUDE = (
    "package.json",
    "icon.png",
    "extension.js",
    "authoring.js",
    "client.js",
    "highlighting.js",
    "workspaces.js",
    "commands.js",
    "syntaxes",
    "snippets",
)
# Shipping node_modules wholesale would distribute the test harness too, so the
# runtime closure is computed from the declared dependencies instead.
SKIP = {".bin", "node_modules"}
DROP = {".map", ".ts"}


def runtime_closure() -> list[str]:
    """Every package reachable from the manifest's runtime dependencies."""
    declared = package.get("dependencies")
    pending = sorted(cast("dict[str, object]", declared) if isinstance(declared, dict) else {})
    seen: list[str] = []

    while pending:
        name = pending.pop()

        if name in seen:
            continue
        manifest = HERE / "node_modules" / name / "package.json"

        if not manifest.is_file():
            raise FileNotFoundError(f"{name} is declared but not installed; run npm install")
        seen.append(name)
        found: object = json.loads(manifest.read_text())

        if not isinstance(found, dict):
            raise TypeError(f"{name} manifest must be an object")
        nested = cast("dict[str, object]", found).get("dependencies")
        pending.extend(cast("dict[str, object]", nested) if isinstance(nested, dict) else {})

    return sorted(seen)


def main() -> None:
    out = HERE / f"{PKG['name']}-{PKG['version']}.vsix"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("extension.vsixmanifest", MANIFEST)
        z.writestr("[Content_Types].xml", CONTENT_TYPES)

        for name in INCLUDE:
            path = HERE / name

            if path.is_file():
                z.write(path, f"extension/{name}")
            elif path.is_dir():
                for f in sorted(path.rglob("*")):
                    if f.is_file():
                        z.write(f, f"extension/{f.relative_to(HERE)}")

        for name in runtime_closure():
            root = HERE / "node_modules" / name

            for f in sorted(root.rglob("*")):
                if not f.is_file() or f.suffix in DROP:
                    continue

                if SKIP & set(f.relative_to(root).parts):
                    continue
                z.write(f, f"extension/node_modules/{name}/{f.relative_to(root)}")
    print(out)


if __name__ == "__main__":
    main()
