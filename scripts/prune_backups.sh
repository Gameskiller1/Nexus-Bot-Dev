#!/usr/bin/env python3
"""Retention: all <7d, one/day to 30d, one/month to 12mo.
Non-'scheduled' tags (pre-fix, pre-migration, manual) are kept forever."""
import os
import re
import sys
from datetime import datetime, timezone, timedelta

DEST = "/home/ubuntu/Nexus-Bot-Dev/backups"
PATTERN = re.compile(r"^nexus-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)-([\w-]+)\.db\.gz$")
now = datetime.now(timezone.utc)

backups = []
for name in os.listdir(DEST):
    m = PATTERN.match(name)
    if not m:
        continue  # legacy / quarantined files untouched
    ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
    backups.append((ts, m.group(2), os.path.join(DEST, name)))

backups.sort(key=lambda b: b[0])
keep, days, months = set(), set(), set()

for ts, tag, path in backups:
    age = now - ts
    if tag != "scheduled":
        keep.add(path)
        continue
    if age < timedelta(days=7):
        keep.add(path)
    elif age < timedelta(days=30):
        if ts.date() not in days:
            days.add(ts.date())
            keep.add(path)
    elif age < timedelta(days=365):
        key = (ts.year, ts.month)
        if key not in months:
            months.add(key)
            keep.add(path)

removed = 0
for ts, tag, path in backups:
    if path not in keep:
        os.remove(path)
        removed += 1
        print(f"pruned {os.path.basename(path)}", file=sys.stderr)

print(f"retention: kept {len(keep)}, pruned {removed}")