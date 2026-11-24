"""Export a recorded events file for static hosting (e.g. GitHub Pages).

    python -m gk.export_replay events.jsonl docs/replay.json [--all]

Takes the LAST run in the file (from its final `run_start` event) unless --all is given, and writes
{"label": "REPLAY ...", "events": [...]}. The page plays it back with a permanent REPLAY badge via
index.html?replay=replay.json. It never overwrites docs/index.html.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def export(src: str, dst: str, last_run_only: bool = True) -> dict:
    events = []
    with open(src, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    if last_run_only:
        starts = [i for i, e in enumerate(events) if e.get("type") == "run_start"]
        if starts:
            events = events[starts[-1]:]
    modes = sorted({e.get("mode") for e in events if e.get("mode")})
    doc = {"label": "REPLAY of a recorded run (not live)", "source": src,
           "exported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "modes": modes, "simulated": modes == ["mock"], "events": events}
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return {"events": len(events), "modes": modes}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    paths = [a for a in argv if not a.startswith("--")]
    if len(paths) != 2:
        print("usage: python -m gk.export_replay events.jsonl docs/replay.json [--all]")
        return 2
    if paths[1].replace("\\", "/").endswith("docs/index.html"):
        print("refusing to overwrite docs/index.html")
        return 2
    info = export(paths[0], paths[1], "--all" not in argv)
    print(f"wrote {paths[1]}: {info['events']} events, modes {info['modes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
