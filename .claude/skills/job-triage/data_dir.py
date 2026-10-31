import os

ENV_VAR = "JOB_TRIAGE_DATA_DIR"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
POINTER = os.path.join(REPO_ROOT, ".data-dir")
DEFAULT = os.path.join(os.path.dirname(REPO_ROOT), os.path.basename(REPO_ROOT) + "-data")


def _expand(path):
    return os.path.abspath(os.path.join(REPO_ROOT, os.path.expanduser(path)))


def candidates():
    found = []
    if os.environ.get(ENV_VAR):
        found.append(_expand(os.environ[ENV_VAR]))
    if os.path.exists(POINTER):
        with open(POINTER) as f:
            line = f.readline().strip()
        if line:
            found.append(_expand(line))
    found.append(DEFAULT)
    return found


def find_data_dir():
    return next((p for p in candidates() if os.path.isdir(p)), None)


def require_data_dir():
    found = find_data_dir()
    if found:
        return found
    raise SystemExit(
        "No data repo found. Looked in: " + ", ".join(candidates()) + ".\n"
        "Create one with the setup-job-triage skill, "
        f"or point {ENV_VAR} at an existing one."
    )


def data_path(*parts):
    return os.path.join(require_data_dir(), *parts)


def resolve(*parts):
    return os.path.join(find_data_dir() or DEFAULT, *parts)
