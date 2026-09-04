#!/usr/bin/env python3
"""全文索引：文件名 + 文本/Office/PDF 文件内容 -> SQLite FTS5"""
import os, re, sqlite3, time, zipfile, subprocess

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(APP_DIR, "projects.db")
ROOT = "/mnt/d/Projects"

TEXT_EXT = {".md", ".txt", ".log", ".json", ".py", ".toml", ".yaml", ".yml", ".ini",
            ".conf", ".cfg", ".sh", ".bat", ".ps1", ".csv", ".sql", ".js", ".html", ".css"}
OFFICE_EXT = {".docx", ".pptx", ".xlsx"}
PDF_EXT = {".pdf"}
SKIP_DIR = {".git", "node_modules", "__pycache__", ".venv", "venv", ".sandbox", ".sandbox-bin"}
MAX_CONTENT = 400_000
MAX_FILES = 50000

def xml_text(xml):
    """从 Office XML 提取可读文本（保留段落分隔）"""
    xml = re.sub(r"</w:p>|</a:p>|</a:fld>|</text:p>", "\n", xml)
    txt = re.sub(r"<[^>]+>", "", xml)
    return txt

def extract_docx(p):
    with zipfile.ZipFile(p) as z:
        return xml_text(z.read("word/document.xml").decode("utf-8", "replace"))

def extract_pptx(p):
    out = []
    with zipfile.ZipFile(p) as z:
        for n in sorted(z.namelist()):
            if re.match(r"ppt/slides/slide\d+\.xml$", n):
                out.append(xml_text(z.read(n).decode("utf-8", "replace")))
    return "\n".join(out)

def extract_xlsx(p):
    out = []
    with zipfile.ZipFile(p) as z:
        if "xl/sharedStrings.xml" in z.namelist():
            out.append(xml_text(z.read("xl/sharedStrings.xml").decode("utf-8", "replace")))
    return "\n".join(out)

def extract_pdf(p):
    try:
        r = subprocess.run(["pdftotext", "-q", p, "-"], capture_output=True,
                           text=True, timeout=30)
        return r.stdout
    except Exception:
        return ""

def index_db():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
        path, name, content, tokenize='unicode61')""")
    conn.commit()
    return conn

def build():
    conn = index_db()
    n_file, n_doc, t0 = 0, 0, time.time()
    conn.execute("DELETE FROM fts")
    rows = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR and not d.startswith(".")]
        for fn in filenames:
            if n_file >= MAX_FILES:
                break
            if fn.startswith("~$"):          # Office 临时锁文件，跳过
                continue
            p = os.path.join(dirpath, fn)
            ext = os.path.splitext(fn)[1].lower()
            rel = os.path.relpath(p, ROOT)
            content = ""
            try:
                if ext in TEXT_EXT and os.path.getsize(p) <= 2_000_000:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(MAX_CONTENT)
                    n_doc += 1
                elif ext in OFFICE_EXT and os.path.getsize(p) <= 20_000_000:
                    try:
                        fn_map = {".docx": extract_docx, ".pptx": extract_pptx, ".xlsx": extract_xlsx}
                        content = fn_map[ext](p)[:MAX_CONTENT]
                        n_doc += 1
                    except Exception:
                        content = ""
                elif ext in PDF_EXT and os.path.getsize(p) <= 50_000_000:
                    content = extract_pdf(p)[:MAX_CONTENT]
                    if content.strip():
                        n_doc += 1
            except OSError:
                pass
            rows.append((rel, fn, content))
            n_file += 1
            if len(rows) >= 2000:
                conn.executemany("INSERT INTO fts VALUES(?,?,?)", rows)
                rows = []
        if n_file >= MAX_FILES:
            break
    if rows:
        conn.executemany("INSERT INTO fts VALUES(?,?,?)", rows)
    conn.commit()
    print(f"索引完成: {n_file} 个文件 (含 {n_doc} 个内容文档), 耗时 {time.time()-t0:.1f}s")

if __name__ == "__main__":
    build()
