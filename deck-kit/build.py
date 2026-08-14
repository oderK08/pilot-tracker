#!/usr/bin/env python3
"""
Fusionne index.html + tous ses <script src> en UN seul fichier autonome.

    python3 build.py            ->  deck.html

Tu développes en modules, tu livres un fichier unique qui s'ouvre
partout, hors ligne, sans rien installer.

Bonus : --embed remplace aussi les <img src="..."> locales par leur
version base64, pour que le fichier soit vraiment auto-suffisant.
"""
import base64
import mimetypes
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent
SRC = ROOT / "index.html"
OUT = ROOT / "deck.html"
EMBED_IMAGES = "--embed" in sys.argv


def inline_scripts(html: str) -> str:
    def repl(m):
        path = ROOT / m.group(1)
        if not path.exists():
            print(f"  ! script introuvable, laissé tel quel : {m.group(1)}")
            return m.group(0)
        print(f"  + {m.group(1)} ({path.stat().st_size:,} o)")
        code = path.read_text(encoding="utf-8")
        # Un "</script>" à l'intérieur du code fermerait la balise trop tôt.
        code = code.replace("</script>", "<\\/script>")
        return f"<script>\n{code}\n</script>"

    return re.sub(r'<script src="([^"]+)"></script>', repl, html)


def inline_images(html: str) -> str:
    def repl(m):
        attr, path_str = m.group(1), m.group(2)
        if path_str.startswith(("http", "data:")):
            return m.group(0)
        path = ROOT / path_str
        if not path.exists():
            return m.group(0)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        b64 = base64.b64encode(path.read_bytes()).decode()
        print(f"  + image {path_str} ({path.stat().st_size:,} o -> base64)")
        return f'{attr}="data:{mime};base64,{b64}"'

    return re.sub(r'(src|href)="([^"]+\.(?:png|jpe?g|gif|webp|svg))"', repl, html)


def main() -> None:
    print(f"Fusion de {SRC.name} ...")
    html = SRC.read_text(encoding="utf-8")
    html = inline_scripts(html)
    if EMBED_IMAGES:
        html = inline_images(html)
    OUT.write_text(html, encoding="utf-8")
    print(f"\n-> {OUT.name} · {OUT.stat().st_size:,} octets · fichier autonome")


if __name__ == "__main__":
    main()
