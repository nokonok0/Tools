"""Retain display snapshots, independently from live quota validity."""
import json
import math
import os
from pathlib import Path


class LastForecast:
    def __init__(self, path):
        self.path = Path(path)
        self.dirty = False
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.values = {key: value for key, value in saved.items()
                           if isinstance(value, dict) and all(
                               isinstance(value.get(field), (int, float))
                               and math.isfinite(value[field])
                               for field in ("rate", "hours", "observed_at"))}
        except (OSError, ValueError, AttributeError):
            self.values = {}

    def present(self, identity, result):
        key = json.dumps(identity, ensure_ascii=False)
        saved = self.values.get(key)
        if "rate" in result and "observed_at" in result:
            if not saved or result["observed_at"] > saved["observed_at"]:
                saved = dict(result)
                self.values[key] = saved
                self.dirty = True
            displayed = {**saved, "status": result["status"]}
        elif saved:
            displayed = {**saved, "status": result["status"], "historical": True}
            if result["status"] == "exhausted":
                displayed["hours"] = 0
        else:
            return dict(result)
        if result["status"] == "exhausted":
            displayed["hours"] = 0
        if result["status"] == "stale":
            displayed["historical"] = True
        return displayed

    def save(self):
        if not self.dirty:
            return
        temporary = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(self.values, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)
            self.dirty = False
        except OSError:
            # A read-only/full data drive must not hide the in-memory values.
            pass
