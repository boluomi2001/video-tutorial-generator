#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Write per-seq creds from a batch dict then upload all of them."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from up import upload_seq  # noqa: E402

# creds are appended below by caller via stdin json
data = json.loads(sys.stdin.read())
for seq, c in data.items():
    json.dump(c, open(os.path.join(HERE, "_cred_%s.json" % seq), "w", encoding="utf-8"), ensure_ascii=False)
for seq in data:
    upload_seq(seq)
