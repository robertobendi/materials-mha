#!/usr/bin/env python3
"""post_comment.py <request_id> <comment.md> --confirm

Posts one comment. Needs MC_READ_ONLY=0 and an explicit --confirm; there is no
accept or decline path here.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc_api

if "--confirm" not in sys.argv:
    sys.exit("add --confirm to actually post")
rid, path = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
print("--- posting to request", rid, "---")
print(text)
print(mc_api.comment(rid, text))
