# -*- coding: utf-8 -*-
"""引路人 · 核心层
配置 / SQLite 存储 / 知识库检索（TF-IDF 余弦）/ 大模型可选适配

设计约束：只使用 Python 标准库。
理由：小工具必须能在任何一台内网机器上 `python server.py` 直接跑起来。
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 路径与常量

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RUNTIME_DIR = DATA_DIR / "runtime"
WEB_DIR = BASE_DIR / "web"
DB_PATH = RUNTIME_DIR / "mentor.db"
SETTINGS_PATH = RUNTIME_DIR / "settings.json"
FILES_DIR = RUNTIME_DIR / "files"     # 任务/反馈的附件（运行数据，不入版本库）
FAQ_PATH = DATA_DIR / "faq.json"
PROJECTS_PATH = DATA_DIR / "projects.json"

DEFAULT_SETTINGS = {
    "llm": {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "model": "",
        "timeout": 60,
    },
    "retrieval": {
        # 分段余弦分数（标题段权重 ×3，正文段 ×1），实测范围约 0~3。
        # 覆盖率高（问句里多数词都能在文档中找到）时用 match_threshold 判定；
        # 覆盖率低时说明问句里很多词文档里根本没有，此时要求更高的 strong_threshold。
        # 宁可多转人工，也不要答错——培养场景里错误答案的代价远大于多问一句。
        "match_threshold": 0.9,
        "strong_threshold": 1.6,
        "min_coverage": 0.30,
        "top_k": 4,
        # 同一问题累计被问到多少次，自动生成 FAQ 草稿
        "draft_trigger_count": 2,
        "fingerprint_similarity": 0.62,
    },
    "mentor": {
        "escalate_contact": "导师",
    },
}

# 敏感话题分两档，区别很重要：
#
# HARD —— 无论知识库有没有，一律转人工。这类问题的错误答案会造成不可逆的后果
#         （钱、人事、法务），而且答案高度依赖个人情况，不存在"标准答案"。
#
# SOFT —— 只要求"必须有明确出处"。像"怎么申请权限"这类，公司 FAQ 里本来就写了
#         标准流程，照文档答并附出处是安全的；知识库没有时才转人工。
#         一刀切会把大量有标准答案的流程问题也推给导师，反而增加负担。
HARD_PATTERNS = [
    r"薪[资水]|工资|月薪|年包|涨薪|调薪|年终奖|绩效(结果|评分|等级)|考核结果",
    r"离职|辞退|裁员|劳动(合同|仲裁)|竞业|赔偿|n\+1",
    r"社保|公积金|户口|落户|签证|个税|报税",
    r"投诉|举报|性骚扰|劳动纠纷|法律|起诉",
]
SOFT_PATTERNS = [
    r"权限|开通账号|管理员|root|生产库|线上数据库|密钥|secret|token|密码|api[ _\-]?key",
    r"报销|发票",
]


def sensitive_hit(text: str):
    """返回 (档位, 命中词)。档位为 'hard' / 'soft' / None。"""
    for pat in HARD_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return "hard", m.group(0)
    for pat in SOFT_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return "soft", m.group(0)
    return None, None


# ---------------------------------------------------------------- 基础工具

def ensure_dirs() -> None:
    for d in (DATA_DIR, RUNTIME_DIR, WEB_DIR, FILES_DIR):
        d.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "knowledge").mkdir(parents=True, exist_ok=True)
    for sub in ("paths", "scenarios", "rubrics", "env_checks"):
        (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)


LOAD_WARNINGS: list = []


def warn(msg: str) -> None:
    """记录内容文件的问题。绝不静默——导师手改文件写错时，工具必须出声。"""
    if msg not in LOAD_WARNINGS:
        LOAD_WARNINGS.append(msg)
        sys.stderr.write(f"[引路人·内容文件告警] {msg}\n")


def load_json(path: Path, default):
    # utf-8-sig：容忍 Windows 记事本保存时加的 BOM。
    # 这个工具的前提就是"导师用文本编辑器直接改内容文件"，不兼容 BOM 等于不可用。
    if not path.exists():
        return default
    raw = None
    for enc in ("utf-8-sig", "utf-16", "gbk"):
        try:
            raw = path.read_text(encoding=enc)
            break
        except (OSError, UnicodeDecodeError):
            continue
    if raw is None:
        warn(f"{path.name}：无法识别文件编码，已按空内容处理")
        return default
    try:
        return json.loads(raw)
    except ValueError as exc:
        warn(f"{path.name}：JSON 格式错误，该文件已被忽略，请检查逗号与引号 —— {exc}")
        return default


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # newline="\n"：Windows 文本模式默认会把 \n 翻译成 CRLF，而仓库 .gitattributes
    # 规定 LF。不固定行尾的话，程序回写一次内容文件，git status 就会显示整个文件被改了。
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_dir_json(subdir: str) -> list:
    out = []
    d = DATA_DIR / subdir
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        item = load_json(p, None)
        if isinstance(item, dict):
            item.setdefault("_file", p.name)
            out.append(item)
    return out


def load_projects() -> list:
    """项目列表（data/projects.json）：每个项目是独立的知识空间。"""
    data = load_json(PROJECTS_PATH, {"projects": []})
    if not isinstance(data, dict) or "projects" not in data:
        return []
    return [p for p in data["projects"] if isinstance(p, dict)]


def save_projects(projects: list) -> None:
    save_json(PROJECTS_PATH, {"projects": projects})


def load_users_file() -> list:
    """users.json 是身份与 PIN 的唯一来源（pin 可选）。每次现读，改完即生效。"""
    cfg = load_json(DATA_DIR / "users.json", {})
    users = cfg.get("users") if isinstance(cfg, dict) else None
    return [u for u in (users or []) if isinstance(u, dict) and u.get("id")]


def auth_enabled() -> bool:
    """只要有一个用户配置了 4 位 pin，全站就进入「选人 + PIN」身份保护模式；
    一个都没配则完全开放（保持小工具零配置即用的特点）。"""
    return any(str(u.get("pin") or "").strip() for u in load_users_file())


def strip_frontmatter(text: str):
    """解析 Markdown 的简单 frontmatter（只支持 key: value 与逗号分隔列表）。"""
    text = text.lstrip("\ufeff")          # 容忍记事本写入的 BOM
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---"):
        return {}, text.strip()
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text.strip()
    raw = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("\n")
    meta = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if "," in v:
            meta[k.strip()] = [x.strip() for x in v.split(",") if x.strip()]
        else:
            meta[k.strip()] = v
    return meta, body.strip()


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    return [str(value)]


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def is_overdue(review_date: str) -> bool:
    """复核日期是否已过期。空值视为过期（提醒补填）。"""
    if not review_date:
        return True
    try:
        return datetime.strptime(str(review_date)[:10], "%Y-%m-%d").date() < datetime.now().date()
    except ValueError:
        return True


# ---------------------------------------------------------------- 检索

_ASCII_RE = re.compile(r"[a-z0-9][a-z0-9_.+#/\-]*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

# 中英文停用词。除了常见虚词，还刻意去掉了"怎么办""有什么"这类
# 跨词边界的噪声二元组——它们在标题里到处都是，会把不相关的问题拉高分数。
_STOP = {
    "的", "了", "是", "我", "你", "他", "在", "有", "和", "就", "不", "人", "都",
    "一", "上", "也", "很", "到", "说", "要", "去", "会", "着", "没", "看", "好",
    "自己", "这", "那", "什么", "怎么", "如何", "为什么", "哪些", "哪个", "可以",
    "需要", "请问", "一下", "吗", "呢", "啊", "吧", "应该", "有没有", "是不是",
    "了怎", "么办", "有什", "么样", "我的", "我在", "我要", "想问", "帮我",
    "the", "a", "an", "is", "are", "how", "what", "why", "to", "of", "in",
    "on", "for", "and", "or", "i", "my", "do", "does", "can", "should",
}


def tokenize(text: str) -> list:
    """中文用字符二元组，英文用单词。无需分词库，召回与精度平衡较好。"""
    text = (text or "").lower()
    tokens = [t for t in _ASCII_RE.findall(text) if t not in _STOP]
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            if run not in _STOP:
                tokens.append(run)
        else:
            for i in range(len(run) - 1):
                bg = run[i:i + 2]
                if bg not in _STOP:
                    tokens.append(bg)
    return tokens


def _split_segments(doc: dict) -> list:
    """把一篇文档切成若干"段"分别建索引。

    为什么分段：整篇做余弦会被长文档稀释——一个 2000 字的 FAQ 里出现一次"周报"，
    余弦值会低到无法使用；而换成语义相近的短段落，匹配会准确得多。
    """
    title = doc.get("title", "") or ""
    tags = " ".join(_as_list(doc.get("tags")))
    segs = [(f"{title} {title} {title} {tags} {tags}", 3.0)]
    text = doc.get("text") or ""
    if doc.get("kind") == "faq":
        parts = re.split(r"[。！？；\n]+", text)
    else:
        parts = re.split(r"\n\s*\n+", text)
    for p in parts:
        p = p.strip().lstrip("#").strip()
        if len(p) >= 6:
            segs.append((p, 1.0))
    return segs


class SearchIndex:
    """TF-IDF + 余弦相似度，按段落建索引，分数落在 0~1，便于设定"敢不敢回答"的阈值。"""

    def __init__(self, docs: list):
        """docs: [{'id','kind','title','text','tags','payload'}]"""
        self.docs = docs
        self.vectors = []          # 与 self.meta 一一对应
        self.meta = []             # (doc_index, weight)
        self.idf = {}
        self._build()

    def _build(self) -> None:
        tokenized = []
        self.doc_tokens = []
        for di, d in enumerate(self.docs):
            tokens_here = set()
            for text, weight in _split_segments(d):
                toks = tokenize(text)
                tokenized.append(toks)
                self.meta.append((di, weight))
                tokens_here |= set(toks)
            self.doc_tokens.append(tokens_here)
        n = len(tokenized)
        if n == 0:
            return
        df = Counter()
        for toks in tokenized:
            df.update(set(toks))
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
        for toks in tokenized:
            self.vectors.append(self._vector(toks))

    def _vector(self, tokens: list):
        if not tokens:
            return {}
        tf = Counter(tokens)
        total = sum(tf.values())
        vec = {}
        for t, c in tf.items():
            w = (c / total) * self.idf.get(t, 0.0)
            if w:
                vec[t] = w
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm:
            for k in vec:
                vec[k] /= norm
        return vec

    def search(self, query: str, top_k: int = 4, kind: str | None = None,
               project: str | None = None) -> list:
        """project=None 检索全部；指定项目时只命中该项目知识 + 无归属的通用知识。"""
        qtokens = tokenize(query)
        qv = self._vector(qtokens)
        if not qv:
            return []
        qset = set(qtokens)

        best = {}
        for i, dv in enumerate(self.vectors):
            if not dv:
                continue
            di, weight = self.meta[i]
            d = self.docs[di]
            if kind and d.get("kind") != kind:
                continue
            if project is not None and str(d.get("project") or "") not in ("", project):
                continue
            small, big = (qv, dv) if len(qv) <= len(dv) else (dv, qv)
            s = sum(w * big.get(t, 0.0) for t, w in small.items())
            if s <= 0.0:
                continue
            s *= weight
            cur = best.get(di)
            if cur is None or s > cur[0]:
                best[di] = (s, i)

        out = []
        for di, (s, i) in best.items():
            d = self.docs[di]
            doc_tokens = self.doc_tokens[di]
            matched = qset & doc_tokens
            out.append({
                "score": round(s, 4),
                "coverage": round(len(matched) / len(qset), 3) if qset else 0.0,
                "matched": len(matched),
                "query_terms": len(qset),
                "id": d["id"], "kind": d["kind"], "title": d["title"],
                "text": d["text"], "tags": d["tags"], "payload": d["payload"],
            })
        out.sort(key=lambda x: -x["score"])
        return out[:top_k]



# ---------------------------------------------------------------- 知识库

class KnowledgeBase:
    def __init__(self):
        self.entries = []
        self.faq = {"items": []}
        self.index = None

    def load(self) -> None:
        self.entries = []
        kdir = DATA_DIR / "knowledge"
        if kdir.is_dir():
            for p in sorted(kdir.glob("*.md")):
                meta, body = strip_frontmatter(
                    p.read_text(encoding="utf-8-sig", errors="replace"))
                self.entries.append({
                    "id": meta.get("id") or p.stem,
                    "title": meta.get("title") or p.stem,
                    "track": _as_list(meta.get("track")),
                    "tags": _as_list(meta.get("tags")),
                    "project": str(meta.get("project") or ""),
                    "owner": meta.get("owner", "未指定"),
                    "review_date": meta.get("review_date", ""),
                    "source": meta.get("source", f"知识库 / {p.name}"),
                    "body": body,
                    "file": p.name,
                })
        self.faq = load_json(FAQ_PATH, {"items": []})
        if not isinstance(self.faq, dict) or "items" not in self.faq:
            self.faq = {"items": []}

        docs = []
        for e in self.entries:
            docs.append({
                "id": e["id"], "kind": "kb", "title": e["title"],
                "text": e["body"], "tags": e["tags"] + e["track"],
                "project": e.get("project", ""),
                "payload": e,
            })
        for it in self.faq["items"]:
            if it.get("status") != "published":
                continue
            docs.append({
                "id": it.get("id", ""), "kind": "faq", "title": it.get("q", ""),
                "text": it.get("a", ""), "tags": _as_list(it.get("tags")),
                "payload": it,
            })
        self.index = SearchIndex(docs)

    def search(self, query: str, top_k: int = 4, project: str | None = None) -> list:
        if self.index is None:
            self.load()
        return self.index.search(query, top_k=top_k, project=project)

    def get_entry(self, entry_id: str):
        for e in self.entries:
            if e["id"] == entry_id:
                return e
        for it in self.faq["items"]:
            if it.get("id") == entry_id:
                return it
        return None

    def overdue(self) -> list:
        out = []
        for e in self.entries:
            if is_overdue(e.get("review_date", "")):
                out.append({"id": e["id"], "title": e["title"], "kind": "kb",
                            "owner": e.get("owner"), "review_date": e.get("review_date", "")})
        for it in self.faq["items"]:
            if it.get("status") == "published" and is_overdue(it.get("review_date", "")):
                out.append({"id": it.get("id"), "title": it.get("q"), "kind": "faq",
                            "owner": it.get("owner"), "review_date": it.get("review_date", "")})
        return out

    # ---- FAQ 写入 ----
    def next_faq_id(self) -> str:
        mx = 0
        for it in self.faq["items"]:
            m = re.match(r"faq-(\d+)$", str(it.get("id", "")))
            if m:
                mx = max(mx, int(m.group(1)))
        return f"faq-{mx + 1:03d}"

    def add_faq(self, item: dict) -> dict:
        item.setdefault("id", self.next_faq_id())
        item.setdefault("status", "published")
        item.setdefault("created_by", "mentor")
        self.faq["items"].append(item)
        save_json(FAQ_PATH, self.faq)
        self.load()
        return item


# ---------------------------------------------------------------- 存储

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, name TEXT, track TEXT, cohort TEXT, joined_at TEXT, role TEXT DEFAULT 'newcomer'
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user TEXT, kind TEXT, ref TEXT, score REAL, payload TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS progress (
  user TEXT, path_id TEXT, stage_id TEXT, status TEXT, best_score REAL,
  attempts INTEGER DEFAULT 0, updated_at TEXT,
  PRIMARY KEY (user, path_id, stage_id)
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user TEXT, question TEXT, anonymous INTEGER DEFAULT 0,
  top_score REAL, matched TEXT, escalated INTEGER DEFAULT 0, answered INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT, sample_question TEXT, count INTEGER DEFAULT 1,
  status TEXT DEFAULT 'open', draft_id TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS coach_sessions (
  id TEXT PRIMARY KEY, user TEXT, scenario TEXT, turns TEXT,
  status TEXT, result TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS escalations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user TEXT, question TEXT, reason TEXT, status TEXT DEFAULT 'open',
  answer TEXT DEFAULT '', answered_at TEXT, ref TEXT DEFAULT '', created_at TEXT
);
CREATE TABLE IF NOT EXISTS task_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER, title TEXT, itype TEXT DEFAULT 'general',
  content TEXT DEFAULT '', created_at TEXT
);
CREATE TABLE IF NOT EXISTS faq_hits (
  faq_id TEXT PRIMARY KEY, hits INTEGER DEFAULT 0, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user TEXT, role TEXT DEFAULT 'newcomer', created_at TEXT
);
CREATE TABLE IF NOT EXISTS weeklies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user TEXT, week_label TEXT, content TEXT,
  score REAL, mode TEXT, review TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT, description TEXT, creator TEXT,
  assignee TEXT DEFAULT '', created_at TEXT
);
CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                          -- 'task' | 'feedback'
  owner_id INTEGER,                   -- 任务 id / 反馈 id
  filename TEXT, stored_name TEXT, size INTEGER, mime TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS task_feedbacks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER, item_id INTEGER DEFAULT 0, user TEXT, content TEXT,
  status TEXT DEFAULT 'submitted', note TEXT DEFAULT '', created_at TEXT
);
"""


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            self._migrate()

    def _migrate(self) -> None:
        """旧库补列：role 分角色登录、escalations 导师回复、feedback 子项归属。幂等。"""
        for table, col, dft in (("users", "role", "'newcomer'"),
                                ("sessions", "role", "'newcomer'"),
                                ("escalations", "answer", "''"),
                                ("escalations", "answered_at", "''"),
                                ("escalations", "ref", "''"),
                                ("task_feedbacks", "item_id", "0")):
            cols = [r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")]
            if col not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT DEFAULT {dft}")
        self._conn.commit()

    def _exec(self, sql: str, args: tuple = ()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _query(self, sql: str, args: tuple = ()) -> list:
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    # ---- users ----
    def upsert_user(self, uid: str, name: str, track: str, cohort: str = "",
                    joined_at: str = "", role: str = "newcomer") -> None:
        self._exec(
            "INSERT INTO users(id,name,track,cohort,joined_at,role) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, track=excluded.track, role=excluded.role",
            (uid, name, track, cohort, joined_at or today_str(), role),
        )

    def users(self) -> list:
        return self._query("SELECT * FROM users ORDER BY joined_at, id")

    # ---- events ----
    def add_event(self, user: str, kind: str, ref: str = "", score=None, payload=None) -> None:
        self._exec(
            "INSERT INTO events(user,kind,ref,score,payload,created_at) VALUES(?,?,?,?,?,?)",
            (user, kind, ref, score, json.dumps(payload or {}, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )

    def events(self, user: str | None = None, kind: str | None = None, limit: int = 50) -> list:
        sql = "SELECT * FROM events WHERE 1=1"
        args = []
        if user:
            sql += " AND user=?"
            args.append(user)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = self._query(sql, tuple(args))
        for r in rows:
            try:
                r["payload"] = json.loads(r["payload"] or "{}")
            except ValueError:
                r["payload"] = {}
        return rows

    def dimension_scores(self, user: str, half_life: float = 0.6) -> dict:
        """把各类事件折算成能力维度分。新近记录权重更高。"""
        rows = self._query(
            "SELECT kind,payload,score,created_at FROM events WHERE user=? AND score IS NOT NULL "
            "ORDER BY id DESC LIMIT 60", (user,))
        acc = {}
        for i, r in enumerate(rows):          # rows 已按时间倒序
            try:
                p = json.loads(r["payload"] or "{}")
            except ValueError:
                p = {}
            w = half_life ** i
            dims = p.get("dimensions") or {}
            if isinstance(dims, dict) and dims:
                for d, v in dims.items():
                    if isinstance(v, (int, float)):
                        a = acc.setdefault(d, [0.0, 0.0])
                        a[0] += float(v) * w
                        a[1] += w
            else:
                d = p.get("dimension") or _KIND_DIMENSION.get(r["kind"])
                if d and r["score"] is not None:
                    a = acc.setdefault(d, [0.0, 0.0])
                    a[0] += float(r["score"]) * w
                    a[1] += w
        return {d: round(s / w, 1) for d, (s, w) in acc.items() if w > 0}

    # ---- progress ----
    def get_progress(self, user: str, path_id: str) -> dict:
        rows = self._query(
            "SELECT * FROM progress WHERE user=? AND path_id=?", (user, path_id))
        return {r["stage_id"]: r for r in rows}

    def set_progress(self, user: str, path_id: str, stage_id: str,
                     status: str, score=None) -> None:
        cur = self._query(
            "SELECT * FROM progress WHERE user=? AND path_id=? AND stage_id=?",
            (user, path_id, stage_id))
        if cur:
            best = max(float(cur[0].get("best_score") or 0), float(score or 0))
            self._exec(
                "UPDATE progress SET status=?, best_score=?, attempts=attempts+1, updated_at=? "
                "WHERE user=? AND path_id=? AND stage_id=?",
                (status, best, datetime.now().isoformat(timespec="seconds"),
                 user, path_id, stage_id))
        else:
            self._exec(
                "INSERT INTO progress(user,path_id,stage_id,status,best_score,attempts,updated_at) "
                "VALUES(?,?,?,?,?,1,?)",
                (user, path_id, stage_id, status, float(score or 0),
                 datetime.now().isoformat(timespec="seconds")))

    def all_progress(self) -> list:
        return self._query("SELECT * FROM progress")

    # ---- questions / staleness ----
    def add_question(self, user: str, question: str, anonymous: bool,
                     top_score: float, matched: list, escalated: bool, answered: bool) -> int:
        cur = self._exec(
            "INSERT INTO questions(user,question,anonymous,top_score,matched,escalated,answered,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (user, question, 1 if anonymous else 0, top_score,
             json.dumps(matched, ensure_ascii=False), 1 if escalated else 0,
             1 if answered else 0, datetime.now().isoformat(timespec="seconds")))
        return int(cur.lastrowid)

    def questions(self, limit: int = 200) -> list:
        return self._query("SELECT * FROM questions ORDER BY id DESC LIMIT ?", (limit,))

    def escalations(self, status: str = "open") -> list:
        return self._query(
            "SELECT * FROM escalations WHERE status=? ORDER BY id DESC", (status,))

    def add_escalation(self, user: str, question: str, reason: str, ref: str = "") -> None:
        self._exec(
            "INSERT INTO escalations(user,question,reason,status,ref,created_at) VALUES(?,?,?,?,?,?)",
            (user, question, reason, "open", ref,
             datetime.now().isoformat(timespec="seconds")))

    def resolve_escalation(self, eid: int) -> None:
        self._exec("UPDATE escalations SET status='done' WHERE id=?", (eid,))

    def answer_escalation(self, eid: int, answer: str) -> int:
        cur = self._exec(
            "UPDATE escalations SET status='done', answer=?, answered_at=? WHERE id=?",
            (answer, datetime.now().isoformat(timespec="seconds"), eid))
        # 任务提问回流：ref 形如 feedback:12 时，答案同步写回子项反馈的导师点评，
        # 新人在任务卡片上直接看到答案，不用跳去问答页。
        if cur.rowcount:
            row = self._query("SELECT ref FROM escalations WHERE id=?", (eid,))
            ref = (row[0]["ref"] if row and row[0]["ref"] else "")
            if ref.startswith("feedback:"):
                try:
                    fid = int(ref.split(":", 1)[1])
                    self._exec("UPDATE task_feedbacks SET note=? WHERE id=?", (answer, fid))
                except (ValueError, IndexError):
                    pass
        return cur.rowcount

    def my_escalations(self, user: str) -> list:
        return self._query(
            "SELECT * FROM escalations WHERE user=? ORDER BY id DESC", (user,))

    # ---- FAQ 命中计数（存 SQLite，不写回 faq.json，保持内容文件纯净可 review）----
    def bump_faq_hit(self, faq_id: str) -> None:
        self._exec(
            "INSERT INTO faq_hits(faq_id,hits,updated_at) VALUES(?,1,?) "
            "ON CONFLICT(faq_id) DO UPDATE SET hits=hits+1, updated_at=excluded.updated_at",
            (faq_id, datetime.now().isoformat(timespec="seconds")))

    def faq_hit(self, faq_id: str) -> int:
        rows = self._query("SELECT hits FROM faq_hits WHERE faq_id=?", (faq_id,))
        return int(rows[0]["hits"]) if rows else 0

    def import_faq_hits(self, items: list) -> None:
        """一次性迁移旧版 faq.json 里的 hits 计数。已迁移过的条目以 SQLite 为准。"""
        for it in items:
            n = int(it.get("hits") or 0)
            if n > 0 and it.get("id"):
                self._exec(
                    "INSERT OR IGNORE INTO faq_hits(faq_id,hits,updated_at) VALUES(?,?,?)",
                    (it["id"], n, datetime.now().isoformat(timespec="seconds")))

    # ---- 登录会话（可选身份保护，见 load_users_file / auth_enabled）----
    def create_session(self, user: str, role: str = "newcomer") -> str:
        token = secrets.token_hex(16)
        self._exec("INSERT INTO sessions(token,user,role,created_at) VALUES(?,?,?,?)",
                   (token, user, role, datetime.now().isoformat(timespec="seconds")))
        return token

    def session_user(self, token: str):
        """返回会话行 {token, user, role, ...}；无效 token 返回 None。"""
        if not token:
            return None
        rows = self._query("SELECT token, user, role FROM sessions WHERE token=?", (token,))
        if not rows:
            return None
        r = dict(rows[0])
        r.setdefault("role", "newcomer")
        return r

    def drop_session(self, token: str) -> None:
        self._exec("DELETE FROM sessions WHERE token=?", (token,))

    # ---- 周报 ----
    def add_weekly(self, user: str, week_label: str, content: str,
                   score, mode: str, review: dict) -> int:
        cur = self._exec(
            "INSERT INTO weeklies(user,week_label,content,score,mode,review,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user, week_label, content, score, mode,
             json.dumps(review or {}, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")))
        return int(cur.lastrowid)

    def weeklies(self, user: str | None = None, limit: int = 200) -> list:
        sql = "SELECT * FROM weeklies"
        args: list = []
        if user:
            sql += " WHERE user=?"
            args.append(user)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = self._query(sql, tuple(args))
        for r in rows:
            try:
                r["review"] = json.loads(r["review"] or "{}")
            except ValueError:
                r["review"] = {}
        return rows

    def latest_weekly_by_user(self) -> dict:
        """每个用户最近一次周报（导师看板用）。"""
        rows = self._query(
            "SELECT w.* FROM weeklies w "
            "JOIN (SELECT user, MAX(id) AS mid FROM weeklies GROUP BY user) t "
            "ON w.user=t.user AND w.id=t.mid")
        return {r["user"]: r for r in rows}

    # ---- 任务中心 ----
    def add_task(self, title: str, description: str, creator: str, assignee: str) -> int:
        cur = self._exec(
            "INSERT INTO tasks(title,description,creator,assignee,created_at) VALUES(?,?,?,?,?)",
            (title, description, creator, assignee,
             datetime.now().isoformat(timespec="seconds")))
        return int(cur.lastrowid)

    def add_task_item(self, task_id: int, title: str, itype: str, content: str) -> int:
        """添加任务子项：一个任务可拆多个子项（问题回答 / 需求 / 其他）。"""
        cur = self._exec(
            "INSERT INTO task_items(task_id,title,itype,content,created_at) VALUES(?,?,?,?,?)",
            (task_id, title, itype, content, datetime.now().isoformat(timespec="seconds")))
        return int(cur.lastrowid)

    def task_items(self, task_id: int) -> list:
        return self._query(
            "SELECT * FROM task_items WHERE task_id=? ORDER BY id", (task_id,))

    def get_task_item(self, item_id: int):
        rows = self._query("SELECT * FROM task_items WHERE id=?", (item_id,))
        return rows[0] if rows else None

    def delete_task_item(self, item_id: int) -> None:
        """删除子项并级联清理其附件与反馈。"""
        for f in self._query(
                "SELECT stored_name FROM uploads WHERE kind='item' AND owner_id=?", (item_id,)):
            try:
                (FILES_DIR / f["stored_name"]).unlink()
            except OSError:
                pass
        self._exec("DELETE FROM uploads WHERE kind='item' AND owner_id=?", (item_id,))
        fb_ids = [r["id"] for r in self._query(
            "SELECT id FROM task_feedbacks WHERE item_id=?", (item_id,))]
        for fid in fb_ids:
            for f in self._query(
                    "SELECT stored_name FROM uploads WHERE kind='feedback' AND owner_id=?", (fid,)):
                try:
                    (FILES_DIR / f["stored_name"]).unlink()
                except OSError:
                    pass
            self._exec("DELETE FROM uploads WHERE kind='feedback' AND owner_id=?", (fid,))
        self._exec("DELETE FROM task_feedbacks WHERE item_id=?", (item_id,))
        self._exec("DELETE FROM task_items WHERE id=?", (item_id,))

    def tasks_all(self, limit: int = 100) -> list:
        return self._query(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))

    def get_task(self, task_id: int):
        rows = self._query("SELECT * FROM tasks WHERE id=?", (task_id,))
        return rows[0] if rows else None

    def delete_task(self, task_id: int) -> None:
        for f in self._query(
                "SELECT stored_name FROM uploads WHERE kind='task' AND owner_id=?", (task_id,)):
            try:
                (FILES_DIR / f["stored_name"]).unlink()
            except OSError:
                pass
        self._exec("DELETE FROM uploads WHERE kind='task' AND owner_id=?", (task_id,))
        fb_ids = [r["id"] for r in self._query(
            "SELECT id FROM task_feedbacks WHERE task_id=?", (task_id,))]
        for fid in fb_ids:
            for f in self._query(
                    "SELECT stored_name FROM uploads WHERE kind='feedback' AND owner_id=?", (fid,)):
                try:
                    (FILES_DIR / f["stored_name"]).unlink()
                except OSError:
                    pass
            self._exec("DELETE FROM uploads WHERE kind='feedback' AND owner_id=?", (fid,))
        self._exec("DELETE FROM task_feedbacks WHERE task_id=?", (task_id,))
        for it in self._query("SELECT id FROM task_items WHERE task_id=?", (task_id,)):
            self.delete_task_item(it["id"])
        self._exec("DELETE FROM task_items WHERE task_id=?", (task_id,))
        self._exec("DELETE FROM tasks WHERE id=?", (task_id,))

    def add_upload(self, kind: str, owner_id: int, filename: str,
                   stored_name: str, size: int, mime: str) -> int:
        cur = self._exec(
            "INSERT INTO uploads(kind,owner_id,filename,stored_name,size,mime,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (kind, owner_id, filename, stored_name, size, mime,
             datetime.now().isoformat(timespec="seconds")))
        return int(cur.lastrowid)

    def uploads_for(self, kind: str, owner_id: int) -> list:
        return self._query(
            "SELECT * FROM uploads WHERE kind=? AND owner_id=? ORDER BY id", (kind, owner_id))

    def get_upload(self, upload_id: int):
        rows = self._query("SELECT * FROM uploads WHERE id=?", (upload_id,))
        return rows[0] if rows else None

    def add_feedback(self, task_id: int, item_id: int, user: str, content: str) -> int:
        cur = self._exec(
            "INSERT INTO task_feedbacks(task_id,item_id,user,content,status,created_at) "
            "VALUES(?,?,?,?, 'submitted',?)",
            (task_id, item_id, user, content, datetime.now().isoformat(timespec="seconds")))
        return int(cur.lastrowid)

    def feedbacks_for_item(self, item_id: int) -> list:
        return self._query(
            "SELECT * FROM task_feedbacks WHERE item_id=? ORDER BY id", (item_id,))

    def confirm_feedback(self, feedback_id: int, note: str) -> int:
        cur = self._exec(
            "UPDATE task_feedbacks SET status='confirmed', note=? WHERE id=?",
            (note, feedback_id))
        return cur.rowcount

    # ---- FAQ 沉淀候选池 ----
    def candidates(self, status: str = "open") -> list:
        return self._query(
            "SELECT * FROM candidates WHERE status=? ORDER BY count DESC, id DESC", (status,))

    def all_candidates(self) -> list:
        return self._query("SELECT * FROM candidates ORDER BY id DESC")

    def add_candidate(self, question: str, fingerprint: str) -> dict:
        self._exec(
            "INSERT INTO candidates(fingerprint,sample_question,count,status,created_at,updated_at) "
            "VALUES(?,?,1,'open',?,?)",
            (fingerprint, question, datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds")))
        return self._query("SELECT * FROM candidates ORDER BY id DESC LIMIT 1")[0]

    def bump_candidate(self, cid: int) -> None:
        self._exec(
            "UPDATE candidates SET count=count+1, updated_at=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), cid))

    def link_candidate_draft(self, cid: int, draft_id: str) -> None:
        self._exec("UPDATE candidates SET status='drafted', draft_id=? WHERE id=?", (draft_id, cid))

    # ---- 陪练会话 ----
    def save_session(self, sid: str, user: str, scenario: str, turns: list,
                     status: str, result=None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self._exec(
            "INSERT INTO coach_sessions(id,user,scenario,turns,status,result,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET turns=excluded.turns, "
            "status=excluded.status, result=excluded.result, updated_at=excluded.updated_at",
            (sid, user, scenario, json.dumps(turns, ensure_ascii=False), status,
             json.dumps(result or {}, ensure_ascii=False), now, now))

    def get_session(self, sid: str):
        rows = self._query("SELECT * FROM coach_sessions WHERE id=?", (sid,))
        if not rows:
            return None
        r = rows[0]
        r["turns"] = json.loads(r["turns"] or "[]")
        r["result"] = json.loads(r["result"] or "{}")
        return r

    def sessions(self, user: str | None = None, limit: int = 30) -> list:
        sql = "SELECT * FROM coach_sessions"
        args = []
        if user:
            sql += " WHERE user=?"
            args.append(user)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        return self._query(sql, tuple(args))


_KIND_DIMENSION = {
    "exam": "professional",
    "coach": "communication",
    "review": "discipline",
    "envcheck": "discipline",
}
# 注意：提问（ask）不参与能力打分——"问了什么"不等于"掌握了什么"，
# 用提问频次打分会诱导新人少问问题，与工具目标相反。


# ---------------------------------------------------------------- 大模型（可选）

class LLM:
    """OpenAI 兼容接口。未配置时 enabled=False，全部功能退化为本地模式。"""

    def __init__(self, settings: dict):
        self.reload(settings)

    def reload(self, settings: dict) -> None:
        cfg = dict(settings.get("llm") or {})
        # 环境变量优先级最高，方便 CI / 服务器注入
        cfg["base_url"] = os.environ.get("MENTOR_LLM_BASE_URL") or cfg.get("base_url") or ""
        cfg["api_key"] = os.environ.get("MENTOR_LLM_API_KEY") or cfg.get("api_key") or ""
        cfg["model"] = os.environ.get("MENTOR_LLM_MODEL") or cfg.get("model") or ""
        if os.environ.get("MENTOR_LLM_BASE_URL"):
            cfg["enabled"] = True
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled") and cfg.get("base_url") and cfg.get("model"))
        self.last_error = ""

    @property
    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "base_url": self.cfg.get("base_url", ""),
            "model": self.cfg.get("model", ""),
            "has_key": bool(self.cfg.get("api_key")),
            "last_error": getattr(self, "last_error", "") or "",
        }

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 1200):
        """返回文本；未启用或失败时返回 None，由调用方降级处理。"""
        if not self.enabled:
            return None
        url = self.cfg["base_url"].rstrip("/") + "/chat/completions"
        payload = {
            "model": self.cfg["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.get('api_key', '')}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=float(self.cfg.get("timeout") or 60)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip() or None
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, OSError) as exc:
            self.last_error = str(exc)
            return None


# ---------------------------------------------------------------- 全局单例

ensure_dirs()
_settings = load_json(SETTINGS_PATH, {}) or {}


def _merged_settings() -> dict:
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    for section, values in (_settings or {}).items():
        if isinstance(values, dict) and section in out:
            out[section].update(values)
        else:
            out[section] = values
    return out


SETTINGS = _merged_settings()
KB = KnowledgeBase()
KB.load()
STORE = Store()
LLM_CLIENT = LLM(SETTINGS)


def reload_all() -> None:
    global LLM_CLIENT
    LOAD_WARNINGS.clear()
    # 原地更新 SETTINGS，而不是重新绑定：server.py / agents.py 都是
    # "from core import SETTINGS" 按引用导入的，重新绑定会让它们手里
    # 的旧字典永远停留在启动时的值——页面改了配置要重启才生效。
    new_settings = _merged_settings()
    SETTINGS.clear()
    SETTINGS.update(new_settings)
    KB.load()
    LLM_CLIENT.reload(SETTINGS)


def update_settings(new: dict) -> dict:
    global _settings
    for section, values in (new or {}).items():
        if isinstance(values, dict):
            _settings.setdefault(section, {}).update(values)
        else:
            _settings[section] = values
    save_json(SETTINGS_PATH, _settings)
    reload_all()
    return SETTINGS


# ---------------------------------------------------------------- 内容文件校验

VALID_DIMENSIONS = {"business", "professional", "communication", "discipline"}
VALID_CHECK_TYPES = {"command", "path", "env", "tcp"}
VALID_STATUS = {"published", "draft", "escalated"}


def validate_data() -> list:
    """校验全部内容文件。返回 [{level, where, message}]。

    为什么需要这个：内容文件是给人手改的。一个打错的逗号会让整个 faq.json 被忽略，
    工具不会崩，只会"静默变傻"——那比崩溃更危险。所以必须主动检查并大声报出来。
    """
    issues = [{"level": "error", "where": "内容文件", "message": w} for w in LOAD_WARNINGS]

    def err(where, msg):
        issues.append({"level": "error", "where": where, "message": msg})

    def bad(where, msg):
        issues.append({"level": "warn", "where": where, "message": msg})

    # ---- 知识条目 ----
    for e in KB.entries:
        if not e.get("review_date"):
            bad(f"knowledge/{e['file']}", "缺少 review_date，会被当成过期知识一直标红")
        if not e.get("owner") or e.get("owner") == "未指定":
            bad(f"knowledge/{e['file']}", "缺少 owner，知识过期时没人负责复核")
    ids = [e["id"] for e in KB.entries]
    dup = {i for i in ids if ids.count(i) > 1}
    for d in dup:
        err("knowledge/", f"知识条目 id 重复：{d}")

    # ---- FAQ ----
    faq_ids = []
    for it in KB.faq.get("items") or []:
        fid = it.get("id", "(无 id)")
        faq_ids.append(fid)
        if not it.get("q") or not it.get("a"):
            err(f"faq.json/{fid}", "缺少 q 或 a 字段")
        if it.get("status") not in VALID_STATUS:
            err(f"faq.json/{fid}", f"status 必须是 {'/'.join(sorted(VALID_STATUS))}，当前为 {it.get('status')!r}")
        if it.get("status") == "published" and not it.get("review_date"):
            bad(f"faq.json/{fid}", "已发布但缺少 review_date")
        if it.get("status") == "published" and str(it.get("a") or "").strip().startswith("待补充"):
            bad(f"faq.json/{fid}", "已发布但答案仍是『待补充』占位内容，会把占位文案当正式答案自动回复")
    fdup = {i for i in faq_ids if faq_ids.count(i) > 1}
    for d in fdup:
        err("faq.json", f"FAQ id 重复：{d}")

    # ---- 关卡地图 ----
    seen_path_tracks = []
    for p in load_dir_json("paths"):
        pid = p.get("id", "?")
        seen_path_tracks.append(p.get("track"))
        stage_ids = [s.get("id") for s in (p.get("stages") or [])]
        if len(stage_ids) != len(set(stage_ids)):
            err(f"paths/{p.get('_file')}", "存在重复的关卡 id")
        for s in p.get("stages") or []:
            sid = s.get("id", "?")
            for r in s.get("requires") or []:
                if r not in stage_ids:
                    err(f"paths/{pid}/{sid}", f"requires 指向不存在的关卡：{r}")
            for q in ((s.get("exam") or {}).get("questions") or []):
                opts = q.get("options") or []
                ans = q.get("answer")
                if not isinstance(ans, int) or not (0 <= ans < len(opts)):
                    err(f"paths/{pid}/{sid}/{q.get('id')}",
                        f"answer={ans!r} 不是合法选项下标（共 {len(opts)} 个选项）")
                if q.get("dimension") not in VALID_DIMENSIONS:
                    err(f"paths/{pid}/{sid}/{q.get('id')}",
                        f"dimension={q.get('dimension')!r} 不在 {sorted(VALID_DIMENSIONS)} 中")
                if not q.get("explain"):
                    bad(f"paths/{pid}/{sid}/{q.get('id')}", "没有 explain，新人答错后看不到解析")
    for t in load_json(DATA_DIR / "users.json", {}).get("users") or []:
        # 导师账号不参与成长地图，track 允许为空
        if str(t.get("role") or "newcomer") != "mentor" and t.get("track") not in seen_path_tracks:
            err("users.json", f"用户 {t.get('id')} 的 track={t.get('track')!r} 没有对应的关卡地图")
        pin = str(t.get("pin") or "").strip()
        if pin and not re.fullmatch(r"\d{4}", pin):
            err("users.json", f"用户 {t.get('id')} 的 pin 必须是 4 位数字（当前 {t.get('pin')!r}）")
        role = str(t.get("role") or "newcomer")
        if role not in ("mentor", "newcomer"):
            err("users.json", f"用户 {t.get('id')} 的 role 必须是 mentor 或 newcomer（当前 {role!r}）")

    # ---- 评分标准 ----
    rubric_ids = []
    for r in load_dir_json("rubrics"):
        rid = r.get("id", "?")
        rubric_ids.append(rid)
        items = r.get("items") or []
        if not items:
            err(f"rubrics/{r.get('_file')}", "没有任何评分项")
        total_w = sum(i.get("weight", 0) for i in items)
        if items and abs(total_w - 100) > 0.01:
            bad(f"rubrics/{rid}", f"评分项权重合计为 {total_w}，建议调整为 100")
        for i in items:
            if not i.get("signals"):
                bad(f"rubrics/{rid}/{i.get('id')}",
                    "缺少 signals，未接入大模型时该项无法被本地启发式评分")
            if not i.get("levels"):
                bad(f"rubrics/{rid}/{i.get('id')}", "缺少 levels，新人看不到满分标准")

    # ---- 陪练场景 ----
    kb_ids = set(ids) | set(faq_ids)
    for s in load_dir_json("scenarios"):
        sid = s.get("id", "?")
        if s.get("rubric") not in rubric_ids:
            err(f"scenarios/{sid}", f"rubric={s.get('rubric')!r} 不存在")
        if not s.get("opening"):
            bad(f"scenarios/{sid}", "没有 opening 开场白")
        if not s.get("model_answer"):
            bad(f"scenarios/{sid}", "没有 model_answer，评分后无法给新人示范答法")

    # ---- 关卡里的 learn 引用 ----
    for p in load_dir_json("paths"):
        for s in p.get("stages") or []:
            for l in s.get("learn") or []:
                ref = l.get("ref")
                if l.get("type") == "kb" and ref and ref not in kb_ids:
                    err(f"paths/{p.get('id')}/{s.get('id')}",
                        f"learn 引用了不存在的知识条目：{ref}")

    # ---- 环境自检 ----
    for c in load_dir_json("env_checks"):
        cid = c.get("id", "?")
        if not c.get("checks"):
            err(f"env_checks/{cid}", "没有任何检查项")
        for ck in c.get("checks") or []:
            if ck.get("type") not in VALID_CHECK_TYPES:
                err(f"env_checks/{cid}/{ck.get('id')}",
                    f"type={ck.get('type')!r} 不在 {sorted(VALID_CHECK_TYPES)} 中")
            if not ck.get("fix"):
                bad(f"env_checks/{cid}/{ck.get('id')}", "没有 fix，检查失败后新人不知道怎么办")

    # ---- 能力维度 ----
    dims = load_json(DATA_DIR / "dimensions.json", {}).get("dimensions") or []
    if not dims:
        err("dimensions.json", "没有配置任何能力维度")
    for d in dims:
        if d.get("id") not in VALID_DIMENSIONS:
            err("dimensions.json", f"维度 id={d.get('id')!r} 不是引擎认识的四个维度之一")

    return issues
