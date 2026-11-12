"""One-off check: does Wowhead serve an item's web page (with its drop table) to a GitHub runner?"""
import json
import re
import sys

import requests

ITEM = 4338  # Mageweave Cloth


def note(text: str) -> None:
    print(f"::notice title=wowhead-test::{text[:900]}")


for label, url in (("xml feed", f"https://www.wowhead.com/forever/item={ITEM}&xml"),
                   ("web page", f"https://www.wowhead.com/forever/item={ITEM}")):
    r = requests.get(url, timeout=30, headers={"User-Agent": "forever-ah-tracker"})
    note(f"{label}: HTTP {r.status_code}, {len(r.text)} characters")
page = r.text
at = page.find("dropped-by")
note(f"drop table marker present: {at >= 0}")
if r.status_code != 200 or at < 0:
    sys.exit(0)
note("format sample: " + re.sub(r"\s+", " ", page[max(0, at - 150):at + 500]))
start = page.find("data:", at)
start = page.find("[", start)
depth, end = 0, start
for end in range(start, len(page)):
    depth += page[end] == "["
    depth -= page[end] == "]"
    if depth == 0:
        break
try:
    rows = json.loads(page[start:end + 1])
except ValueError as exc:
    note(f"could not parse the table: {exc}")
    sys.exit(0)
note(f"rows: {len(rows)}; keys: {sorted(rows[0])}")
big = [x for x in rows if x.get("outof", 0) >= 500]
big.sort(key=lambda x: -x["count"] / x["outof"])
for x in big[:8]:
    note(f"{x.get('name')}: {x['count']} of {x['outof']} = {100 * x['count'] / x['outof']:.0f}%")
