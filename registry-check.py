#!/usr/bin/env python3
"""Require an authenticated, definitive not-found response before publication."""
import subprocess
import sys

EXPECTED = "repo.cylo.net/qbittorrent:5.2.4_2.0.15.0-1"
BASELINE = "repo.cylo.net/qbittorrent:5.2.4_2.0.15.0"

def inspect(image):
    return subprocess.run(["docker", "manifest", "inspect", image],
                          capture_output=True, text=True, timeout=90)

if len(sys.argv) != 2 or sys.argv[1] != EXPECTED:
    raise SystemExit("Unexpected image reference")
if inspect(BASELINE).returncode:
    raise SystemExit("Baseline registry image is unreadable; refusing publication")
result = inspect(EXPECTED)
if result.returncode == 0:
    raise SystemExit("Release tag already exists; refusing replacement")
if result.stderr.strip() != f"no such manifest: {EXPECTED}":
    raise SystemExit("Registry response is indeterminate; refusing publication")
print("Authenticated registry confirms the release tag is absent")
