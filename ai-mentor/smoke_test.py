# -*- coding: utf-8 -*-
"""引路人 · 冒烟测试
跑完检查所有接口是否正常。用法：
    python server.py            # 先启动服务
    python smoke_test.py        # 另开一个终端
"""
from __future__ import annotations

import json
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8848"
PASS, FAIL = [], []


def _answers(path_id: str, stage_id: str) -> dict:
    """从关卡配置里读出正确答案，用于验证「全对应当通关」。"""
    p = Path(__file__).resolve().parent / "data" / "paths" / f"{path_id}.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    for s in cfg.get("stages", []):
        if s.get("id") == stage_id:
            return {q["id"]: q["answer"] for q in (s.get("exam") or {}).get("questions", [])}
    return {}


def call(method: str, path: str, body=None):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


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
check("GET /api/exam（不下发答案）",
      lambda: call("GET", "/api/exam?path=engineering&stage=e1"),
      lambda o: o.get("questions") and all("answer" not in q for q in o["questions"]))

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
check("GET /api/dashboard",
      lambda: call("GET", "/api/dashboard"),
      lambda o: o.get("users") and o.get("alerts") and o.get("counters"))

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

print("\n[作业批改]")
check("GET /api/rubrics", lambda: call("GET", "/api/rubrics"),
      lambda o: len(o.get("rubrics") or []) >= 4)
check("POST /api/review（本地启发式可解释）",
      lambda: call("POST", "/api/review", {
          "rubric": "requirement-analysis", "user": "chen_sy", "title": "第一次需求分析",
          "text": "本次需求的目标用户是中小商家，要解决对账耗时的问题，目标是每周节省 3 小时。"
                  "本期只做自动对账，不做自动开票，边界明确。异常情况包括账单为空、重复导入、"
                  "权限不足，都给了错误提示。验收标准：对账耗时从 15 分钟降到 5 分钟以内，准确率 100%。"}),
      lambda o: o.get("items") and o.get("total") is not None)

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

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for name, why in FAIL:
    print(f"  失败：{name} -> {why}")
sys.exit(1 if FAIL else 0)
