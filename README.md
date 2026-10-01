# Loom

**Weave your branches.** A local-first web dashboard for Git & GitHub: stage, commit, branch and push from one screen, see any repo as a tree or a commit graph, and read its files in a built-in viewer.

Loom is a single Python file plus a single HTML page. It runs on your own machine, listens only on `127.0.0.1`, and shells out to the `git` you already have installed.

<!-- Add a screenshot or GIF here, e.g. ![Loom](docs/loom.png) -->

## Features

**Everyday Git, one screen**
- Stage and unstage files with a click, or drag a file onto the staging area
- Write a message and commit, then push
- One-click **Init, Add all, Fetch, Pull, Push, Stash, Pop**
- Switch branches or create a new one from the sidebar
- **Command bar** for any git command, e.g. `git log --oneline -10`
- **Command feed** that records every command with its output and timing. Each card can be copied, re-run, pinned or deleted, and a **Clear** button wipes the unpinned ones. The feed scrolls on its own.

**See your repo**
- **Diff**: colour-coded diffs for working-tree and staged changes
- **Graph**: an animated commit graph with branch and tag labels
- **Tree**: your files as a collapsible tree, with sizes
- **Reflog**: browse recent history and click an entry to create a recovery branch there (nothing is overwritten)

**Read-only file viewer**
- Click any file in the Tree tab to open it in a mini viewer
- Code and text get line numbers and basic syntax colouring (JS/TS, Python, shell, YAML, JSON, CSS, HTML and similar)
- Images (PNG, JPG, GIF, WebP, SVG, ICO, BMP, AVIF) are shown with their pixel size
- Binary and very large files get a short "no preview" message
- Works for both local folders and GitHub repos. Close with the button, a click outside, or `Esc`

**GitHub without cloning**
- Paste or drag a GitHub link (`https://github.com/owner/repo`, including `/tree/branch` links) to map the repo as a **tree**, a **commit graph**, and browse its **files**. Everything is read through the GitHub API.
- Private repos and higher rate limits work with a token

**Push over HTTPS safely**
- Set `origin` from the page and push with a personal access token
- The token is sent to git for that single push only. It is never written to `.git/config` or to disk

**Polish**
- Themes (Noir, Dracula, Tokyo) plus a light/dark switch
- Command palette (`Ctrl/Cmd + K`), keyboard shortcuts, and an in-app help guide (`?`)
- Zen mode

## Requirements

- Python 3.9+
- `git` on your PATH (2.31+ for token-based HTTPS push)
- Python packages: `fastapi` and `uvicorn`

## Install

```bash
git clone https://github.com/<you>/loom.git
cd loom
pip install fastapi uvicorn
```

`loom.py` and `index.html` must stay in the same folder.

## Run

```bash
python loom.py                    # start, then paste a repo path in the page
python loom.py /path/to/repo      # open a local repo straight away
python loom.py --port 9000        # use another port (default 8765)
python loom.py --no-browser       # don't auto-open the browser
python loom.py help               # print the usage guide
```

Loom prints a URL such as `http://127.0.0.1:8765/?token=...` and opens it in your browser. The token changes on every run.

## Usage

### Daily flow
1. Open a repo: paste a folder path in the top box and press Open.
2. Click **+** next to files to stage them (or **Add all**).
3. Type a commit message and press **Commit**.
4. Press **Push**.

### Map a GitHub repo
Paste a GitHub link into the top box, or drag the link onto the page. Then use the **Tree** tab to browse, click a file to read it, or open the **Graph** tab to see its recent commits.

The remote graph shows the history of one branch (the one in the link, or the default branch), up to 100 commits, with branch and tag labels. The local graph shows all branches.

### Push over HTTPS
1. On GitHub go to *Settings → Developer settings → Personal access tokens* and create a fine-grained token with **Contents: Read and write** for your repo.
2. In Loom's **Remote** box paste `https://github.com/you/repo.git` and press **Set**.
3. Paste the token in the token box and press **Push**.

To skip pasting the token each time, run this once in a terminal and push once from there. Loom's normal **Push** button then works:

```bash
git config --global credential.helper store
```

### Keyboard shortcuts

| Key | Action |
| --- | --- |
| `?` | Open help |
| `Ctrl/Cmd + K` | Command palette |
| `Alt + A` | Add all |
| `Alt + S` | Stash |
| `Alt + D` | Diff tab |
| `Alt + G` | Graph tab |
| `Alt + T` | Tree tab |
| `Alt + R` | Reflog tab |
| `Alt + Z` | Toggle Zen mode |
| `Alt + M` | Toggle light / dark |
| `Alt + H` | Help |
| `Esc` | Close the file viewer, palette or help. Press twice to go back to the Diff tab |

The palette lists every available action.

## Security

Loom is meant for use on your own machine.

- It binds to `127.0.0.1` only.
- Requests with any other `Host` header are rejected, which blocks DNS-rebinding attacks.
- Every API call needs a random per-run token, sent in the `X-Loom-Token` header.
- The local file viewer only reads files inside the repo folder you opened. Paths that escape it are refused.
- Tokens you paste are used for one request and are not stored or logged. They are redacted from command output.

Keep in mind that the command bar runs git commands as you, with the same power as your terminal. Do not expose Loom to a network or put it behind a public proxy.

## Limits

- GitHub allows 60 unauthenticated API requests per hour. Each repo mapping uses 2 requests, each remote graph about 3, and each remote file view 1. Add a token if you hit the limit.
- GitHub may truncate the file list of very large repos. Loom tells you when that happens.
- The file viewer previews text up to about 1.5 MB and images up to 8 MB.
- The viewer is read-only by design. Edit files in your usual editor.
- Reflog is only available for local repos.

## Project layout

```
loom/
├── loom.py      # FastAPI backend: runs git, talks to the GitHub API, serves the page
└── index.html   # the whole UI (HTML, CSS and JavaScript, no build step)
```

### API (internal)

All routes live under `/api/` and require the token header.

| Route | Purpose |
| --- | --- |
| `POST /api/run` | Run a git action in a local repo |
| `POST /api/file` | Read a local file for the viewer |
| `POST /api/remote` | Map a GitHub repo (file list) |
| `POST /api/remote_graph` | Commit graph of a GitHub repo |
| `POST /api/remote_file` | Read a GitHub file for the viewer |

## Contributing

Issues and pull requests are welcome. There is no build step: edit `loom.py` or `index.html` and restart.

## License

Add a license of your choice (for example MIT) as a `LICENSE` file and name it here.
