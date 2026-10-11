"""Build the GitHub Pages site in docs/ from collection.csv.

Cover priority: a crop of the capture (crops.json) > Cover Art Archive art (covers/,
from enrich.py) > none. Year: the CSV's year column > MusicBrainz first-release year.
Raw captures (with the room and the person in them) are never copied into docs/.
"""

import csv
import json
from pathlib import Path

import cv2

DOCS = Path("docs")
SIZE = 500


def main():
    cache = json.loads(Path("mb_cache.json").read_text())
    crops = {k: v for k, v in json.loads(Path("crops.json").read_text()).items() if not k.startswith("_")}
    out_covers = DOCS / "covers"
    out_covers.mkdir(parents=True, exist_ok=True)
    for old in out_covers.glob("*.jpg"):
        old.unlink()

    records = []
    for order, row in enumerate(csv.DictReader(open("collection.csv"))):
        rid = Path(row["image"]).stem
        hit = cache.get(f'{row["artist"]}|{row["title"]}', {})

        img = None
        if rid in crops:
            x0, y0, x1, y1 = crops[rid]
            img = cv2.imread(row["image"])[y0:y1, x0:x1]
        elif (Path("covers") / f"{rid}.jpg").exists():
            img = cv2.imread(str(Path("covers") / f"{rid}.jpg"))
        cover = None
        if img is not None:
            img = cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
            cover = f"covers/{rid}.jpg"
            cv2.imwrite(str(DOCS / cover), img, [cv2.IMWRITE_JPEG_QUALITY, 84])

        unknown = row["artist"] == "?"
        records.append({
            "id": rid,
            "order": order,
            "artist": None if unknown else row["artist"],
            "title": None if unknown or row["title"].startswith("(") else row["title"].rstrip("?"),
            "year": row["year"] or hit.get("year") or None,
            "format": row["format"],
            "needsId": unknown or row["confidence"] != "high",
            "notes": row["notes"],
            "cover": cover,
            "mbid": hit.get("mbid"),
        })

    (DOCS / "collection.json").write_text(json.dumps(records, indent=1, ensure_ascii=False))
    (DOCS / ".nojekyll").touch()
    print(f"{len(records)} records, {sum(r['cover'] is not None for r in records)} covers -> {DOCS}/")


if __name__ == "__main__":
    main()
