#!/usr/bin/env python3
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc_api
qs = mc_api.open_requests()
print(json.dumps([{"request_id": q["id"], "record_id": q["topic"]["record"],
                   "title": q["title"], "created": q["created"]} for q in qs],
                 indent=2, ensure_ascii=False))
