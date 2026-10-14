#!/usr/bin/env python3
"""hrld: HRL Hyper OS Feudal Agent Daemon + Interactive macOS Desktop (stdlib only)."""
import http.server, json, os, pathlib, platform, shlex, sqlite3, subprocess, sys, urllib.parse, webbrowser

PORT = int(os.environ.get("HRL_PORT", "8765"))
Q_FILE = pathlib.Path(__file__).with_name("q_table.json")
DB_FILE = pathlib.Path(__file__).with_name("q_table.db")

# Platform-specific native command fallbacks
if platform.system() == "Darwin":
    SUBGOALS = {
        "system_status": ["uname", "-a"],
        "list_files": ["ls", "-la"],
        "disk_usage": ["df", "-h"],
        "memory_top": ["vm_stat"],
        "network_info": ["ifconfig"],
        "whoami_uptime": ["uptime"],
    }
elif platform.system() == "Windows":
    SUBGOALS = {
        "system_status": ["cmd", "/c", "ver"],
        "list_files": ["cmd", "/c", "dir"],
        "disk_usage": ["powershell", "-NoProfile", "-Command", "Get-PSDrive -PSProvider FileSystem | Select-Object Name, Used, Free"],
        "memory_top": ["powershell", "-NoProfile", "-Command", "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 ProcessName, CPU, WorkingSet64"],
        "network_info": ["ipconfig"],
        "whoami_uptime": ["whoami"],
    }
else:
    SUBGOALS = {
        "system_status": ["uname", "-a"],
        "list_files": ["ls", "-la"],
        "disk_usage": ["df", "-h"],
        "memory_top": ["free", "-h"],
        "network_info": ["ip", "addr"],
        "whoami_uptime": ["uptime"],
    }

# Tabular Q-table with SQLite persistence and JSON file fallback
Q_TABLE = {k: 0.0 for k in SUBGOALS}

def init_db():
    if Q_FILE.exists():
        try:
            Q_TABLE.update(json.loads(Q_FILE.read_text()))
        except Exception:
            pass
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS q_values (subgoal TEXT PRIMARY KEY, score REAL)")
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM q_values")
            if cur.fetchone()[0] == 0:
                for k, v in Q_TABLE.items():
                    cur.execute("INSERT OR REPLACE INTO q_values (subgoal, score) VALUES (?, ?)", (k, v))
                conn.commit()
            else:
                for row in cur.execute("SELECT subgoal, score FROM q_values"):
                    Q_TABLE[row[0]] = float(row[1])
    except Exception:
        pass

init_db()


def save_q():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            for k, v in Q_TABLE.items():
                conn.execute("INSERT OR REPLACE INTO q_values (subgoal, score) VALUES (?, ?)", (k, v))
            conn.commit()
    except Exception:
        pass
    Q_FILE.write_text(json.dumps(Q_TABLE, indent=2))


def update_q(subgoal: str, reward: float, lr: float = 0.25) -> float:
    Q_TABLE[subgoal] = round(Q_TABLE.get(subgoal, 0.0) + lr * (reward - Q_TABLE.get(subgoal, 0.0)), 3)
    save_q()
    return Q_TABLE[subgoal]


def manager_select(goal: str) -> str:
    g = goal.lower()
    keyword_map = {
        "status": "system_status", "uname": "system_status", "kernel": "system_status", "os": "system_status",
        "file": "list_files", "ls": "list_files", "dir": "list_files", "finder": "list_files",
        "disk": "disk_usage", "storage": "disk_usage", "df": "disk_usage",
        "mem": "memory_top", "ram": "memory_top", "ps": "memory_top", "top": "memory_top",
        "net": "network_info", "ip": "network_info", "wifi": "network_info",
        "up": "whoami_uptime", "time": "whoami_uptime", "whoami": "whoami_uptime", "user": "whoami_uptime",
    }
    matched = {v for k, v in keyword_map.items() if k in g} | {s for s in SUBGOALS if s in g}
    candidates = list(matched) or list(SUBGOALS.keys())
    return max(candidates, key=lambda s: Q_TABLE.get(s, 0.0))


def worker_exec(subgoal: str, dry_run: bool = False) -> dict:
    cmd = SUBGOALS.get(subgoal)
    if not cmd:
        return {"ok": False, "reward": -1.0, "error": "unknown subgoal", "q_table": Q_TABLE}
    if dry_run:
        q = update_q(subgoal, 1.0)
        return {"ok": True, "subgoal": subgoal, "cmd": shlex.join(cmd) if hasattr(shlex, 'join') else ' '.join(cmd), "out": "(dry-run)", "reward": 1.0, "q": q, "q_table": Q_TABLE}
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    r = 1.0 if proc.returncode == 0 else -1.0
    q = update_q(subgoal, r)
    out = (proc.stdout or proc.stderr).strip()
    return {"ok": proc.returncode == 0, "subgoal": subgoal, "cmd": shlex.join(cmd) if hasattr(shlex, 'join') else ' '.join(cmd), "out": out[:3000], "reward": r, "q": q, "q_table": Q_TABLE}


def handle_goal(payload: dict) -> dict:
    goal = str(payload.get("goal", "")).strip()
    if not goal:
        return {"ok": False, "error": "empty goal", "q_table": Q_TABLE}
    return worker_exec(manager_select(goal), dry_run=bool(payload.get("dry_run", False)))


def list_dir(path_str: str) -> dict:
    p = pathlib.Path(path_str or ".").expanduser().resolve()
    if not p.exists() or not p.is_dir():
        p = pathlib.Path.cwd()
    items = [{"name": "..", "path": str(p.parent), "is_dir": True, "size": ""}]
    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:80]:
        try:
            st = child.stat()
            items.append({"name": child.name, "path": str(child), "is_dir": child.is_dir(), "size": "" if child.is_dir() else f"{st.st_size} B"})
        except OSError:
            pass
    return {"cwd": str(p), "items": items}


def run_shell(cmd_str: str) -> dict:
    if not cmd_str.strip():
        return {"ok": True, "out": ""}
    try:
        proc = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, timeout=5)
        return {"ok": proc.returncode == 0, "out": ((proc.stdout or "") + (proc.stderr or "")).strip()[:3000]}
    except Exception as e:
        return {"ok": False, "out": str(e)}


def free_port(port: int):
    """Safely terminate any lingering process listening on the specified port."""
    if platform.system() == "Windows":
        try:
            res = subprocess.run(f"netstat -ano | findstr :{port}", shell=True, capture_output=True, text=True)
            for line in res.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 5 and "LISTENING" in parts:
                    pid = parts[-1]
                    subprocess.run(f"taskkill /PID {pid} /F", shell=True, capture_output=True)
        except Exception:
            pass
    else:
        subprocess.run(f"lsof -ti tcp:{port} | xargs kill -9", shell=True, stderr=subprocess.DEVNULL)


DESKTOP_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>HRL Hyper OS</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, "SF Pro Display", "Inter", sans-serif; }
  body { height: 100vh; overflow: hidden; background: radial-gradient(circle at 20% 20%, #1d3557, #0f172a 60%, #050811 100%); color: #f5f5f7; user-select: none; }
  #menubar { height: 28px; background: rgba(20,20,24,0.75); backdrop-filter: blur(20px); display: flex; align-items: center; justify-content: space-between; padding: 0 14px; font-size: 13px; border-bottom: 1px solid rgba(255,255,255,0.12); z-index: 9000; position: relative; }
  .menu-left, .menu-right { display: flex; gap: 16px; align-items: center; }
  .menu-item { cursor: pointer; opacity: 0.88; } .menu-item:hover { opacity: 1; }
  .window { position: absolute; background: rgba(28,28,34,0.84); backdrop-filter: blur(26px); border: 1px solid rgba(255,255,255,0.2); border-radius: 12px; box-shadow: 0 22px 55px rgba(0,0,0,0.55); display: flex; flex-direction: column; resize: both; overflow: hidden; min-width: 280px; min-height: 180px; }
  .titlebar { height: 34px; background: rgba(255,255,255,0.06); display: flex; align-items: center; padding: 0 12px; gap: 8px; border-bottom: 1px solid rgba(255,255,255,0.08); cursor: grab; flex-shrink: 0; }
  .titlebar:active { cursor: grabbing; }
  .dot { width: 12px; height: 12px; border-radius: 50%; display: inline-block; cursor: pointer; }
  .red { background: #ff5f56; } .yellow { background: #ffbd2e; } .green { background: #27c93f; }
  .w-title { margin-left: 8px; font-size: 12px; color: #ccc; font-weight: 600; pointer-events: none; }
  .w-body { padding: 12px; font-family: "SF Mono", Menlo, monospace; font-size: 12px; line-height: 1.5; flex: 1; overflow-y: auto; user-select: text; white-space: pre-wrap; }
  .toolbar { display: flex; gap: 8px; align-items: center; padding: 8px 12px; background: rgba(0,0,0,0.25); border-top: 1px solid rgba(255,255,255,0.08); }
  .btn { background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.18); color: #fff; padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 12px; }
  .btn:hover { background: rgba(255,255,255,0.22); }
  .sh-input { flex: 1; background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.2); color: #38ef7d; padding: 5px 8px; border-radius: 6px; font-family: "SF Mono", monospace; font-size: 12px; outline: none; }
  .file-row { display: flex; justify-content: space-between; padding: 5px 8px; border-radius: 6px; cursor: pointer; }
  .file-row:hover { background: rgba(10,132,255,0.35); }
  .q-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.08); }
  .badge { background: #0a84ff; padding: 2px 8px; border-radius: 99px; font-size: 11px; min-width: 64px; text-align: center; }
  #spotlight { position: fixed; top: 14%; left: 50%; transform: translateX(-50%); width: 540px; background: rgba(30,30,38,0.92); backdrop-filter: blur(30px); border: 1px solid rgba(255,255,255,0.28); border-radius: 14px; padding: 12px 16px; box-shadow: 0 25px 70px rgba(0,0,0,0.65); z-index: 9500; display: flex; gap: 10px; align-items: center; }
  #spotlight input { flex: 1; background: transparent; border: none; outline: none; color: #fff; font-size: 16px; }
  #dock { position: fixed; bottom: 12px; left: 50%; transform: translateX(-50%); height: 56px; background: rgba(40,40,48,0.68); backdrop-filter: blur(24px); border: 1px solid rgba(255,255,255,0.2); border-radius: 18px; display: flex; align-items: center; padding: 0 14px; gap: 10px; box-shadow: 0 12px 32px rgba(0,0,0,0.5); z-index: 9000; }
  .dock-btn { padding: 7px 13px; border-radius: 12px; background: rgba(255,255,255,0.1); cursor: pointer; font-size: 13px; font-weight: 500; transition: transform 0.15s; }
  .dock-btn:hover { transform: translateY(-4px) scale(1.06); background: rgba(255,255,255,0.22); }
</style></head>
<body>
  <div id="menubar">
    <div class="menu-left">
      <strong class="menu-item"> HRL Hyper OS</strong>
      <span class="menu-item" onclick="showWin('win-term')">Terminal</span>
      <span class="menu-item" onclick="showWin('win-finder')">Finder</span>
      <span class="menu-item" onclick="showWin('win-q')">HRL Brain</span>
    </div>
    <div class="menu-right">
      <span class="menu-item" onclick="toggleSpotlight()">⌘K Spotlight</span>
      <span id="clock"></span>
    </div>
  </div>

  <div id="spotlight">
    <span>🔍</span>
    <input id="goalInput" placeholder="Spotlight / HRL Goal (e.g. 'disk usage', 'list files', 'memory', 'network')..." autofocus />
  </div>

  <!-- Window 1: Terminal + HRL Reward Feedback -->
  <div class="window" id="win-term" style="top: 52px; left: 24px; width: 520px; height: 360px; z-index: 10;">
    <div class="titlebar" onmousedown="dragStart(event, 'win-term')">
      <span class="dot red" onclick="hideWin('win-term')"></span>
      <span class="dot yellow" onclick="hideWin('win-term')"></span>
      <span class="dot green" onclick="maxWin('win-term')"></span>
      <span class="w-title">POSIX / Windows Hybrid Terminal</span>
    </div>
    <div class="w-body" id="termOut">Booting HRL Hyper OS...</div>
    <div class="toolbar">
      <span style="font-size:11px;color:#aaa;">Subgoal: <strong id="lastSubgoal" style="color:#fff;">system_status</strong></span>
      <button class="btn" onclick="sendReward(1)" title="Reinforce subgoal (+1)">👍 Reward +1</button>
      <button class="btn" onclick="sendReward(-1)" title="Penalize subgoal (-1)">👎 Penalize -1</button>
      <input class="sh-input" id="shInput" placeholder="$ run shell command (Enter)..." />
    </div>
  </div>

  <!-- Window 2: Finder File Browser -->
  <div class="window" id="win-finder" style="top: 52px; left: 564px; width: 420px; height: 360px; z-index: 11;">
    <div class="titlebar" onmousedown="dragStart(event, 'win-finder')">
      <span class="dot red" onclick="hideWin('win-finder')"></span>
      <span class="dot yellow" onclick="hideWin('win-finder')"></span>
      <span class="dot green" onclick="maxWin('win-finder')"></span>
      <span class="w-title" id="finderTitle">Finder — /</span>
    </div>
    <div class="w-body" id="finderOut"></div>
  </div>

  <!-- Window 3: Feudal HRL Q-Table Monitor -->
  <div class="window" id="win-q" style="top: 430px; left: 24px; width: 960px; height: 220px; z-index: 12;">
    <div class="titlebar" onmousedown="dragStart(event, 'win-q')">
      <span class="dot red" onclick="hideWin('win-q')"></span>
      <span class="dot yellow" onclick="hideWin('win-q')"></span>
      <span class="dot green" onclick="maxWin('win-q')"></span>
      <span class="w-title">Feudal HRL Manager — Persistent Subgoal Q-Table (q_table.db / q_table.json)</span>
    </div>
    <div class="w-body" id="qOut"></div>
  </div>

  <div id="dock">
    <div class="dock-btn" onclick="showWin('win-term'); runGoal('kernel status')">🖥️ Terminal</div>
    <div class="dock-btn" onclick="showWin('win-finder'); loadFinder('.')">📁 Finder</div>
    <div class="dock-btn" onclick="showWin('win-q')">🧠 HRL Brain</div>
    <div class="dock-btn" onclick="runGoal('disk storage')">💾 Disk</div>
    <div class="dock-btn" onclick="runGoal('memory ram')">⚡ Memory</div>
    <div class="dock-btn" onclick="runGoal('network ip')">🌐 Network</div>
    <div class="dock-btn" onclick="toggleSpotlight()">🔍 Spotlight</div>
  </div>

<script>
  let topZ = 20, currentSubgoal = 'system_status';
  function focusWin(id) { const w = document.getElementById(id); w.style.zIndex = ++topZ; }
  function showWin(id) { const w = document.getElementById(id); w.style.display = 'flex'; focusWin(id); }
  function hideWin(id) { document.getElementById(id).style.display = 'none'; }
  function maxWin(id) {
    const w = document.getElementById(id); focusWin(id);
    if (w.dataset.max === '1') { w.style.top = w.dataset.t; w.style.left = w.dataset.l; w.style.width = w.dataset.w; w.style.height = w.dataset.h; w.dataset.max = '0'; }
    else { w.dataset.t = w.style.top; w.dataset.l = w.style.left; w.dataset.w = w.style.width; w.dataset.h = w.style.height; w.style.top = '34px'; w.style.left = '10px'; w.style.width = 'calc(100vw - 20px)'; w.style.height = 'calc(100vh - 108px)'; w.dataset.max = '1'; }
  }
  function dragStart(e, id) {
    if (e.target.classList.contains('dot')) return;
    const w = document.getElementById(id); focusWin(id);
    const sx = e.clientX - w.offsetLeft, sy = e.clientY - w.offsetTop;
    function move(ev) { w.style.left = Math.max(0, ev.clientX - sx) + 'px'; w.style.top = Math.max(28, ev.clientY - sy) + 'px'; }
    function up() { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); }
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
  }
  const input = document.getElementById('goalInput');
  function toggleSpotlight() {
    const s = document.getElementById('spotlight');
    s.style.display = s.style.display === 'none' ? 'flex' : 'none';
    if (s.style.display === 'flex') input.focus();
  }
  window.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); toggleSpotlight(); }
    if (e.key === 'Escape') document.getElementById('spotlight').style.display = 'none';
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && input.value.trim()) {
      runGoal(input.value.trim()); input.value = ''; document.getElementById('spotlight').style.display = 'none';
    }
  });
  document.getElementById('shInput').addEventListener('keydown', async e => {
    if (e.key === 'Enter' && e.target.value.trim()) {
      const cmd = e.target.value.trim(); e.target.value = '';
      const r = await fetch('/api/sh?cmd=' + encodeURIComponent(cmd));
      const d = await r.json();
      const term = document.getElementById('termOut');
      term.textContent += `\\n\\n$ ${cmd}\\n${d.out}`;
      term.scrollTop = term.scrollHeight;
    }
  });
  async function runGoal(goal) {
    showWin('win-term');
    const r = await fetch('/api/goal?goal=' + encodeURIComponent(goal));
    const d = await r.json();
    currentSubgoal = d.subgoal || currentSubgoal;
    document.getElementById('lastSubgoal').textContent = currentSubgoal;
    document.getElementById('termOut').textContent =
      `$ hrl-manager --goal "${goal}"\\n→ Subgoal: ${d.subgoal} (auto-reward: ${d.reward}, Q=${d.q})\\n$ ${d.cmd}\\n\\n${d.out}`;
    renderQ(d.q_table || {});
  }
  async function sendReward(val) {
    const r = await fetch(`/api/reward?subgoal=${encodeURIComponent(currentSubgoal)}&r=${val}`);
    const d = await r.json();
    renderQ(d.q_table || {});
  }
  function renderQ(qt) {
    document.getElementById('qOut').innerHTML = Object.entries(qt)
      .map(([k, v]) => `<div class="q-row"><span><strong>${k}</strong></span><div><button class="btn" onclick="currentSubgoal='${k}';sendReward(1)">👍</button> <button class="btn" onclick="currentSubgoal='${k}';sendReward(-1)">👎</button> <span class="badge">Q = ${v}</span></div></div>`).join('');
  }
  async function loadFinder(path) {
    const r = await fetch('/api/files?path=' + encodeURIComponent(path));
    const d = await r.json();
    document.getElementById('finderTitle').textContent = 'Finder — ' + d.cwd;
    document.getElementById('finderOut').innerHTML = d.items.map(it =>
      `<div class="file-row" onclick="${it.is_dir ? `loadFinder('${it.path.replace(/'/g, "\\\\'")}')` : `document.getElementById('shInput').value='head -n 25 ${it.path.replace(/'/g, "\\\\'")}';showWin('win-term');`}">
        <span>${it.is_dir ? '📁' : '📄'} ${it.name}</span><span style="color:#888">${it.size}</span>
      </div>`
    ).join('');
  }
  setInterval(() => { document.getElementById('clock').textContent = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}); }, 1000);
  runGoal('kernel status');
  loadFinder('.');
</script></body></html>
"""


class HRLHTTPHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/goal":
            self._json(handle_goal({"goal": qs.get("goal", ["system_status"])[0]}))
        elif parsed.path == "/api/reward":
            sg = qs.get("subgoal", ["system_status"])[0]
            r = float(qs.get("r", ["1"])[0])
            q = update_q(sg, r) if sg in SUBGOALS else 0.0
            self._json({"ok": sg in SUBGOALS, "subgoal": sg, "q": q, "q_table": Q_TABLE})
        elif parsed.path == "/api/files":
            self._json(list_dir(qs.get("path", ["."])[0]))
        elif parsed.path == "/api/sh":
            self._json(run_shell(qs.get("cmd", [""])[0]))
        else:
            body = DESKTOP_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)


def self_check():
    res1 = handle_goal({"goal": "check disk storage", "dry_run": True})
    assert res1["ok"] and res1["subgoal"] == "disk_usage", res1
    q_before = Q_TABLE["disk_usage"]
    q_after = update_q("disk_usage", -1.0)
    assert q_after < q_before and (Q_FILE.exists() or DB_FILE.exists()), (q_before, q_after)
    files = list_dir(".")
    assert "cwd" in files and len(files["items"]) >= 1, files
    sh = run_shell("echo hrl_ok")
    assert sh["ok"] and "hrl_ok" in sh["out"], sh
    print("self-check passed: Finder items =", len(files["items"]), "| Q saved to", Q_FILE.name, "&", DB_FILE.name)


if __name__ == "__main__":
    if "--check" in sys.argv:
        self_check()
        sys.exit(0)
    free_port(PORT)
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    url = f"http://127.0.0.1:{PORT}"
    print(f"Booting HRL Hyper OS Live Desktop at {url} (Ctrl+C to stop)")
    webbrowser.open(url)
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), HRLHTTPHandler) as httpd:
        httpd.serve_forever()
