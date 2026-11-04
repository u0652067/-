"""One-line summaries for rehearsal2.sh."""
import json
import sys

if sys.argv[1] == "--manifest":
    m = json.load(open(sys.argv[2], encoding="utf-8-sig"))
    v = m["verified_restore"]
    print("sha256", m["sha256"], "bytes", m["bytes"])
    print("tables in manifest", len(m["row_counts"]), "rows", sum(m["row_counts"].values()),
          "verified", v and v["ok"], "differs", v and v["differs"])
elif sys.argv[1] == "--integrity":
    d = json.load(open(sys.argv[2], encoding="utf-8"))
    print("window", d.get("window"))
    print("verdict", json.dumps(d.get("verdict"), ensure_ascii=False)[:1500])
else:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    print(sys.argv[1].rsplit("/", 1)[-1], len(d), "tables", sum(v["rows"] for v in d.values()), "rows")
