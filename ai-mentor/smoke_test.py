# -*- coding: utf-8 -*-
"""引路人 · 冒烟测试
跑完检查所有接口是否正常。用法：
    python server.py            # 先启动服务
    python smoke_test.py        # 另开一个终端
"""
from __future__ import annotations

import json
import random
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 管道 / CI 场景下强制 UTF-8，避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        pass

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8848"
PASS, FAIL = [], []

# 双端分角色登录：从 users.json 读账号与 PIN，按需登录并缓存 token
USERS = {u["id"]: u for u in json.loads(
    (Path(__file__).resolve().parent / "data" / "users.json").read_text(encoding="utf-8-sig")
)["users"]}
TOKENS = {}


def tok(user: str) -> str:
    if user not in TOKENS:
        u = USERS.get(user) or {}
        req = urllib.request.Request(BASE + "/api/login",
            data=json.dumps({"user": user, "pin": str(u.get("pin") or "")}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode("utf-8"))
        TOKENS[user] = resp.get("token") or ""
    return TOKENS[user]


def _answers(path_id: str, stage_id: str) -> dict:
    """从关卡配置里读出正确答案，用于验证「全对应当通关」。"""
    p = Path(__file__).resolve().parent / "data" / "paths" / f"{path_id}.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    for s in cfg.get("stages", []):
        if s.get("id") == stage_id:
            return {q["id"]: q["answer"] for q in (s.get("exam") or {}).get("questions", [])}
    return {}


def call(method: str, path: str, body=None, user=None):
    # 身份自动推导：body.user -> 查询串 user -> 默认导师（导师可访问全部接口）
    if user is None:
        if isinstance(body, dict) and body.get("user"):
            user = str(body["user"])
        else:
            m = re.search(r"[?&]user=([^&]*)", path)
            user = m.group(1) if m else "mentor"
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "X-Token": tok(user)})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def raw(method: str, path: str, body=None, token: str = ""):
    """与 call 相同，但 HTTP 4xx 不抛异常，返回 (status, payload)——供身份保护测试用。"""
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except ValueError:
            payload = {}
        return e.code, payload


def check(name, fn, cond=None):
    try:
        out = fn()
        ok = bool(cond(out)) if cond else True
    except Exception as exc:                      # noqa: BLE001
        FAIL.append((name, f"{exc.__class__.__name__}: {exc}"))
        print(f"  [FAIL] {name}  -> {exc}")
        return {}
    if ok:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append((name, f"条件不满足: {json.dumps(out, ensure_ascii=False, default=str)[:220]}"))
        print(f"  [FAIL] {name}")
    return out


print(f"== 冒烟测试 {BASE} ==\n")

print("[基础]")
check("GET /api/health", lambda: call("GET", "/api/health"), lambda o: o.get("ok"))
meta = check("GET /api/meta", lambda: call("GET", "/api/meta"),
             lambda o: o.get("users") and o.get("knowledge", {}).get("entries", 0) > 0)
check("GET / (页面)", lambda: urllib.request.urlopen(BASE + "/", timeout=10).status, lambda s: s == 200)
check("GET /app.js", lambda: urllib.request.urlopen(BASE + "/app.js", timeout=10).status, lambda s: s == 200)

print("\n[问答 · 可信链路]")
check("命中知识库（应给出处）",
      lambda: call("POST", "/api/ask", {"user": "chen_sy", "question": "开发环境怎么搭建？"}),
      lambda o: not o.get("escalated") and o.get("sources"))
check("命中 FAQ（应给出处）",
      lambda: call("POST", "/api/ask", {"user": "chen_sy", "question": "客户生气了怎么沟通"}),
      lambda o: not o.get("escalated") and o.get("sources"))
check("知识库没有（应转人工）",
      lambda: call("POST", "/api/ask", {"user": "chen_sy", "question": "今天食堂中午有什么菜"}),
      lambda o: o.get("escalated") is True)
check("HARD 敏感话题（钱/人事，知识库有也不答）",
      lambda: call("POST", "/api/ask", {"user": "chen_sy", "question": "我这个月工资多少"}),
      lambda o: o.get("escalated") is True)
check("HARD 敏感话题（法务）",
      lambda: call("POST", "/api/ask", {"user": "chen_sy", "question": "竞业协议怎么签"}),
      lambda o: o.get("escalated") is True)
check("SOFT 敏感话题但有文档（应回答 + 附敏感提醒）",
      lambda: call("POST", "/api/ask", {"user": "chen_sy", "question": "怎么申请软件和工具权限"}),
      lambda o: (not o.get("escalated")) and o.get("notice") and o.get("sources"))
check("SOFT 敏感话题但知识库没有（应转人工）",
      lambda: call("POST", "/api/ask", {"user": "chen_sy", "question": "生产库的root密码是多少"}),
      lambda o: o.get("escalated") is True)
check("匿名提问",
      lambda: call("POST", "/api/ask", {"user": "chen_sy", "question": "试用期怎么转正", "anonymous": True}),
      lambda o: "escalated" in o)
check("POST /api/ask/escalate（没答好主动联系导师）",
      lambda: call("POST", "/api/ask/escalate",
                   {"user": "chen_sy", "question": "想请导师帮我看一下我写的第一份技术方案", "note": "方便的话今天下午"}),
      lambda o: o.get("ok"))
check("GET /api/escalations/mine（我的求助可查）",
      lambda: call("GET", "/api/escalations/mine?user=chen_sy"),
      lambda o: any("技术方案" in (e.get("question") or "") for e in (o.get("items") or [])))

print("\n[检索标定回归]  —— 这些用例锁定了阈值标定结果，改检索参数后必须仍然成立")
SHOULD_HIT = {
    "开发环境怎么搭建？": "开发环境",
    "代码提交规范有哪些？": "代码提交",
    "需求评审的流程是什么？": "需求评审",
    "公司 WiFi 怎么连": "WiFi",
    "VPN 怎么配置？": "VPN",
    "转正流程是什么": "转正",
    "客户要求超出合同范围怎么办": "客户",
    "线上出事故了怎么办": "线上问题",
    "跨部门同事不配合怎么办": "跨部门",
    "本地依赖装不上": "依赖",
    "怎么申请软件权限": "权限",
    "试用期考核主要看什么": "试用期",
}
for q, expect in SHOULD_HIT.items():
    check(f"应命中 -> {expect}",
          lambda q=q, expect=expect: call("POST", "/api/ask", {"user": "demo", "question": q}),
          lambda o, expect=expect: (not o.get("escalated")) and any(
              expect in (s.get("title") or "") for s in (o.get("sources") or [])))

SHOULD_ESCALATE = [
    "今天食堂中午有什么菜", "附近哪家咖啡好喝", "明天天气怎么样",
    "公司股票代码是多少", "帮我写一首诗", "推荐一部电影", "篮球比赛几点开始",
    "打印机怎么加墨粉", "年会在哪个酒店办", "公司班车几点发车",
]
escalated_count = 0
for q in SHOULD_ESCALATE:
    r = call("POST", "/api/ask", {"user": "demo", "question": q})
    if r.get("escalated"):
        escalated_count += 1
    else:
        FAIL.append((f"应转人工 -> {q}", f"却给出了答案：{(r.get('answer') or '')[:80]}"))
        print(f"  [FAIL] 应转人工 -> {q}")
if escalated_count == len(SHOULD_ESCALATE):
    PASS.append("知识库外的 10 个问题全部正确转人工（零误答）")
    print(f"  [OK]   知识库外的 {escalated_count}/{len(SHOULD_ESCALATE)} 个问题全部正确转人工（零误答）")

print("\n[成长地图]")
path = check("GET /api/path",
             lambda: call("GET", "/api/path?user=chen_sy&track=engineering"),
             lambda o: o.get("stages") and o["stages"][0]["status"] != "locked")
check("GET /api/exam（不下发答案与解析）",
      lambda: call("GET", "/api/exam?path=engineering&stage=e1"),
      lambda o: o.get("questions") and all("answer" not in q and "explain" not in q
                                           for q in o["questions"]))

qs = call("GET", "/api/exam?path=engineering&stage=e1")["questions"]
check("POST /api/exam 全对 → 通关",
      lambda: call("POST", "/api/exam", {"user": "chen_sy", "path": "engineering",
                                         "stage": "e1", "answers": _answers("engineering", "e1")}),
      lambda o: o.get("passed") and o.get("score") == 100)
check("POST /api/exam 全错 → 不通关",
      lambda: call("POST", "/api/exam", {"user": "demo", "path": "engineering",
                                         "stage": "e1", "answers": {q["id"]: 9 for q in qs}}),
      lambda o: o.get("passed") is False)
check("通关后解锁下一关",
      lambda: call("GET", "/api/path?user=chen_sy&track=engineering"),
      lambda o: [s for s in o["stages"] if s["id"] == "e2"][0]["status"] != "locked")
check("非研发地图独立可用",
      lambda: call("GET", "/api/path?user=lin_xy&track=non_engineering"),
      lambda o: len(o.get("stages") or []) >= 5)

print("\n[档案与看板]")
check("GET /api/profile",
      lambda: call("GET", "/api/profile?user=chen_sy"),
      lambda o: o.get("radar") and o.get("stats"))
check("GET /api/dashboard（精简版：问题/消息/周报）",
      lambda: call("GET", "/api/dashboard"),
      lambda o: o.get("escalations") is not None and o.get("messages") is not None
      and o.get("weeklies") is not None and o.get("counters"))
dash = call("GET", "/api/dashboard")
escs = (dash.get("escalations") or [])
if escs:
    eid, euser = escs[0]["id"], escs[0]["user"]
    check("POST /api/escalation/answer（导师回复求助）",
          lambda: call("POST", "/api/escalation/answer",
                       {"id": eid, "answer": "收到，这个问题我上午当面跟你讲"}),
          lambda o: o.get("ok"))
    mine = call("GET", "/api/escalations/mine?user=" + euser)
    check("回复后新人「我的求助」能看到答案",
          lambda: mine,
          lambda o: any(e.get("id") == eid and e.get("answer") for e in (o.get("items") or [])))

print("\n[陪练室]")
check("GET /api/scenarios", lambda: call("GET", "/api/scenarios"),
      lambda o: len(o.get("scenarios") or []) >= 5)
sess = call("POST", "/api/coach/start", {"user": "lin_xy", "scenario": "sc-biz-client"})
check("POST /api/coach/start", lambda: sess, lambda o: o.get("session") and o.get("turns"))
if sess.get("session"):
    sid = sess["session"]
    check("POST /api/coach/reply",
          lambda: call("POST", "/api/coach/reply", {"session": sid, "message": "我理解您的着急，先确认一下您最关心的是恢复时间还是补偿？"}),
          lambda o: o.get("reply"))
    check("POST /api/coach/finish（应给出评分）",
          lambda: call("POST", "/api/coach/finish", {"session": sid}),
          lambda o: o.get("result", {}).get("items") and o["result"].get("total") is not None)

print("\n[环境自检]")
check("GET /api/envcheck（列出检查集）", lambda: call("GET", "/api/envcheck"),
      lambda o: len(o.get("sets") or []) >= 3)
check("GET /api/envcheck 执行检查（永不崩溃）",
      lambda: call("GET", "/api/envcheck?id=common&user=chen_sy"),
      lambda o: o.get("results") and o.get("percent") is not None)

print("\n[FAQ 沉淀闭环]  —— 完整的「问到不会 -> 沉淀 -> 下次自动答上」链路")
# 这个测试会往知识库里写东西，所以必须能重复运行：
# 每次随机抽一个知识库里没有的词组合，抽到"已经有答案"的就重抽（说明前几轮跑过了）。
THINGS = ["门禁卡", "工牌", "停车证", "快递柜", "饮水机", "显示器", "键盘", "工位",
          "打印机驱动", "会议室投影", "微波炉", "咖啡机", "更衣柜", "雨伞架"]
ACTS = ["怎么补办", "怎么申请", "坏了找谁", "在哪儿领", "周末能用吗", "下班后能进吗"]

Q, first = None, None
for _ in range(24):
    cand_q = random.choice(THINGS) + random.choice(ACTS)
    r = call("POST", "/api/ask", {"user": "zhao_m", "question": cand_q})
    if r.get("escalated"):
        Q, first = cand_q, r
        break
if Q:
    PASS.append("第 1 次提问：知识库没有 → 转人工")
    print(f"  [OK]   第 1 次提问「{Q}」：知识库没有 → 转人工")
else:
    FAIL.append(("FAQ 闭环", "连续 24 次随机提问都被答上了，无法构造未收录的问题"))
    print("  [FAIL] 无法构造未收录的问题（知识库被前几轮测试撑满了？）")

if Q:
    check("第 2 个人问同一件事：命中同一候选而非新建",
          lambda: call("POST", "/api/ask", {"user": "chen_sy", "question": Q + "啊"}),
          lambda o: o.get("escalated") is True)

drafts = call("GET", "/api/faq/drafts")
cand = next((c for c in (drafts.get("candidates") or []) if c["sample_question"] == Q), None)
if cand:
    PASS.append("候选池按问题指纹正确聚类")
    print(f"  [OK]   候选池按问题指纹正确聚类（「{Q}」被问 {cand['count']} 次）")
else:
    FAIL.append(("候选池聚类", f"没有找到「{Q}」对应的候选"))
    print("  [FAIL] 候选池聚类")

if cand:
    d = call("POST", "/api/faq/draft", {"candidate_id": cand["id"]})
    check("POST /api/faq/draft（生成草稿）", lambda: d, lambda o: o.get("draft"))
    if d.get("draft"):
        check("POST /api/faq/publish（发布后进入检索池）",
              lambda: call("POST", "/api/faq/publish",
                           {"id": d["draft"]["id"],
                            "a": f"关于「{Q}」，请到行政前台办理，工作日 9:00-18:00。"}),
              lambda o: o.get("faq", {}).get("status") == "published")
        check("闭环验证：同一问题现在应被自动答上",
              lambda: call("POST", "/api/ask", {"user": "demo", "question": Q}),
              lambda o: not o.get("escalated"))
        # 清理：把测试写进知识库的 FAQ 删掉，避免反复运行污染内容文件
        call("POST", "/api/faq/delete", {"id": d["draft"]["id"]})
        check("清理：测试产生的 FAQ 已从知识库移除",
              lambda: call("GET", "/api/kb/" + d["draft"]["id"]),
              lambda o: o.get("error"))

print("\n[设置]")
check("GET /api/settings", lambda: call("GET", "/api/settings"),
      lambda o: o.get("settings") and o.get("llm_status"))

print("\n[周报]")
WEEKLY_GOOD = ("本周完成登录模块联调，交付 3 个功能点，登录成功率提升到 99.8%。"
               "遇到验证码服务超时问题，增加重试与降级策略后解决，结果可复现。"
               "下周计划：完成权限模块开发并提测，产出接口文档。")
check("POST /api/weekly（提交并自动批改）",
      lambda: call("POST", "/api/weekly", {"user": "chen_sy", "content": WEEKLY_GOOD}),
      lambda o: o.get("ok") and o.get("score") is not None)
check("周报空白内容应被拒绝",
      lambda: call("POST", "/api/weekly", {"user": "chen_sy", "content": "   "}),
      lambda o: o.get("error"))
check("GET /api/weekly 我的历史",
      lambda: call("GET", "/api/weekly?user=chen_sy"),
      lambda o: len(o.get("mine") or []) >= 1)
check("GET /api/weekly/all 全员列表",
      lambda: call("GET", "/api/weekly/all"),
      lambda o: any(r.get("user") == "chen_sy" for r in (o.get("all") or [])))

print("\n[任务中心]")
TASK_TITLE = "冒烟测试任务 " + str(random.randint(10000, 99999))
CRLF = chr(13) + chr(10)


def multipart_body(fields: dict, files: list) -> tuple:
    """手工拼 multipart/form-data（与 server 端解析器配套）。files: [(字段名, 文件名, 字节)]"""
    b = "----smoke" + str(random.randint(100000, 999999))
    out = []
    for k, v in fields.items():
        out.append(("--" + b + CRLF + "Content-Disposition: form-data; "
                    + "name=" + chr(34) + k + chr(34) + CRLF + CRLF + v + CRLF).encode("utf-8"))
    for name, filename, content in files:
        out.append(("--" + b + CRLF + "Content-Disposition: form-data; name=" + chr(34)
                    + name + chr(34) + "; filename=" + chr(34) + filename + chr(34)
                    + CRLF + "Content-Type: application/octet-stream" + CRLF + CRLF).encode("utf-8"))
        out.append(content)
        out.append(CRLF.encode("ascii"))
    out.append(("--" + b + "--" + CRLF).encode("ascii"))
    return b"".join(out), "multipart/form-data; boundary=" + b


def call_multipart(path: str, fields: dict, files: list, user=None):
    if user is None:
        user = str(fields.get("user") or "mentor")
    body, ctype = multipart_body(fields, files)
    req = urllib.request.Request(BASE + path, data=body, method="POST",
                                 headers={"Content-Type": ctype, "X-Token": tok(user)})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


mt = call_multipart("/api/tasks",
                    {"user": "mentor", "title": TASK_TITLE,
                     "description": "端到端测试任务，可删除", "assignee": "",
                     "item_title_0": "回答：这个功能的边界是什么",
                     "item_itype_0": "question",
                     "item_content_0": "阅读附件后回答：这个功能明确不做什么？",
                     "item_title_1": "确认需求文档",
                     "item_itype_1": "requirement",
                     "item_content_1": "阅读附件需求，回帖确认理解一致"},
                    [("files", "附件说明.txt", "附件内容 ABC123".encode("utf-8"))])
check("POST /api/tasks（发布任务 + 两个子项 + 附件）", lambda: mt, lambda o: o.get("ok") and o.get("id"))

tl = call("GET", "/api/tasks?user=chen_sy")
task = next((t for t in (tl.get("tasks") or []) if t.get("title") == TASK_TITLE), None)
check("GET /api/tasks（任务、两个子项与附件都在）", lambda: task,
      lambda o: o and len(o.get("items") or []) == 2 and len(o.get("files") or []) == 1)

if task:
    furl = BASE + "/api/files/" + str(task["files"][0]["id"])
    with urllib.request.urlopen(urllib.request.Request(furl, headers={"X-Token": tok("mentor")}), timeout=10) as fr:
        fbody = fr.read().decode("utf-8")
    check("GET /api/files（附件字节级还原）", lambda: fbody,
          lambda s: s == "附件内容 ABC123")

    it0 = task["items"][0]
    fbr = call_multipart("/api/tasks/feedback",
                         {"user": "chen_sy", "task_id": str(task["id"]),
                          "item_id": str(it0["id"]),
                          "content": "边界是明确不做自动对账，其余见附件。"},
                         [("files", "截图.png", bytes([137]) + b"PNG-mock")])
    check("POST /api/tasks/feedback（按子项反馈 + 附件）", lambda: fbr, lambda o: o.get("ok"))

    t2 = next((t for t in (call("GET", "/api/tasks?user=chen_sy").get("tasks") or [])
               if t["id"] == task["id"]), None)
    check("子项进度与完成状态正确", lambda: t2,
          lambda o: o and o["items"][0]["my_done"] and not o["items"][1]["my_done"]
          and o["items_my_done"] == 1 and o["items_total"] == 2)
    fb_id = (t2["items"][0]["feedbacks"] or [{}])[0].get("id")
    check("POST /api/feedback/confirm（导师逐项确认）",
          lambda: call("POST", "/api/feedback/confirm", {"id": fb_id, "note": "回答到位"}),
          lambda o: o.get("ok"))
    t3 = next((t for t in (call("GET", "/api/tasks?user=chen_sy").get("tasks") or [])
               if t["id"] == task["id"]), None)
    check("确认后子项反馈状态变为 confirmed",
          lambda: t3, lambda o: o and (o["items"][0]["feedbacks"] or [{}])[0].get("status") == "confirmed")
    dash2 = call("GET", "/api/dashboard")
    qesc = next((e for e in (dash2.get("escalations") or [])
                 if "任务提问" in (e.get("reason") or "") and TASK_TITLE[:10] in (e.get("question") or "")), None)
    if qesc:
        check("「问题」子项反馈自动进入导师待回复队列", lambda: qesc, lambda o: o.get("id"))
        check("导师回复后答案回流到子项点评",
              lambda: call("POST", "/api/escalation/answer",
                           {"id": qesc["id"], "answer": "边界确认无误，下一步看接口文档"}),
              lambda o: o.get("ok"))
        t4 = next((t for t in (call("GET", "/api/tasks?user=chen_sy").get("tasks") or [])
                   if t["id"] == task["id"]), None)
        check("回流验证：子项反馈可见导师回复",
              lambda: t4,
              lambda o: o and "边界确认无误" in ((o["items"][0]["feedbacks"] or [{}])[0].get("note") or ""))
    check("POST /api/tasks/delete（清理测试任务与附件）",
          lambda: call("POST", "/api/tasks/delete", {"id": task["id"]}),
          lambda o: o.get("ok"))
    gone = raw("GET", "/api/files/" + str(task["files"][0]["id"]), token=tok("mentor"))
    check("删除任务后附件随之失效（404）", lambda: gone, lambda o: o[0] == 404)

print("\n[项目知识库 · AI 生成]")
PNAME = "冒烟卫星项目 " + str(random.randint(10000, 99999))
imp = call_multipart("/api/projects/import",
                     {"user": "mentor", "name": PNAME, "description": "端到端测试项目"},
                     [("files", "README.md", "卫星遥测数据解析工具，用于处理下传数据。".encode("utf-8")),
                      ("files", "requirements.txt", b"numpy==1.26" + bytes([10]) + b"pandas==2.1")],
                     user="mentor")
check("POST /api/projects/import（导入项目并生成知识库）", lambda: imp,
      lambda o: o.get("ok") and o.get("generated", 0) >= 2)
pid = (imp or {}).get("project", {}).get("id", "")
meta2 = call("GET", "/api/meta")
check("项目进入 meta 列表（与通用知识库分开管理）", lambda: meta2,
      lambda o: any(p.get("id") == pid for p in (o.get("projects") or [])))
askp = call("POST", "/api/ask", {"user": "demo", "question": PNAME + " 需要装什么环境", "project": pid})
check("项目内提问命中该项目知识库（隔离生效）", lambda: askp,
      lambda o: (not o.get("escalated")) and any("项目资料概览" in (s.get("title") or "") for s in (o.get("sources") or [])))

with urllib.request.urlopen(urllib.request.Request(BASE + "/api/weekly/export",
        headers={"X-Token": tok("mentor")}), timeout=15) as fr:
    exp = fr.read().decode("utf-8")
check("GET /api/weekly/export（周报导出为 Markdown 文件）", lambda: exp,
      lambda s: "新人周报导出" in s and "周" in s)

check("POST /api/tasks/ai-draft（AI 起草任务，未接模型走保底拆分）",
      lambda: call("POST", "/api/tasks/ai-draft",
                   {"description": "完成登录模块开发" + chr(10) + "阅读接口文档" + chr(10) + "联调测试"},
                   user="mentor"),
      lambda o: o.get("ok") and o.get("draft", {}).get("title") and len(o["draft"].get("items") or []) >= 1)

check("POST /api/projects/delete（清理测试项目与其知识）",
      lambda: call("POST", "/api/projects/delete", {"id": pid}, user="mentor"),
      lambda o: o.get("ok"))
meta3 = call("GET", "/api/meta")
check("清理后项目不再出现", lambda: meta3,
      lambda o: not any(p.get("id") == pid for p in (o.get("projects") or [])))

print("\n[身份保护 · 角色与绑定]  —— 双端登录的权限边界")
check("无 token 访问导师看板应 401",
      lambda: raw("GET", "/api/dashboard"), lambda o: o[0] == 401)
check("错误 PIN 登录被拒绝",
      lambda: raw("POST", "/api/login", {"user": "demo", "pin": "9999"}),
      lambda o: o[1].get("error"))
login = check("正确 PIN 登录发放 token 并带角色",
              lambda: raw("POST", "/api/login", {"user": "demo", "pin": USERS["demo"]["pin"]}),
              lambda o: o[0] == 200 and o[1].get("token") and o[1].get("user", {}).get("role") == "newcomer")
dtok = (login or (0, {}))[1].get("token") or ""
check("新人账号访问导师看板应 403（角色门禁）",
      lambda: raw("GET", "/api/dashboard", token=dtok), lambda o: o[0] == 403)
check("新人发布任务应 403（角色门禁）",
      lambda: raw("POST", "/api/tasks", {"user": "demo", "title": "越权测试"}, token=dtok),
      lambda o: o[0] == 403)
check("登录身份与请求用户不一致应 403（身份绑定）",
      lambda: raw("POST", "/api/ask", {"user": "chen_sy", "question": "开发环境怎么搭建？"}, token=dtok),
      lambda o: o[0] == 403)
check("新人以本人身份提问正常",
      lambda: raw("POST", "/api/ask", {"user": "demo", "question": "开发环境怎么搭建？"}, token=dtok),
      lambda o: o[0] == 200 and not o[1].get("error"))
check("导师账号可访问导师看板",
      lambda: raw("GET", "/api/dashboard", token=tok("mentor")), lambda o: o[0] == 200)
check("GET /api/meta 报告 auth_required",
      lambda: raw("GET", "/api/meta"),
      lambda o: o[0] == 200 and o[1].get("auth_required") is True)

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for name, why in FAIL:
    print(f"  失败：{name} -> {why}")
sys.exit(1 if FAIL else 0)
