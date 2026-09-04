#!/usr/bin/env python3
"""项目台账 - 本地项目管理应用"""
import os
import re
import json
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from flask import Flask, jsonify, request, send_file, render_template

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "projects.db")
ROOT = "/mnt/d/Projects"
PORT = 8765

TEXT_EXT = {".md", ".txt", ".log", ".json", ".py", ".toml", ".yaml", ".yml", ".ini",
            ".conf", ".cfg", ".sh", ".bat", ".ps1", ".csv", ".sql", ".js", ".html", ".css"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}

app = Flask(__name__)

# ---------------- DB ----------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS meta(
            key TEXT PRIMARY KEY, status TEXT DEFAULT '', client TEXT DEFAULT '',
            deadline TEXT DEFAULT '', notes TEXT DEFAULT '', hidden INTEGER DEFAULT 0,
            updated_at TEXT)""")
        try:
            conn.execute("ALTER TABLE meta ADD COLUMN type TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 列已存在

init_db()

def meta_lookup(conn, key):
    """精确匹配，找不到则沿父目录向上找类型继承"""
    row = conn.execute("SELECT * FROM meta WHERE key=?", (key,)).fetchone()
    ptype = ""
    if row:
        ptype = row["type"] or ""
    else:
        parts = key.rstrip("/").split("/")
        for i in range(len(parts) - 1, 2, -1):
            anc = "/".join(parts[:i])
            r = conn.execute("SELECT type FROM meta WHERE key=?", (anc,)).fetchone()
            if r and r["type"]:
                ptype = r["type"]
                break
    return row, ptype

# ---------------- 扫描（后台线程 + 持久化快照，API 永不阻塞） ----------------
_cache = {"data": [], "ts": 0, "scanning": False, "ever_done": False}
_scan_lock = threading.Lock()

def wpath(p):
    """WSL 路径 -> Windows 路径"""
    p = os.path.realpath(p)
    m = re.match(r"^/mnt/([a-z])(/.*)?$", p)
    if m:
        return m.group(1).upper() + ":" + (m.group(2) or "\\").replace("/", "\\")
    return p

def safe(path):
    rp = os.path.realpath(path)
    root = os.path.realpath(ROOT)
    return rp == root or rp.startswith(root + os.sep)

def dir_stat(d):
    """统计目录：文件数、大小、最近修改时间（递归，带文件数上限保护）"""
    n, size, mt = 0, 0, os.path.getmtime(d)
    stack = [d]
    while stack and n < 20000:
        cur = stack.pop()
        try:
            for e in os.scandir(cur):
                try:
                    if e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        n += 1; size += st.st_size; mt = max(mt, st.st_mtime)
                    elif e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                except OSError:
                    pass
        except OSError:
            pass
    return n, size, mt

CODE_SIGNALS = {".git", "package.json", "Podfile", "pubspec.yaml", "requirements.txt",
                "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "*.xcodeproj", "*.csproj"}

def is_code_project(d, depth=2):
    """检测目录是否为源码/开发项目"""
    stack = [(d, 0)]
    while stack:
        cur, lv = stack.pop()
        if lv > depth:
            continue
        try:
            for e in os.scandir(cur):
                if e.is_file():
                    if e.name in CODE_SIGNALS or any(
                            e.name.endswith(s.strip("*")) for s in CODE_SIGNALS if s.startswith("*")):
                        return True
                elif e.is_dir() and not e.name.startswith("."):
                    if e.name in CODE_SIGNALS or e.name.endswith(".xcodeproj"):
                        return True
                    stack.append((e.path, lv + 1))
        except OSError:
            pass
    return False

def has_files_within(d, depth=2):
    """d 的 depth 层以内是否存在任何文件"""
    stack = [(d, 0)]
    while stack:
        cur, lv = stack.pop()
        if lv > depth:
            continue
        try:
            for e in os.scandir(cur):
                if e.is_file(follow_symlinks=False):
                    return True
                if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                    stack.append((e.path, lv + 1))
        except OSError:
            pass
    return False

def scan_projects():
    """扫描：纯容器目录(无直接文件、且近两层内无实质文件)继续下钻，其余作为项目卡片"""
    projects = []
    def walk(dirpath, chain, depth):
        try:
            entries = list(os.scandir(dirpath))
        except OSError:
            return
        dirs = sorted([e for e in entries if e.is_dir(follow_symlinks=False)
                       and not e.name.startswith((".", "$"))], key=lambda e: e.name)
        files = [e for e in entries if e.is_file(follow_symlinks=False)
                 and not e.name.startswith((".", "$"))]
        # 一级(分组根)必下钻；其后：无直接文件且近两层无文件 → 容器
        is_container = dirs and (depth == 1 or (not files and has_files_within(dirpath, 2)))
        if depth >= 4 or not is_container:
            n, size, mt = dir_stat(dirpath)
            projects.append({"key": dirpath, "name": os.path.basename(dirpath),
                             "group": " / ".join(chain[:-1]), "chain": chain,
                             "files": n, "size": size, "mtime": mt})
            return
        for d in dirs:
            walk(d.path, chain + [d.name], depth + 1)

    for g in sorted(os.listdir(ROOT)):
        gp = os.path.join(ROOT, g)
        if os.path.isdir(gp) and not g.startswith((".", "$")):
            walk(gp, [g], 1)
    return projects

def get_data(force=False):
    """只读内存缓存，永不阻塞。force=True 时触发后台重扫（不等待）。"""
    if force and not _cache["scanning"]:
        threading.Thread(target=scan_worker, args=(True,), daemon=True).start()
    return _cache["data"]

def scan_worker(force=False):
    if _cache["scanning"]:
        return
    with _scan_lock:
        if _cache["scanning"]:
            return
        _cache["scanning"] = True
        try:
            projects = scan_projects()
            with db() as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS snapshot(
                    id INTEGER PRIMARY KEY CHECK(id=1), data TEXT)""")
                out = {}
                for p in projects:
                    row, inherited_type = meta_lookup(conn, p["key"])
                    m = dict(row) if row else {}
                    detected = is_code_project(p["key"])
                    ptype = m.get("type") or inherited_type or ("开发项目" if detected else "客户项目")
                    if ptype == "客户项目":
                        # 资源/测试记录/AI平台归档类自动归类，避免淹没主视图
                        if "资源" in p["chain"] or "测试记录" in p["chain"]:
                            ptype = "内部事务"
                        if p["name"] in ("AI共享网络平台", "agentos"):
                            ptype = "归档"
                    p["type"] = ptype
                    p["status"] = m.get("status") or "未标记"
                    p["client"] = m.get("client", "")
                    p["deadline"] = m.get("deadline", "")
                    p["notes"] = m.get("notes", "")
                    p["hidden"] = m.get("hidden", 0)
                    out.setdefault(p["group"], []).append(p)
                groups = [{"group": g, "projects": ps} for g, ps in sorted(out.items())]
                conn.execute("INSERT OR REPLACE INTO snapshot(id,data) VALUES(1,?)",
                             (json.dumps(groups, ensure_ascii=False),))
            _cache.update(data=groups, ts=time.time(), ever_done=True)
        except Exception as e:
            print("[scan_worker] 扫描失败:", e)
        finally:
            _cache["scanning"] = False

def load_snapshot():
    """启动时从 DB 加载上次扫描快照，秒开"""
    try:
        with db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS snapshot(
                id INTEGER PRIMARY KEY CHECK(id=1), data TEXT)""")
            row = conn.execute("SELECT data FROM snapshot WHERE id=1").fetchone()
            if row:
                _cache.update(data=json.loads(row[0]), ts=time.time(), ever_done=True)
    except Exception as e:
        print("[load_snapshot] 失败:", e)

def bg_loop():
    scan_worker()
    while True:
        time.sleep(300)          # 每 5 分钟后台静默刷新
        try:
            scan_worker()
        except Exception as e:
            print("[bg_loop]", e)

def human(n):
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

# ---------------- 路由 ----------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/projects")
def api_projects():
    force = request.args.get("force") == "1"
    if force:
        get_data(force=True)
    return jsonify({"root": wpath(ROOT), "total": sum(len(g["projects"]) for g in _cache["data"]),
                    "groups": _cache["data"], "scanning": _cache["scanning"]})

@app.route("/api/meta", methods=["POST"])
def api_meta():
    d = request.get_json()
    key = d.get("key", "")
    if not safe(key):
        return jsonify({"ok": 0, "msg": "路径越界"}), 400
    with db() as conn:
        conn.execute("""INSERT INTO meta(key,updated_at) VALUES(?,?)
            ON CONFLICT(key) DO NOTHING""", (key, datetime.now().isoformat()))
        conn.execute("""UPDATE meta SET status=COALESCE(?,status),
            client=COALESCE(?,client), deadline=COALESCE(?,deadline),
            notes=COALESCE(?,notes), hidden=COALESCE(?,hidden),
            type=COALESCE(?,type),
            updated_at=? WHERE key=?""",
            (d.get("status"), d.get("client"), d.get("deadline"),
             d.get("notes"),
             None if d.get("hidden") is None else int(d["hidden"]),
             d.get("type"),
             datetime.now().isoformat(), key))
        # 即时反映到缓存（不等下次扫描）
        row = conn.execute("SELECT * FROM meta WHERE key=?", (key,)).fetchone()
        if row:
            m = dict(row)
            for g in _cache["data"]:
                for p in g["projects"]:
                    if p["key"] == key:
                        p["status"] = m.get("status") or "未标记"
                        p["client"] = m.get("client", "")
                        p["deadline"] = m.get("deadline", "")
                        p["notes"] = m.get("notes", "")
                        p["hidden"] = m.get("hidden", 0)
                        if m.get("type"):
                            p["type"] = m["type"]
    return jsonify({"ok": 1})

@app.route("/api/files")
def api_files():
    p = request.args.get("path", ROOT)
    if not safe(p):
        return jsonify({"ok": 0, "msg": "路径越界"}), 400
    items = []
    try:
        for e in os.scandir(p):
            try:
                st = e.stat(follow_symlinks=False)
                items.append({"name": e.name, "dir": e.is_dir(follow_symlinks=False),
                              "size": st.st_size if e.is_file() else 0, "mtime": st.st_mtime})
            except OSError:
                pass
    except OSError as ex:
        return jsonify({"ok": 0, "msg": str(ex)}), 400
    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return jsonify({"ok": 1, "path": wpath(p), "items": items})

@app.route("/api/open", methods=["POST"])
def api_open():
    d = request.get_json()
    p = d.get("path", "")
    if not safe(p):
        return jsonify({"ok": 0}), 400
    wp = wpath(p)
    is_dir = os.path.isdir(p)
    try:
        if is_dir:
            subprocess.Popen(["/mnt/c/Windows/explorer.exe", wp])
        elif d.get("mode") == "select":
            subprocess.Popen(["/mnt/c/Windows/explorer.exe", "/select,", wp])
        else:  # 用系统默认程序打开文件
            subprocess.Popen(["/mnt/c/Windows/explorer.exe", wp])
        return jsonify({"ok": 1})
    except OSError:
        return jsonify({"ok": 0}), 500

@app.route("/api/file")
def api_file():
    p = request.args.get("path", "")
    if not safe(p) or not os.path.isfile(p):
        return jsonify({"ok": 0, "msg": "无效路径"}), 400
    ext = os.path.splitext(p)[1].lower()
    if ext in TEXT_EXT:
        try:
            txt = open(p, "r", encoding="utf-8", errors="replace").read(200000)
            return jsonify({"ok": 1, "type": "text", "name": os.path.basename(p), "content": txt})
        except OSError as ex:
            return jsonify({"ok": 0, "msg": str(ex)}), 400
    if ext in IMG_EXT:
        return send_file(p, mimetype="image/" + ext[1:].replace("jpg", "jpeg"))
    if ext == ".pdf":   # 浏览器原生预览
        return send_file(p, mimetype="application/pdf")
    if ext in (".docx", ".pptx", ".xlsx"):   # 提取正文供预览
        try:
            from build_index import extract_docx, extract_pptx, extract_xlsx
            fn_map = {".docx": extract_docx, ".pptx": extract_pptx, ".xlsx": extract_xlsx}
            content = fn_map[ext](p)[:200000]
            return jsonify({"ok": 1, "type": "text", "name": os.path.basename(p),
                            "content": content or "(未提取到文本)"})
        except Exception as ex:
            return jsonify({"ok": 0, "msg": f"提取失败: {ex}"}), 400
    return send_file(p, as_attachment=True)

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"ok": 1, "hits": [], "files": [], "total": 0})
    ql = q.lower()
    hits = []
    for g in get_data():
        for p in g["projects"]:
            score = 0
            if ql in p["name"].lower(): score += 10
            if ql in p["notes"].lower(): score += 5
            if ql in p["client"].lower(): score += 5
            if score:
                hits.append({**p, "score": score})
    hits.sort(key=lambda x: -x["score"])

    # 全文检索：候选文件 + Python 侧提取全部匹配片段
    like = f"%{q}%"
    cjk = any('\u4e00' <= ch <= '\u9fff' for ch in q)
    rows = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if not cjk:
            try:
                rows = conn.execute(
                    "SELECT path,name,content FROM fts WHERE fts MATCH ? LIMIT 300",
                    (q,)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            rows = conn.execute(
                "SELECT path,name,content FROM fts WHERE content LIKE ? OR name LIKE ? LIMIT 300",
                (like, like)).fetchall()

    root_real = os.path.realpath(ROOT)
    files, total = [], 0
    for r in rows:
        content = r["content"] or ""
        low, snips, hits_n = content.lower(), [], 0
        start = 0
        while len(snips) < 5:
            i = low.find(ql, start)
            if i < 0:
                break
            s = max(0, i - 60)
            e = min(len(content), i + len(q) + 60)
            frag = content[s:e].replace("\n", " ").strip()
            if s > 0: frag = "…" + frag
            if e < len(content): frag += "…"
            snips.append(frag)
            hits_n += 1
            total += 1
            start = i + len(q)
        if not snips and ql in r["name"].lower():
            snips = ["(仅文件名匹配)"]
        if snips:
            files.append({"path": r["path"], "name": r["name"], "full":
                          os.path.join(root_real, r["path"]), "snips": snips, "hits": hits_n})
    return jsonify({"ok": 1, "hits": hits[:30], "files": files, "total": total})

@app.route("/api/reindex", methods=["POST"])
def api_reindex():
    import subprocess, sys
    r = subprocess.run([sys.executable, os.path.join(APP_DIR, "build_index.py")],
                       capture_output=True, text=True, timeout=600)
    return jsonify({"ok": 1, "out": r.stdout.strip()})

if __name__ == "__main__":
    import sys
    if "--daemon" in sys.argv:
        if os.fork():
            sys.exit(0)
        os.setsid()
        if os.fork():
            sys.exit(0)
        sys.stdout.flush()
        fd = os.open(os.path.join(APP_DIR, "server.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        os.dup2(fd, 1); os.dup2(fd, 2)
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
    load_snapshot()                                        # 秒开：先加载快照
    threading.Thread(target=bg_loop, daemon=True).start()  # 后台扫描线程
    print(f"项目台账运行中: http://localhost:{PORT}  (根目录: {ROOT})")
    app.run(host="0.0.0.0", port=PORT, debug=False)
