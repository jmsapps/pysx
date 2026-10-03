"""Build a .vsix with the standard library only.

A .vsix is an OPC zip. Nothing validates the manifest, so vsce/npm are not
needed and the engines.vscode pin cannot fail the install.
"""

import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
PKG = json.loads((HERE / "package.json").read_text())

MANIFEST = f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{PKG['name']}" Version="{PKG['version']}" Publisher="{PKG['publisher']}" />
    <DisplayName>{PKG['displayName']}</DisplayName>
    <Description xml:space="preserve">{PKG['description']}</Description>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
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

INCLUDE = ("package.json", "extension.js", "syntaxes", "pysx-root.json")


def main() -> None:
    # The installed extension lives outside the checkout, so the path to the
    # interpreter cannot be relative to __dirname.
    (HERE / "pysx-root.json").write_text(
        json.dumps({"root": str(HERE.parent.resolve())}) + "\n"
    )
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
    print(out)


if __name__ == "__main__":
    sys.exit(main())
