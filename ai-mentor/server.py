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
import sys
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agents  # noqa: E402
import core  # noqa: E402
from core import SETTINGS, update_settings, WEB_DIR  # noqa: E402

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
    if path == "/api/settings":
        s = json.loads(json.dumps(SETTINGS))
        s["llm"] = {**s.get("llm", {}),
                    "api_key": "***" if s.get("llm", {}).get("api_key") else ""}
        return {"settings": s, "llm_status": core.LLM_CLIENT.status}
    if path == "/api/health":
        return {"ok": True}
    return None


def route_post(path: str, body: dict):
    if path == "/api/ask":
        return agents.ask(body.get("user") or "demo", body.get("question", ""),
                          bool(body.get("anonymous")), body.get("track"))
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
        body = self._read_body()
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
