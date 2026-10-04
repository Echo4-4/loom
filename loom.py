#!/usr/bin/env python3
"""Loom - weave your branches. Local-first web UI for git."""
import argparse, base64, difflib, filecmp, hashlib, json, shutil, os, re, secrets, shlex, subprocess, threading, time, webbrowser
import urllib.error, urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

HERE = Path(__file__).parent
TOKEN = secrets.token_urlsafe(24)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    # Block DNS-rebinding (Host check) and require the token on every API call.
    host = request.headers.get("host", "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return JSONResponse({"error": "bad host"}, status_code=403)
    if request.url.path.startswith("/api/"):
        sent = request.headers.get("x-loom-token", "")
        if not secrets.compare_digest(sent, TOKEN):
            return JSONResponse({"error": "bad token"}, status_code=401)
    return await call_next(request)


def run_git(args, cwd, timeout=60, extra_env=None, redact=()):
    t = time.time()
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **(extra_env or {})}
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, timeout=timeout, env=env)
        out, err, code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        out, err, code = "", "timed out after %ss" % timeout, 124
    except FileNotFoundError:
        out, err, code = "", "git is not installed (sudo apt install git)", 127
    for s in redact:
        out, err = out.replace(s, "***"), err.replace(s, "***")
    return {"command": "git " + shlex.join(args), "stdout": out, "stderr": err,
            "exit_code": code, "duration": round(time.time() - t, 3)}


SIMPLE = {
    "status": ["status", "--porcelain=v1", "-b"],
    "log": ["log", "--graph", "--oneline", "--decorate", "--all", "-n", "60"],
    "branches": ["branch", "--format=%(HEAD)|%(refname:short)"],
    "add_all": ["add", "-A"],
    "fetch": ["fetch", "--all", "--prune"],
    "pull": ["pull"],
    "stash": ["stash", "push", "-u"],
    "stash_pop": ["stash", "pop"],
    "origin": ["config", "--get", "remote.origin.url"],
    "ls": ["ls-files", "-z", "-co", "--exclude-standard"],
    "graphlog": ["log", "--all", "--date-order", "-n", "200", "--format=%H%x1f%P%x1f%D%x1f%s%x1f%an%x1f%ar"],
    "reflog": ["reflog", "-n", "60", "--format=%H%x1f%gd%x1f%gs%x1f%ar"],
}


def need(v, what):
    v = (v or "").strip()
    if not v or v.startswith("-"):
        raise HTTPException(400, f"invalid {what}")
    return v


def build(action, a):
    if action in SIMPLE:
        return SIMPLE[action]
    if action == "init":
        return ["init", "-b", (a.get("branch") or "main").strip()]
    if action == "add":
        return ["add", "--", need(a.get("file"), "file")]
    if action == "unstage":
        return ["restore", "--staged", "--", need(a.get("file"), "file")]
    if action == "commit":
        return ["commit", "-m", need(a.get("message"), "message")]
    if action == "diff":
        return ["diff", "--no-color"] + (["--staged"] if a.get("staged") else []) + ["--", need(a.get("file"), "file")]
    if action == "switch":
        return ["switch", need(a.get("branch"), "branch")]
    if action == "new_branch":
        return ["switch", "-c", need(a.get("branch"), "branch")]
    if action == "push":
        if a.get("upstream"):
            return ["push", "-u", "origin", need(a.get("branch"), "branch")]
        return ["push"]
    if action == "raw":  # command bar: "git log --oneline -10"
        parts = shlex.split(a.get("cmd", ""))
        if parts and parts[0] == "git":
            parts = parts[1:]
        if not parts or parts[0].startswith("-"):
            raise HTTPException(400, "give me a git subcommand")
        return parts
    raise HTTPException(400, "unknown action")


class Req(BaseModel):
    path: str
    action: str
    args: dict = {}


@app.post("/api/run")
def run(r: Req):
    p = Path(r.path).expanduser()
    if not p.is_dir():
        raise HTTPException(400, "not a directory: " + r.path)
    cwd, a = str(p.resolve()), r.args
    if r.action == "set_remote":
        url = need(a.get("url"), "url")
        if not url.startswith(("https://", "git@", "ssh://")):
            raise HTTPException(400, "use an https:// URL")
        has = run_git(["remote", "get-url", "origin"], cwd)["exit_code"] == 0
        return run_git(["remote", "set-url" if has else "add", "origin", url], cwd)
    if r.action == "push_https":
        # Token goes in a one-shot auth header via env: never saved to .git/config.
        tok = need(a.get("token"), "token")
        auth = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
               "GIT_CONFIG_VALUE_0": "Authorization: Basic " + auth}
        return run_git(build("push", a), cwd, 120, env, (tok, auth))
    return run_git(build(r.action, a), cwd)


GH = re.compile(r"^(?:(?:https?://)?(?:www\.)?github\.com[/:]|git@github\.com:)?([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/tree/([^/?#]+))?(?:[/?#].*)?$")


class Remote(BaseModel):
    url: str
    token: str = ""


def gh(path, token):
    h = {"User-Agent": "loom", "Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = "Bearer " + token
    try:
        req = urllib.request.Request("https://api.github.com" + path, headers=h)
        with urllib.request.urlopen(req, timeout=20) as f:
            return json.load(f)
    except urllib.error.HTTPError as e:
        raise HTTPException(400, {404: "repo not found (or private - add a token)",
                                  403: "GitHub rate limit hit - add a token",
                                  401: "GitHub rejected the token"}.get(e.code, f"GitHub error {e.code}"))
    except Exception as e:
        raise HTTPException(400, f"cannot reach GitHub: {e}")


@app.post("/api/remote")
def remote(r: Remote):
    m = GH.match(r.url.strip())
    if not m:
        raise HTTPException(400, "that doesn't look like a GitHub repo link")
    o, n, br = m.groups()
    info = gh(f"/repos/{o}/{n}", r.token)
    br = br or info["default_branch"]
    t = gh(f"/repos/{o}/{n}/git/trees/{quote(br)}?recursive=1", r.token)
    return {"repo": info["full_name"], "branch": br, "description": info.get("description"),
            "truncated": t.get("truncated", False),
            "files": [{"p": x["path"], "s": x.get("size", 0), "h": x["sha"]} for x in t["tree"] if x["type"] == "blob"]}


class RGraph(BaseModel):
    repo: str
    branch: str
    token: str = ""


@app.post("/api/remote_graph")
def remote_graph(r: RGraph):
    """Commit graph for a GitHub repo (read via the API, no clone)."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", r.repo):
        raise HTTPException(400, "invalid repo")
    base = f"/repos/{r.repo}"
    cm = gh(f"{base}/commits?sha={quote(r.branch)}&per_page=100", r.token)
    refs = {}
    for path, pre in ((f"{base}/branches?per_page=100", ""), (f"{base}/tags?per_page=100", "tag: ")):
        try:
            for x in gh(path, r.token):
                refs.setdefault(x["commit"]["sha"], []).append(pre + x["name"])
        except HTTPException:
            pass  # decorations are optional
    out = []
    for c in cm:
        d = [("HEAD -> " + n if n == r.branch else n) for n in refs.get(c["sha"], [])]
        au = c["commit"]["author"] or {}
        out.append({"h": c["sha"], "p": [x["sha"] for x in c["parents"]], "d": ", ".join(d),
                    "s": c["commit"]["message"].split("\n")[0], "a": au.get("name", ""),
                    "t": au.get("date", "")})
    return {"commits": out}


IMG = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
       "svg": "image/svg+xml", "ico": "image/x-icon", "bmp": "image/bmp", "avif": "image/avif"}
MAX_TEXT, MAX_IMG = 1_500_000, 8_000_000


def payload(name, data):
    """Turn raw bytes into something the read-only viewer can show."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    n = len(data)
    if ext in IMG:
        if n > MAX_IMG:
            return {"kind": "big", "size": n}
        return {"kind": "image", "mime": IMG[ext], "data": base64.b64encode(data).decode(), "size": n}
    if b"\0" in data[:8000]:
        return {"kind": "binary", "size": n}
    if n > MAX_TEXT:
        return {"kind": "big", "size": n}
    return {"kind": "text", "text": data.decode("utf-8", "replace"), "size": n}


class FileReq(BaseModel):
    path: str
    file: str


@app.post("/api/file")
def read_file(r: FileReq):
    """Read one file from a local repo (read-only, must stay inside the repo folder)."""
    root = Path(r.path).expanduser().resolve()
    f = (root / r.file).resolve()
    if not root.is_dir() or not f.is_relative_to(root):
        raise HTTPException(400, "file is outside the repo")
    if not f.is_file():
        raise HTTPException(404, "file not found (deleted or moved?)")
    size = f.stat().st_size
    if size > MAX_IMG:
        return {"kind": "big", "size": size}
    return payload(f.name, f.read_bytes())


class RFile(BaseModel):
    repo: str
    sha: str
    name: str
    size: int = 0
    token: str = ""


@app.post("/api/remote_file")
def remote_file(r: RFile):
    """Read one file of a GitHub repo through the blobs API (read-only)."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", r.repo) or not re.fullmatch(r"[0-9a-f]{40}", r.sha):
        raise HTTPException(400, "invalid file reference")
    if r.size > MAX_IMG:
        return {"kind": "big", "size": r.size}
    b = gh(f"/repos/{r.repo}/git/blobs/{r.sha}", r.token)
    return payload(r.name, base64.b64decode(b.get("content", "")))


# ---- Compare: this repo vs another folder / file ---------------------------
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
MAX_FILES, MAX_DIFF = 20000, 600_000
BAK = Path.home() / ".loom-backups"


def sides(path, other):
    if not (other or "").strip():
        raise HTTPException(400, "enter the other folder or file path")
    a, b = Path(path).expanduser(), Path(other.strip()).expanduser()
    if not a.is_dir():
        raise HTTPException(400, "not a directory: " + path)
    if not b.exists():
        raise HTTPException(400, "not found: " + other)
    return a.resolve(), b.resolve()


def pair(path, other, rel):
    """Validate a repo-relative file and return (repo_root, repo_file, other_file). Only the repo side is ever written."""
    a, b = sides(path, other)
    rel = (rel or "").strip().strip("/")
    pa = (a / rel).resolve()
    if not rel or pa == a or not pa.is_relative_to(a) or ".git" in Path(rel).parts:
        raise HTTPException(400, "bad file path")
    if b.is_file():
        return a, pa, b
    pb = (b / rel).resolve()
    if not pb.is_relative_to(b):
        raise HTTPException(400, "bad file path")
    return a, pa, pb


def listing(root):
    """{relative path: absolute path}. Uses git's ignore rules when root is a repo."""
    if (root / ".git").exists():
        r = run_git(["ls-files", "-z", "-co", "--exclude-standard"], str(root))
        if not r["exit_code"]:
            names = [n for n in r["stdout"].split("\0") if n and ".git" not in n.split("/")]
            return {n: str(root / n) for n in names
                    if (root / n).is_file() and not (root / n).is_symlink()}
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP and not os.path.islink(os.path.join(d, x))]
        for f in files:
            fp = os.path.join(d, f)
            if not os.path.islink(fp):
                out[os.path.relpath(fp, root).replace(os.sep, "/")] = fp
        if len(out) > MAX_FILES:
            raise HTTPException(400, f"too many files to compare (limit {MAX_FILES})")
    return out


def ignored(root, names):
    """Which of these names does the repo at root git-ignore?"""
    if not names or not (root / ".git").exists():
        return set()
    try:
        p = subprocess.run(["git", "check-ignore", "--no-index", "-z", "--stdin"], cwd=root,
                           input="\0".join(names) + "\0", capture_output=True, text=True, timeout=60)
        return {n for n in p.stdout.split("\0") if n}
    except Exception:
        return set()


def is_bin(p):
    try:
        if os.path.getsize(p) > MAX_DIFF:
            return True
        with open(p, "rb") as f:
            return b"\0" in f.read(8000)
    except OSError:
        return True


def same(pa, pb, eol):
    if os.path.getsize(pa) == os.path.getsize(pb) and filecmp.cmp(pa, pb, shallow=False):
        return True
    if not eol or is_bin(pa) or is_bin(pb):
        return False
    return Path(pa).read_bytes().replace(b"\r\n", b"\n") == Path(pb).read_bytes().replace(b"\r\n", b"\n")


def entry(rel, pa, pb):
    return {"p": rel, "sa": os.path.getsize(pa) if pa else 0, "sb": os.path.getsize(pb) if pb else 0,
            "bin": any(is_bin(x) for x in (pa, pb) if x)}


class Cmp(BaseModel):
    path: str
    other: str
    target: str = ""
    eol: bool = True


@app.post("/api/compare")
def compare(r: Cmp):
    a, b = sides(r.path, r.other)
    if b.is_file():
        rel = (r.target or b.name).strip().strip("/")
        pa = (a / rel).resolve()
        if not rel or not pa.is_relative_to(a) or ".git" in Path(rel).parts:
            raise HTTPException(400, "bad repo file name")
        fa, fb = ({rel: str(pa)} if pa.is_file() else {}), {rel: str(b)}
    else:
        if a == b or a.is_relative_to(b) or b.is_relative_to(a):
            raise HTTPException(400, "pick a folder that is not the repo itself or inside/around it")
        fa, fb = listing(a), listing(b)
        for n in ignored(a, list(fb)) - set(fa):   # skip build junk / secrets this repo ignores
            fb.pop(n, None)
    out = {"changed": [], "only_a": [], "only_b": [], "same": 0}
    for rel in sorted(fa.keys() | fb.keys()):
        x, y = fa.get(rel), fb.get(rel)
        if x and y:
            if same(x, y, r.eol):
                out["same"] += 1
            else:
                out["changed"].append(entry(rel, x, y))
        elif y:
            out["only_b"].append(entry(rel, None, y))
        else:
            out["only_a"].append(entry(rel, x, None))
    return out


def split(p):
    """(bytes, lines) of a UTF-8 text file; a missing file is empty."""
    b = p.read_bytes() if p.is_file() else b""
    if len(b) > MAX_DIFF or b"\0" in b[:8000]:
        raise ValueError("binary or too large to merge line by line")
    try:
        return b, b.decode("utf-8").splitlines(True)
    except UnicodeDecodeError:
        raise ValueError("not UTF-8 text")


def opcodes(la, lb):
    k = lambda L: [x.rstrip("\r\n") for x in L]   # line endings never count as a difference
    return difflib.SequenceMatcher(None, k(la), k(lb), autojunk=False).get_opcodes()


def glue(parts):
    s = ""
    for x in parts:
        s += ("\n" if s and x and not s.endswith("\n") else "") + x
    return s


class CFile(BaseModel):
    path: str
    other: str
    rel: str


@app.post("/api/compare_file")
def compare_file(r: CFile):
    a, pa, pb = pair(r.path, r.other, r.rel)
    try:
        ba, la = split(pa)
        _, lb = split(pb)
    except ValueError:
        return {"kind": "binary"}
    return {"kind": "text", "a": la, "b": lb, "ops": [list(o) for o in opcodes(la, lb)],
            "base": hashlib.sha1(ba).hexdigest()}


class Item(BaseModel):
    rel: str
    mode: str                      # take | join | merge | delete
    content: Optional[str] = None  # merge: the finished text
    base: Optional[str] = None     # merge: hash of the repo file when the review opened


class Apply(BaseModel):
    path: str
    other: str
    items: list[Item]


@app.post("/api/apply")
def apply(r: Apply):
    """Write results into the repo side only. Every overwritten/deleted file is backed up first."""
    stamp, used, res = time.strftime("%Y%m%d-%H%M%S"), None, []
    for it in r.items:
        try:
            a, pa, pb = pair(r.path, r.other, it.rel)
            data = None
            if it.mode == "take":
                if not pb.is_file():
                    raise ValueError("the other side has no such file")
            elif it.mode == "merge":
                cur = pa.read_bytes() if pa.is_file() else b""
                if it.content is None or it.base != hashlib.sha1(cur).hexdigest():
                    raise ValueError("file changed since the review opened - review it again")
                data = it.content
            elif it.mode == "join":
                la, lb = split(pa)[1], split(pb)[1]
                data = glue(["".join(la[i1:i2]) if t == "equal" else
                             glue(["".join(la[i1:i2]), "".join(lb[j1:j2])])
                             for t, i1, i2, j1, j2 in opcodes(la, lb)])
            elif it.mode != "delete":
                raise ValueError("unknown mode")
            if pa.is_file():
                bdir = BAK / hashlib.sha1(str(a).encode()).hexdigest()[:10] / stamp
                dst = bdir / pa.relative_to(a)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pa, dst)
                used = bdir
            if it.mode == "delete":
                if pa.is_file():
                    pa.unlink()
            else:
                pa.parent.mkdir(parents=True, exist_ok=True)
                if it.mode == "take":
                    shutil.copyfile(pb, pa)
                else:
                    pa.write_bytes(data.encode("utf-8"))
            res.append({"p": it.rel, "ok": True})
        except (ValueError, OSError, HTTPException) as e:
            res.append({"p": it.rel, "ok": False, "err": str(getattr(e, "detail", e))})
    return {"results": res, "backup": str(used) if used else ""}


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "index.html").read_text(encoding="utf-8")


GUIDE = """
LOOM - Git & GitHub, made easy.
A private dashboard on 127.0.0.1: stage, commit, branch and push from one
screen, and see any repo as a tree.

USAGE
  python loom.py                    start, then paste a repo path in the page
  python loom.py /path/to/repo      open a local repo straight away
  python loom.py help               show this guide
  python loom.py --port 9000        use another port
  python loom.py --no-browser       don't auto-open the browser

IN THE PAGE
  Repo tree     paste a folder path -> Open (Tree tab), or paste / drag a
                GitHub link (https://github.com/owner/repo) onto the page.
  Daily flow    + stage files -> write a message -> Commit -> Push.
  Command bar   type any git command, e.g. git log --oneline -10
  Compare       Compare tab: pick another folder (or file) to see what differs,
                then merge hunks, join, replace or copy into the repo. The other
                side is never changed; overwritten files are backed up to
                ~/.loom-backups first.
  Help          press ? or click Help for the full in-app guide.

PUSH OVER HTTPS
  1. GitHub > Settings > Developer settings > Personal access tokens.
     Create a fine-grained token with Contents: Read and write.
  2. In Loom's Remote box paste https://github.com/you/repo.git -> Set.
  3. Paste the token in the token box -> Push.
  The token is sent to git for that one push only. It is never written to
  .git/config or to disk (needs git 2.31+).
  To skip pasting it each time, run once in a terminal:
     git config --global credential.helper store
  then push once from the terminal; Loom's normal Push button works after.
"""

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Loom - Git & GitHub, made easy. Local-first dashboard.",
                                 epilog="Run `python loom.py help` for the full guide.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--guide", action="store_true", help="print the full guide")
    ap.add_argument("path", nargs="?", help="repo folder to open")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    o = ap.parse_args()
    if o.guide or (o.path == "help" and not Path("help").is_dir()):
        print(GUIDE)
        raise SystemExit
    url = f"http://127.0.0.1:{o.port}/?token={TOKEN}"
    if o.path:
        url += "&path=" + quote(str(Path(o.path).expanduser().resolve()))
    print("\n  LOOM  ->", url, "\n  (localhost only, token changes every run)\n")
    if not o.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=o.port, log_level="warning")
