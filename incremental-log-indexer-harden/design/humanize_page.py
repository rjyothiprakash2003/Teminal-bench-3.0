"""Builds the humanization page (checklist line 10): instruction.md and the four prose
fields of task.toml as text the author edits, saved to the page's db; every other shipped
file as a read-only preview. Usage: humanize_page.py <out.html>"""
import json
import sys
import tomllib
from pathlib import Path

TASK = Path(__file__).resolve().parent.parent
FIELDS = ["difficulty_explanation", "solution_explanation", "verification_explanation", "relevant_experience"]
meta = tomllib.loads((TASK / "task.toml").read_text())["metadata"]
editable = {"instruction": (TASK / "instruction.md").read_text().rstrip("\n")}
editable.update({k: meta[k] for k in FIELDS})
files = {}
for p in sorted(TASK.rglob("*")):
    rel = p.relative_to(TASK).as_posix()
    if not p.is_file() or rel.startswith("design/") or rel == "instruction.md" or p.name == ".DS_Store":
        continue
    files[rel] = p.read_text(errors="replace")
data = json.dumps({"editable": editable, "files": files}).replace("</", "<\\/")
html = (Path(__file__).resolve().parent / "humanize_template.html").read_text().replace("__DATA__", data)
Path(sys.argv[1]).write_text(html)
print("wrote %s: %d editable texts, %d files, %d bytes" % (sys.argv[1], len(editable), len(files), len(html)))
