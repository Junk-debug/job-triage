import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKILLS = os.path.join(ROOT, ".claude", "skills")

for sub in ("job-triage", os.path.join("job-triage", "sources"), "applications-check"):
    path = os.path.join(SKILLS, sub)
    if path not in sys.path:
        sys.path.insert(0, path)
