#!/usr/bin/env python3
import argparse
import os
import shutil
import subprocess
import sys

import data_dir

TEMPLATE = os.path.join(data_dir.REPO_ROOT, "templates", "data-repo")


def inside(path, parent):
    return os.path.commonpath([path, parent]) == parent


def main():
    ap = argparse.ArgumentParser(description="Create the private data repo from the template.")
    ap.add_argument("--dir", help=f"where to create it (default {data_dir.DEFAULT})")
    args = ap.parse_args()

    target = os.path.abspath(os.path.expanduser(args.dir)) if args.dir else data_dir.DEFAULT

    if inside(target, data_dir.REPO_ROOT):
        sys.exit(f"{target} is inside the code repo; the data repo must live outside it")
    if os.path.exists(os.path.join(target, "criteria.md")):
        sys.exit(f"{target} already has a criteria.md; refusing to overwrite")

    shutil.copytree(TEMPLATE, target, dirs_exist_ok=True)
    os.makedirs(os.path.join(target, "triage"), exist_ok=True)
    open(os.path.join(target, "triage", ".gitkeep"), "a").close()

    if not os.path.isdir(os.path.join(target, ".git")):
        subprocess.run(["git", "-C", target, "init", "-q"], check=True)
    subprocess.run(["git", "-C", target, "add", "-A"], check=True)
    subprocess.run(["git", "-C", target, "commit", "-q", "-m", "chore: initial data repo"], check=True)

    if target != data_dir.DEFAULT:
        with open(data_dir.POINTER, "w") as f:
            f.write(target + "\n")

    print(target)


if __name__ == "__main__":
    main()
