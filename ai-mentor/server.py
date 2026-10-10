# -*- coding: utf-8 -*-
"""引路人 · AI 导师  —— 本地 Web 服务

用法：
    python server.py                 # http://127.0.0.1:8848
    python server.py --port 9000
    python server.py --host 0.0.0.0  # 让同一局域网内的同事访问

零依赖：只用 Python 标准库。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import posixpath
import re
import sys
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agents  # noqa: E402
import core  # noqa: E402
from core import SETTINGS, update_settings, WEB_DIR  # noqa: E402

# 输出被重定向（管道 / CI / 日志文件）时 Python 会退回系统本地编码（中文 Windows 是 GBK），
# 统一强制 UTF-8，避免控制台与日志里中文乱码。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        pass

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")


# ---------------------------------------------------------------- 路由表

def route_get(path: str, q: dict):
    if path == "/api/meta":
        return agents.meta()
    if path == "/api/kb":
        return agents.kb_search(q.get("q", [""])[0])
    if path.startswith("/api/kb/"):
        item = agents.kb_get(urllib.parse.unquote(path[len("/api/kb/"):]))
        return item or {"error": "not found"}
    if path == "/api/path":
        return agents.path_with_progress(track=_one(q, "track"), user=_one(q, "user"),
                                         path_id=_one(q, "id"))
    if path == "/api/exam":
        return {"questions": agents.exam_questions(_one(q, "path") or "",
                                                   _one(q, "stage") or "")}
    if path == "/api/profile":
        return agents.profile(_one(q, "user") or "demo")
    if path == "/api/dashboard":
        return agents.dashboard()
    if path == "/api/scenarios":
        return {"scenarios": agents.scenarios()}
    if path == "/api/rubrics":
        return {"rubrics": agents.rubrics()}
    if path == "/api/envcheck":
        cid = _one(q, "id")
        if cid:
            return agents.env_check(cid, _one(q, "user") or "")
        return {"sets": agents.env_check_sets()}
    if path == "/api/faq/drafts":
        return agents.faq_drafts()
    if path == "/api/weekly/all":
        return agents.weekly_all()
    if path == "/api/weekly":
        return agents.weekly_list(_one(q, "user") or "demo")
    if path == "/api/tasks":
        return agents.tasks_list(_one(q, "user") or "")
    if path == "/api/projects":
        return agents.projects_list()
    if path == "/api/escalations/mine":
        return agents.my_escalations(_one(q, "user") or "demo")
    if path == "/api/settings":
        s = json.loads(json.dumps(SETTINGS))
        s["llm"] = {**s.get("llm", {}),
                    "api_key": "***" if s.get("llm", {}).get("api_key") else ""}
        return {"settings": s, "llm_status": core.LLM_CLIENT.status}
    if path == "/api/health":
        return {"ok": True}
    return None


def route_post(path: str, body: dict):
    if path == "/api/login":
        return agents.login(body.get("user") or "", body.get("pin") or "")
    if path == "/api/weekly":
        return agents.weekly_submit(body.get("user") or "demo",
                                    body.get("content", ""),
                                    body.get("week_label", ""))
    if path == "/api/ask/escalate":
        return agents.ask_escalate(body.get("user") or "demo",
                                   body.get("question", ""), body.get("note", ""))
    if path == "/api/escalation/answer":
        return agents.escalation_answer(int(body.get("id") or 0), body.get("answer", ""))
    if path == "/api/tasks/ai-draft":
        return agents.tasks_ai_draft(body.get("description", ""), body.get("project", ""))
    if path == "/api/projects/delete":
        return agents.project_delete(body.get("id", ""))
    if path == "/api/tasks/item/delete":
        return agents.task_item_delete(int(body.get("id") or 0))
    if path == "/api/feedback/confirm":
        return agents.feedback_confirm(int(body.get("id") or 0),
                                       body.get("note", ""))
    if path == "/api/tasks/delete":
        return agents.task_delete(int(body.get("id") or 0))
    if path == "/api/ask":
        return agents.ask(body.get("user") or "demo", body.get("question", ""),
                          bool(body.get("anonymous")), body.get("track"),
                          body.get("project") or None)
    if path == "/api/exam":
        return agents.submit_exam(body.get("user") or "demo", body.get("path", ""),
                                  body.get("stage", ""), body.get("answers") or {})
    if path == "/api/coach/start":
        return agents.coach_start(body.get("user") or "demo", body.get("scenario", ""))
    if path == "/api/coach/reply":
        return agents.coach_reply(body.get("session", ""), body.get("message", ""))
    if path == "/api/coach/finish":
        return agents.coach_finish(body.get("session", ""))
    if path == "/api/review":
        return agents.review(body.get("rubric", ""), body.get("text", ""),
                             body.get("user") or "", body.get("title", ""))
    if path == "/api/faq/draft":
        return agents.faq_make_draft(int(body.get("candidate_id") or 0))
    if path == "/api/faq/publish":
        return agents.faq_publish(body)
    if path == "/api/faq/delete":
        return agents.faq_delete(body.get("id", ""))
    if path == "/api/settings":
        payload = body.get("settings") or body
        if isinstance(payload.get("llm"), dict) and payload["llm"].get("api_key") == "***":
            payload["llm"].pop("api_key", None)
        update_settings(payload)
        return {"ok": True, "llm_status": core.LLM_CLIENT.status}
    if path == "/api/escalation/resolve":
        core.STORE.resolve_escalation(int(body.get("id") or 0))
        return {"ok": True}
    return None


def _one(q: dict, key: str):
    v = q.get(key)
    return v[0] if isinstance(v, list) and v else None


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "YinLuRen/1.0"

    def log_message(self, fmt, *args):        # 安静一点，只留错误
        if str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            sys.stderr.write("[引路人] %s\n" % (fmt % args))

    # ---- helpers ----
    OPEN_PATHS = ("/api/health", "/api/meta", "/api/login")

    OPEN_PATHS = ("/api/health", "/api/meta", "/api/login", "/api/logout")
    # 导师专属接口：新人账号调用一律 403（分角色登录的权限边界）
    MENTOR_ONLY = ("/api/dashboard", "/api/settings", "/api/faq/draft", "/api/faq/publish",
                   "/api/faq/delete", "/api/escalation/resolve", "/api/tasks/delete",
                   "/api/feedback/confirm", "/api/escalation/answer", "/api/tasks/item/delete",
                   "/api/projects/import", "/api/projects/delete", "/api/tasks/ai-draft",
                   "/api/weekly/export")

    def _gate(self, path: str, q: dict, body: dict | None):
        """可选身份保护（users.json 里有人配了 pin 即启用）。
        返回 None 表示放行；否则返回 (status, payload)。
        校验三层：token 有效 → 角色匹配（导师专属接口）→ 登录身份 = 请求里的 user，
        否则 PIN 形同虚设——登录 A 就能替 B 交考试。"""
        if not core.auth_enabled():
            return None
        if path in self.OPEN_PATHS:
            return None
        # token 优先走请求头；/api/files 会被 <img>/<video> 直接引用，
        # 那些标签发不了自定义头，所以同时接受 ?token= 查询参数。
        sess = core.STORE.session_user(
            self.headers.get("X-Token") or _one(q, "token") or "")
        if not sess:
            return 401, {"error": "本站已开启身份保护，请先登录", "auth_required": True}
        role = sess.get("role") or "newcomer"
        mentor_needed = path in self.MENTOR_ONLY or (path == "/api/tasks" and self.command == "POST")
        if mentor_needed and role != "mentor":
            return 403, {"error": "该操作仅导师账号可用", "forbidden": True}
        user = sess.get("user")
        requested = (body or {}).get("user") or _one(q, "user")
        if user and requested and requested != user:
            return 403, {"error": f"当前登录身份是 {user}，与请求的用户 {requested} 不一致，请重新登录",
                         "auth_required": True}
        return None

    def _json(self, obj, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _read_multipart(self):
        """手工解析 multipart/form-data（Python 3.13 起标准库移除了 cgi 模块）。
        返回 (fields: dict, files: [{filename, content, ctype}])。
        二进制安全：每个分段去掉定界符后恰好一个尾部 CRLF，不能用 strip()。"""
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r'boundary=([^;]+)', ctype)
        if not m:
            return {}, []
        boundary = m.group(1).strip('"').encode("latin-1")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}, []
        if n <= 0 or n > agents.MAX_UPLOAD_BYTES:
            return {}, []
        body = self.rfile.read(n)
        fields: dict = {}
        files: list = []
        CRLF = bytes([13, 10])
        for raw in body.split(b"--" + boundary)[1:]:
            if raw.startswith(b"--"):          # 结束定界
                break
            part = raw[2:] if raw.startswith(CRLF) else raw
            if part.endswith(CRLF):
                part = part[:-2]
            head, sep, payload = part.partition(CRLF + CRLF)
            if not sep:
                continue
            headers = {}
            for line in head.decode("utf-8", "replace").split(CRLF.decode("latin-1")):
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            disp = headers.get("content-disposition", "")
            name_m = re.search(r'name="([^"]*)"', disp)
            file_m = re.search(r'filename="([^"]*)"', disp)
            if file_m:
                files.append({"filename": file_m.group(1),
                              "content": payload,
                              "ctype": headers.get("content-type", "")})
            elif name_m:
                fields[name_m.group(1)] = payload.decode("utf-8", "replace")
        return fields, files

    def _send_upload(self, row: dict) -> None:
        """按原文件名内联返回附件，视频/音频支持 Range 拖动播放。"""
        path = core.FILES_DIR / row["stored_name"]
        if not path.is_file():
            self._json({"error": "文件已丢失"}, 404)
            return
        data = path.read_bytes()
        mime = row["mime"] or mimetypes.guess_type(row["filename"])[0] \
            or "application/octet-stream"
        fname = urllib.parse.quote(row["filename"])
        base_headers = [
            ("Content-Type", mime),
            ("Content-Disposition", f"inline; filename*=UTF-8''{fname}"),
            ("Accept-Ranges", "bytes"),
            ("Cache-Control", "private, max-age=3600"),
        ]
        rng = self.headers.get("Range")
        if rng and mime.startswith(("video/", "audio/")):
            m = re.match(r"bytes=([0-9]*)-([0-9]*)$", rng.strip())
            if m and (m.group(1) or m.group(2)):
                total = len(data)
                start = int(m.group(1)) if m.group(1) else max(0, total - int(m.group(2)))
                end = int(m.group(2)) if m.group(2) else total - 1
                end = min(end, total - 1)
                if start > end or start >= total:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.end_headers()
                    return
                chunk = data[start:end + 1]
                self.send_response(206)
                for k, v in base_headers:
                    self.send_header(k, v)
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
        self.send_response(200)
        for k, v in base_headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _parse(self):
        u = urllib.parse.urlparse(self.path)
        return u.path, urllib.parse.parse_qs(u.query)

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        rel = posixpath.normpath(rel)
        if rel.startswith(".."):
            self.send_error(403)
            return
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            self.send_error(403)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self.send_error(404, "Not Found")
            return
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- methods ----
    def do_GET(self):
        path, q = self._parse()
        if path.startswith("/api/"):
            gate = self._gate(path, q, None)
            if gate:
                self._json(gate[1], gate[0])
                return
            if path == "/api/weekly/export":
                fname, content = agents.weekly_export_md()
                data = content.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition",
                                 "attachment; filename*=UTF-8''" + urllib.parse.quote(fname))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path.startswith("/api/files/"):
                try:
                    row = core.STORE.get_upload(int(path[len("/api/files/"):] or 0))
                except ValueError:
                    row = None
                if not row:
                    self._json({"error": "文件不存在"}, 404)
                else:
                    self._send_upload(row)
                return
            try:
                out = route_get(path, q)
            except Exception:                          # noqa: BLE001
                traceback.print_exc()
                self._json({"error": "服务端异常，请看控制台日志"}, 500)
                return
            if out is None:
                self._json({"error": f"未知接口 {path}"}, 404)
            else:
                self._json(out)
            return
        self._static(path)

    def do_POST(self):
        path, _ = self._parse()
        if not path.startswith("/api/"):
            self.send_error(404)
            return
        # 任务发布 / 任务反馈走 multipart（带附件），其余走 JSON
        if path in ("/api/tasks", "/api/tasks/feedback", "/api/projects/import"):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n > agents.MAX_UPLOAD_BYTES:
                self._json({"error": "附件总量超过 200MB 上限"}, 413)
                return
            fields, files = self._read_multipart()
            gate = self._gate(path, {}, fields)
            if gate:
                self._json(gate[1], gate[0])
                return
            try:
                if path == "/api/projects/import":
                    out = agents.project_import(fields.get("name", ""),
                                                fields.get("description", ""), files)
                elif path == "/api/tasks":
                    items = []
                    for k, v in fields.items():
                        m = re.match(r"item_title_([0-9]+)$", k)
                        if m:
                            items.append({"seq": int(m.group(1)), "title": v,
                                          "itype": fields.get("item_itype_" + m.group(1), "general"),
                                          "content": fields.get("item_content_" + m.group(1), "")})
                    items.sort(key=lambda x: x["seq"])
                    out = agents.task_create(fields.get("title", ""),
                                             fields.get("description", ""),
                                             fields.get("user", ""),
                                             fields.get("assignee", ""), items, files)
                else:
                    out = agents.task_feedback(int(fields.get("task_id") or 0),
                                               int(fields.get("item_id") or 0),
                                               fields.get("user", ""),
                                               fields.get("content", ""), files)
            except Exception:                      # noqa: BLE001
                traceback.print_exc()
                self._json({"error": "服务端异常，请看控制台日志"}, 500)
                return
            self._json(out)
            return
        body = self._read_body()
        gate = self._gate(path, {}, body)
        if gate:
            self._json(gate[1], gate[0])
            return
        if path == "/api/logout":
            core.STORE.drop_session(self.headers.get("X-Token") or "")
            self._json({"ok": True})
            return
        try:
            out = route_post(path, body)
        except Exception:                              # noqa: BLE001
            traceback.print_exc()
            self._json({"error": "服务端异常，请看控制台日志"}, 500)
            return
        if out is None:
            self._json({"error": f"未知接口 {path}"}, 404)
        else:
            self._json(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="引路人 · AI 导师 本地服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8848)
    args = ap.parse_args()

    core.ensure_dirs()
    agents.ensure_users()
    core.STORE.import_faq_hits(core.KB.faq.get("items") or [])  # 旧版 hits 计数一次性迁入 SQLite

    issues = core.validate_data()
    errs = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    llm = core.LLM_CLIENT.status
    mode = f"大模型已接入（{llm['model']}）" if llm["enabled"] else "本地模式（未接入大模型，检索增强可用）"
    print("=" * 62)
    print("  引路人 · AI 导师")
    print(f"  地址：http://{args.host}:{args.port}")
    print(f"  模式：{mode}")
    print(f"  知识：{len(core.KB.entries)} 条知识 / "
          f"{len([i for i in core.KB.faq['items'] if i.get('status') == 'published'])} 条 FAQ")
    if errs or warns:
        print(f"  内容文件：{len(errs)} 个错误 / {len(warns)} 个提醒（页面「设置」里可查看全部）")
        for i in (errs + warns)[:8]:
            print(f"    - [{i['level']}] {i['where']}：{i['message']}")
    else:
        print("  内容文件：校验通过")
    print("  停止：Ctrl + C")
    print("=" * 62)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        srv.server_close()


if __name__ == "__main__":
    main()
