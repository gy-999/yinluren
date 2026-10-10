# -*- coding: utf-8 -*-
"""引路人 · 业务层
八个模块：环境自检 / 知识问答 / 关卡闯关 / 陪练室 / 作业批改 / 成长档案 / 导师看板 / FAQ 沉淀

所有模块都遵循同一原则：
  接入大模型时用模型生成，未接入时**降级为本地可用模式**，绝不让功能变成不可用。
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import subprocess
import uuid
from datetime import datetime

from core import (
    DATA_DIR, FILES_DIR, KB, LLM_CLIENT, SETTINGS, STORE, load_dir_json, load_json,
    load_projects, save_projects, load_users_file, auth_enabled, save_json,
    sensitive_hit, is_overdue, tokenize, today_str, validate_data, _as_list,
)

MIN_PASS_DEFAULT = 80


# ================================================================ 元信息

def meta() -> dict:
    users = STORE.users()
    paths = load_dir_json("paths")
    issues = validate_data()
    return {
        "product": "引路人 · AI 导师",
        "tracks": sorted({p.get("track", "general") for p in paths}) or ["general"],
        "projects": load_projects(),
        "dimensions": dimensions(),
        "users": users,
        "llm": LLM_CLIENT.status,
        "auth_required": auth_enabled(),
        "issues": issues,
        "errors": len([i for i in issues if i["level"] == "error"]),
        "knowledge": {
            "entries": len(KB.entries),
            "faq_published": len([i for i in KB.faq["items"] if i.get("status") == "published"]),
            "faq_draft": len([i for i in KB.faq["items"] if i.get("status") == "draft"]),
        },
        "paths": [{"id": p.get("id"), "name": p.get("name"), "track": p.get("track"),
                   "stages": len(p.get("stages") or [])} for p in paths],
    }


def dimensions() -> list:
    cfg = load_json(DATA_DIR / "dimensions.json", {})
    return cfg.get("dimensions") or [
        {"id": "business", "name": "业务理解", "baseline": 70, "anchors": {}},
        {"id": "professional", "name": "专业能力", "baseline": 70, "anchors": {}},
        {"id": "communication", "name": "协作沟通", "baseline": 70, "anchors": {}},
        {"id": "discipline", "name": "规范意识", "baseline": 70, "anchors": {}},
    ]


def ensure_users() -> None:
    seed = load_json(DATA_DIR / "users.json", {"users": []}).get("users") or []
    for u in seed:
        STORE.upsert_user(u.get("id"), u.get("name"), u.get("track", "general"),
                          u.get("cohort", ""), u.get("joined_at", ""),
                          str(u.get("role") or "newcomer"))


def login(uid: str, pin: str) -> dict:
    """选人 + PIN 登录。PIN 在 users.json 里按人配置；没有任何人配置时全站不启用。
    登录成功返回 token 与角色，之后所有请求带 X-Token 头，且只能以本人身份操作。"""
    users = {u["id"]: u for u in load_users_file()}
    u = users.get(uid or "")
    if not u:
        return {"error": "用户不存在"}
    want = str(u.get("pin") or "").strip()
    if want and str(pin or "").strip() != want:
        return {"error": "PIN 不正确"}
    role = str(u.get("role") or "newcomer")
    token = STORE.create_session(uid, role)
    return {"ok": True, "token": token,
            "user": {"id": u["id"], "name": u.get("name", uid),
                     "track": u.get("track", ""), "role": role}}


# ================================================================ ② 知识问答

def _fingerprint(question: str) -> list:
    return sorted(set(tokenize(question)))


def _jaccard(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _source_of(doc: dict) -> dict:
    p = doc.get("payload") or {}
    return {
        "id": doc.get("id"),
        "kind": doc.get("kind"),
        "title": doc.get("title"),
        "score": doc.get("score"),
        "coverage": doc.get("coverage"),
        "owner": p.get("owner", "未指定"),
        "source": p.get("source", ""),
        "review_date": p.get("review_date", ""),
        "stale": is_overdue(p.get("review_date", "")),
    }


def _record_candidate(question: str) -> None:
    """未答上来的问题进入沉淀候选池，同意图累计到阈值后生成草稿。"""
    fp = _fingerprint(question)
    if not fp:
        return
    threshold = float(SETTINGS["retrieval"].get("fingerprint_similarity", 0.62))
    for c in STORE.candidates("open"):
        try:
            other = json.loads(c["fingerprint"])
        except (ValueError, TypeError):
            other = []
        if _jaccard(fp, other) >= threshold:
            STORE.bump_candidate(c["id"])
            return
    STORE.add_candidate(question, json.dumps(fp, ensure_ascii=False))


def ask(user: str, question: str, anonymous: bool = False, track: str | None = None,
        project: str | None = None) -> dict:
    question = (question or "").strip()
    if not question:
        return {"error": "问题不能为空"}

    rcfg = SETTINGS["retrieval"]
    top_k = int(rcfg.get("top_k", 4))
    match_th = float(rcfg.get("match_threshold", 0.9))
    strong_th = float(rcfg.get("strong_threshold", 1.6))
    min_cov = float(rcfg.get("min_coverage", 0.30))

    hits = KB.search(question, top_k=top_k, project=(project or None))
    top_score = hits[0]["score"] if hits else 0.0
    top_cov = hits[0]["coverage"] if hits else 0.0
    # 覆盖率低 = 问句里很多词在文档里根本找不到，此时要求更高的分数才敢回答
    need = match_th if top_cov >= min_cov else strong_th
    sources = [_source_of(h) for h in hits if h["score"] > 0.05]

    # 兜底规则 1：HARD 敏感话题（钱 / 人事 / 法务）无条件转人工，知识库有也不答
    kind, hit_word = sensitive_hit(question)
    if kind == "hard":
        reason = f"涉及「{hit_word}」，这类问题高度依赖个人情况，必须由导师或 HR 当面确认"
        STORE.add_question(user, question, anonymous, top_score,
                           [s["id"] for s in sources], True, False)
        STORE.add_escalation(user, question, reason)
        STORE.add_event(user, "ask", "", None, {"escalated": True})
        return {"escalated": True, "reason": reason, "answer": None,
                "sources": sources, "confidence": top_score, "mode": "escalate",
                "contact": SETTINGS["mentor"].get("escalate_contact", "导师")}

    # 兜底规则 2：知识库没有可靠依据 → 转人工 + 进沉淀池
    if not hits or top_score < need:
        if kind == "soft":
            reason = f"涉及「{hit_word}」，且知识库里没有对应的流程说明，已转给导师"
        else:
            reason = "知识库里没有找到可靠依据，已记录并转给导师"
        STORE.add_question(user, question, anonymous, top_score, [], True, False)
        STORE.add_escalation(user, question, reason)
        _record_candidate(question)
        STORE.add_event(user, "ask", "", None, {"escalated": True})
        return {"escalated": True, "reason": reason, "answer": None,
                "sources": [], "confidence": top_score, "mode": "escalate",
                "contact": SETTINGS["mentor"].get("escalate_contact", "导师")}

    # 命中已发布 FAQ → 计数（存 SQLite，不写回内容文件）
    if hits[0]["kind"] == "faq":
        STORE.bump_faq_hit(hits[0]["id"])

    context = _build_context(hits)
    answer, mode = _compose_answer(question, hits, context, strong_th, top_score, kind, hit_word)
    strong = top_score >= strong_th and top_cov >= min_cov

    STORE.add_question(user, question, anonymous, top_score,
                       [h["id"] for h in hits], False, True)
    STORE.add_event(user, "ask", hits[0]["id"], None,
                    {"matched": hits[0]["id"], "escalated": False})

    return {
        "escalated": False,
        "answer": answer,
        "mode": mode,
        "project": project or "",
        "confidence": top_score,
        "coverage": top_cov,
        "strong": strong,
        "sources": sources,
        "contact": SETTINGS["mentor"].get("escalate_contact", "导师"),
        "caveat": None if strong else "匹配度一般，建议向导师确认后再照做。",
        "notice": (f"这条涉及「{hit_word}」，属于敏感操作。上面是文档里写的流程，"
                   f"实际操作前请再向导师确认一次。" if kind == "soft" else None),
    }


def _build_context(hits: list) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        body = h.get("text") or ""
        if h["kind"] == "faq":
            body = f"问题：{h['title']}\n答案：{body}"
        blocks.append(f"[{i}] {h.get('title', '')}\n{body[:1200]}")
    return "\n\n".join(blocks)


def _compose_answer(question: str, hits: list, context: str, strong_th: float,
                    top_score: float, kind: str | None = None, hit_word: str = ""):
    has_llm = LLM_CLIENT.enabled
    if has_llm:
        system = (
            "你是公司内部的『引路人』AI 导师，负责给新员工答疑。\n"
            "铁律：\n"
            "1. 只能依据下面提供的【参考资料】回答，不许使用你自己的通用知识补充事实。\n"
            "2. 每个关键结论后面必须用 [1][2] 这样的编号标注来源。\n"
            "3. 参考资料里没有的内容，直接说『资料里没写』并建议联系导师，绝不猜测。\n"
            "4. 用中文，口语化、简洁，面向刚入职的新人，必要时给出分步骤操作。\n"
        )
        if kind == "soft":
            system += (
                f"5. 这个问题涉及「{hit_word}」这类敏感操作。答案必须严格照抄资料里的流程，"
                "并在结尾明确提醒新人：动手前先找导师确认一次。\n"
            )
        user_msg = f"【参考资料】\n{context}\n\n【新人的问题】\n{question}"
        out = LLM_CLIENT.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.2)
        if out:
            return out, "llm"

    # ---- 本地模式：直接返回知识原文 + 出处（可信优先，宁可少说也不错说）----
    lines = ["**未接入大模型**，以下直接给出知识库中匹配度最高的内容原文：\n"]
    for i, h in enumerate(hits[:3], 1):
        if h["score"] < 0.05:
            continue
        body = (h.get("text") or "").strip()
        if h["kind"] == "faq":
            lines.append(f"**[{i}] {h['title']}**\n\n{body}\n")
        else:
            lines.append(f"**[{i}] {h['title']}**\n\n{body[:900]}\n")
    if len(lines) == 1:
        lines.append("（匹配到的内容过短，建议直接联系导师）")
    return "\n".join(lines), "local"


def kb_search(q: str, limit: int = 20) -> dict:
    if not q:
        items = [{"id": e["id"], "kind": "kb", "title": e["title"], "tags": e["tags"],
                  "owner": e["owner"], "review_date": e["review_date"],
                  "stale": is_overdue(e["review_date"])} for e in KB.entries]
        for it in KB.faq["items"]:
            if it.get("status") == "published":
                items.append({"id": it["id"], "kind": "faq", "title": it.get("q"),
                              "tags": _as_list(it.get("tags")), "owner": it.get("owner"),
                              "review_date": it.get("review_date", ""),
                              "stale": is_overdue(it.get("review_date", ""))})
        return {"items": items[:limit], "total": len(items)}
    return {"items": [{**_source_of(h), "snippet": (h.get("text") or "")[:180]}
                      for h in KB.search(q, top_k=limit)], "total": None}


def kb_get(entry_id: str):
    e = KB.get_entry(entry_id)
    if not e:
        return None
    if "body" in e:
        return {**e, "kind": "kb", "stale": is_overdue(e.get("review_date", ""))}
    return {**e, "kind": "faq", "body": e.get("a", ""),
            "stale": is_overdue(e.get("review_date", ""))}


# ================================================================ ③ 关卡闯关

def _load_path(track: str | None = None, path_id: str | None = None) -> dict | None:
    paths = load_dir_json("paths")
    if not paths:
        return None
    if path_id:
        for p in paths:
            if p.get("id") == path_id:
                return p
    if track:
        for p in paths:
            if p.get("track") == track:
                return p
    return paths[0]


def path_with_progress(track: str | None = None, user: str | None = None,
                       path_id: str | None = None) -> dict:
    p = _load_path(track, path_id)
    if not p:
        return {"error": "还没有配置任何关卡地图（data/paths/*.json）"}
    stages = p.get("stages") or []
    prog = STORE.get_progress(user, p["id"]) if user else {}

    out_stages = []
    prev_id = None
    for idx, s in enumerate(stages):
        sid = s.get("id")
        requires = s.get("requires")
        if requires is None:
            requires = [prev_id] if (prev_id and idx > 0) else []
        rec = prog.get(sid)
        status = (rec or {}).get("status") or ("available" if not requires else "locked")
        # 依赖已通关则解锁
        if status == "locked" and requires:
            if all((prog.get(r) or {}).get("status") == "passed" for r in requires):
                status = "available"
        if not requires:
            status = status if status == "passed" else "available"
        out_stages.append({
            "id": sid,
            "name": s.get("name"),
            "window": s.get("window", ""),
            "goal": s.get("goal", ""),
            "requires": requires,
            "learn": s.get("learn") or [],
            "practice": s.get("practice") or [],
            "question_count": len((s.get("exam") or {}).get("questions") or []),
            "pass_score": (s.get("exam") or {}).get("pass_score", MIN_PASS_DEFAULT),
            "status": status,
            "best_score": (rec or {}).get("best_score"),
            "attempts": (rec or {}).get("attempts", 0),
            "updated_at": (rec or {}).get("updated_at"),
        })
        prev_id = sid

    passed = len([s for s in out_stages if s["status"] == "passed"])
    return {
        "id": p.get("id"), "name": p.get("name"), "track": p.get("track"),
        "description": p.get("description", ""),
        "stages": out_stages,
        "summary": {"total": len(out_stages), "passed": passed,
                    "percent": round(passed / len(out_stages) * 100) if out_stages else 0},
    }


def exam_questions(path_id: str, stage_id: str) -> list:
    p = _load_path(path_id=path_id)
    if not p:
        return []
    for s in p.get("stages") or []:
        if s.get("id") == stage_id:
            # 不下发答案与解析：解析常在复述正确答案，懂 devtools 的人可以提前偷看。
            # 解析只在交卷后的 details 里返回（见 submit_exam）。
            skip = ("answer", "explain")
            return [{k: v for k, v in q.items() if k not in skip}
                    for q in (s.get("exam") or {}).get("questions") or []]
    return []


def submit_exam(user: str, path_id: str, stage_id: str, answers: dict) -> dict:
    p = _load_path(path_id=path_id)
    if not p:
        return {"error": "关卡地图不存在"}
    stage = next((s for s in (p.get("stages") or []) if s.get("id") == stage_id), None)
    if not stage:
        return {"error": "关卡不存在"}

    questions = (stage.get("exam") or {}).get("questions") or []
    pass_score = (stage.get("exam") or {}).get("pass_score", MIN_PASS_DEFAULT)

    dim_correct, dim_total = {}, {}
    details, correct = [], 0
    for q in questions:
        qid = q.get("id")
        dim = q.get("dimension", "professional")
        dim_total[dim] = dim_total.get(dim, 0) + 1
        picked = answers.get(qid)
        ok = picked is not None and int(picked) == int(q.get("answer", -1))
        if ok:
            correct += 1
            dim_correct[dim] = dim_correct.get(dim, 0) + 1
        details.append({"id": qid, "q": q.get("q"), "ok": ok,
                        "picked": picked, "answer": q.get("answer"),
                        "explain": q.get("explain", ""), "dimension": dim})

    score = round(correct / len(questions) * 100) if questions else 0
    passed = score >= pass_score

    dims = {d: round(dim_correct.get(d, 0) / dim_total[d] * 100)
            for d in dim_total if dim_total[d]}
    STORE.set_progress(user, p["id"], stage_id, "passed" if passed else "available", score)
    STORE.add_event(user, "exam", f"{p['id']}/{stage_id}", score,
                    {"dimensions": dims, "passed": passed})

    return {
        "score": score, "passed": passed, "pass_score": pass_score,
        "correct": correct, "total": len(questions),
        "dimensions": dims, "details": details,
        "next": _next_stage(p, stage_id) if passed else None,
    }


def _next_stage(path: dict, stage_id: str):
    stages = path.get("stages") or []
    for i, s in enumerate(stages):
        if s.get("id") == stage_id and i + 1 < len(stages):
            return {"id": stages[i + 1].get("id"), "name": stages[i + 1].get("name")}
    return None


# ================================================================ ④ 陪练室

def scenarios() -> list:
    return [{"id": s.get("id"), "name": s.get("name"), "track": s.get("track"),
             "difficulty": s.get("difficulty", 3), "rubric": s.get("rubric"),
             "brief": s.get("brief", ""), "role": s.get("role", "")}
            for s in load_dir_json("scenarios")]


def _load_scenario(sid: str):
    for s in load_dir_json("scenarios"):
        if s.get("id") == sid:
            return s
    return None


def _load_rubric(rid: str):
    for r in load_dir_json("rubrics"):
        if r.get("id") == rid:
            return r
    return None


def coach_start(user: str, scenario_id: str) -> dict:
    sc = _load_scenario(scenario_id)
    if not sc:
        return {"error": "陪练场景不存在"}
    rubric = _load_rubric(sc.get("rubric") or "")
    sid = uuid.uuid4().hex[:12]
    mode = "llm" if LLM_CLIENT.enabled else "self_guided"

    if mode == "llm":
        opening = sc.get("opening") or "我们开始吧。"
    else:
        items = (rubric or {}).get("items") or []
        first = items[0]["name"] if items else "你的思路"
        opening = (
            f"{sc.get('opening') or ''}\n\n"
            f"（当前未接入大模型，进入 **结构化引导自省模式**：我会按评分标准逐条追问你，"
            f"帮你把答案想清楚。先说第一条 —— **{first}**。）"
        ).strip()

    turns = [{"role": "assistant", "content": opening}]
    STORE.save_session(sid, user, scenario_id, turns, "active",
                       {"mode": mode, "rubric": sc.get("rubric"),
                        "probed": 0, "user_turns": 0})
    STORE.add_event(user, "coach", scenario_id, None, {"started": True})
    return {"session": sid, "mode": mode, "turns": turns,
            "scenario": {"id": sc.get("id"), "name": sc.get("name"), "role": sc.get("role"),
                         "brief": sc.get("brief"), "max_turns": sc.get("max_turns", 6),
                         "rubric": sc.get("rubric"), "tips": sc.get("coach_tips") or []}}


def coach_reply(session_id: str, message: str) -> dict:
    sess = STORE.get_session(session_id)
    if not sess:
        return {"error": "会话不存在"}
    sc = _load_scenario(sess["scenario"]) or {}
    rubric = _load_rubric(sc.get("rubric") or "") or {}
    items = rubric.get("items") or []
    turns = sess["turns"]
    meta_state = sess.get("result") or {}
    mode = meta_state.get("mode", "self_guided")
    max_turns = int(sc.get("max_turns", 6))

    turns.append({"role": "user", "content": message})
    meta_state["user_turns"] = int(meta_state.get("user_turns", 0)) + 1

    if mode == "llm":
        system = (
            f"你在扮演一个角色扮演训练场景中的对手方：{sc.get('role')}。\n"
            f"场景背景：{sc.get('brief')}\n"
            f"你的行为准则：真实、有压迫感、不轻易放过含糊的回答，每次只追问一个点，"
            f"回复控制在 3 句以内。不要跳出角色，不要给建议，不要夸奖。\n"
            f"当新人回答到位或轮次接近上限时，用一句『好，那我们聊到这里』结束对话。\n"
        )
        msgs = [{"role": "system", "content": system}] + \
               [{"role": t["role"], "content": t["content"]} for t in turns][-12:]
        reply = LLM_CLIENT.chat(msgs, temperature=0.7, max_tokens=400)
        if not reply:
            reply = "（模型调用失败，请检查设置里的 LLM 配置。）"

    else:
        probed = int(meta_state.get("probed", 0))
        if probed < len(items):
            item = items[probed]
            meta_state["probed"] = probed + 1
            hint = "、".join(_as_list(item.get("signals", {}).get("good"))[:3]) if isinstance(
                item.get("signals"), dict) else ""
            reply = (f"记下了。接着看 **{item['name']}**：\n\n"
                     f"{item.get('probe') or '这一块你打算怎么做？说说你的具体做法和理由。'}")
            if hint:
                reply += f"\n\n_（可以自查：有没有说到 {hint}）_"
        else:
            reply = ("评分标准的每一条都问完了。可以点『结束并评分』拿到反馈；"
                     "如果还有想补充的，继续说也可以。")

    turns.append({"role": "assistant", "content": reply})
    done = (mode == "self_guided" and int(meta_state.get("probed", 0)) >= len(items)) or \
           (meta_state["user_turns"] >= max_turns)
    STORE.save_session(session_id, sess["user"], sess["scenario"], turns, "active", meta_state)
    return {"reply": reply, "turns": turns, "suggest_finish": done, "mode": mode}


def coach_finish(session_id: str) -> dict:
    sess = STORE.get_session(session_id)
    if not sess:
        return {"error": "会话不存在"}
    sc = _load_scenario(sess["scenario"]) or {}
    rubric = _load_rubric(sc.get("rubric") or "") or {}
    items = rubric.get("items") or []
    user_text = "\n".join(t["content"] for t in sess["turns"] if t["role"] == "user")

    if LLM_CLIENT.enabled and user_text.strip():
        scored, llm_ok = _llm_score_items(rubric, user_text, "陪练对话")
        mode = "llm" if llm_ok else "heuristic"
    else:
        scored = _heuristic_score_items(items, user_text)
        mode = "heuristic"

    total = _weighted_total(scored)
    result = {
        "mode": mode,
        "items": scored,
        "total": total,
        "summary": _coach_summary(total),
        "advice": _advice_from(scored),
        "model_answer": sc.get("model_answer", ""),
        "coach_tips": sc.get("coach_tips") or [],
    }
    STORE.save_session(session_id, sess["user"], sess["scenario"], sess["turns"],
                       "finished", {**(sess.get("result") or {}), **result})
    dims = {"communication": float(total)}
    STORE.add_event(sess["user"], "coach", sess["scenario"], total, {"dimensions": dims})
    return {"result": result, "scenario": {"id": sc.get("id"), "name": sc.get("name")}}


def _weighted_total(scored: list) -> float:
    total_w = sum(i.get("weight", 0) for i in scored) or 1
    got = sum(i.get("score", 0) / 5 * i.get("weight", 0) for i in scored)
    return round(got / total_w * 100, 1)


def _heuristic_score_items(items: list, text: str) -> list:
    """本地启发式评分。基于评分标准里配置的关键信号词，可解释、可被导师校准。"""
    out = []
    norm = (text or "").lower()
    for it in items:
        signals = it.get("signals") if isinstance(it.get("signals"), dict) else {}
        good = [s for s in _as_list(signals.get("good")) if s and s.lower() in norm]
        bad = [s for s in _as_list(signals.get("bad")) if s and s.lower() in norm]
        score = 3 + (1 if good else 0) - (1 if bad else 0)
        if len(good) >= 3:
            score += 1
        score = max(1, min(5, score))
        uncertain = not good and not bad
        why = []
        if good:
            why.append("命中要点：" + "、".join(good[:4]))
        if bad:
            why.append("需要避免：" + "、".join(bad[:3]))
        if uncertain:
            why.append("未识别到明显信号，建议导师人工复核")
        out.append({
            "id": it.get("id"), "name": it.get("name"), "weight": it.get("weight", 25),
            "score": score, "reason": "；".join(why) or "—",
            "uncertain": uncertain, "needs_review": uncertain,
            "levels": it.get("levels", {}),
        })
    return out


def _llm_score_items(rubric: dict, text: str, kind: str) -> tuple[list, bool]:
    """返回 (评分项, 是否真的走了模型)。模型调用失败时降级为启发式，
    但必须把这件事如实上报——否则新人会以为低分是模型给的，
    其实只是启发式没识别到信号，批改的公信力就这么丢的。"""
    items = rubric.get("items") or []
    schema_desc = json.dumps([{"id": i.get("id"), "name": i.get("name"),
                               "levels": i.get("levels", {})} for i in items],
                             ensure_ascii=False)
    system = (
        "你是严格的、公正的评估员。按给定的评分维度逐项打分（1-5 整数），并给出不超过 40 字的中文理由。\n"
        "评分标准越严格越好，不要给人情分。只输出 JSON 数组，不要任何多余文字。\n"
        '格式：[{"id":"...","score":3,"reason":"..."}]'
    )
    user_msg = f"【评估类型】{kind}\n【评分维度】{schema_desc}\n\n【被评估内容】\n{text[:6000]}"
    out = LLM_CLIENT.chat([{"role": "system", "content": system},
                           {"role": "user", "content": user_msg}],
                          temperature=0.1, max_tokens=900)
    parsed = _parse_json_array(out) if out else None
    if not parsed:
        return _heuristic_score_items(items, text), False
    by_id = {str(p.get("id")): p for p in parsed if isinstance(p, dict)}
    result = []
    for it in items:
        p = by_id.get(str(it.get("id"))) or {}
        try:
            score = int(p.get("score", 3))
        except (TypeError, ValueError):
            score = 3
        result.append({
            "id": it.get("id"), "name": it.get("name"), "weight": it.get("weight", 25),
            "score": max(1, min(5, score)),
            "reason": str(p.get("reason", ""))[:120] or "—",
            "uncertain": False, "needs_review": False,
            "levels": it.get("levels", {}),
        })
    return result, True


def _parse_json_array(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re_sub_fence(text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def re_sub_fence(text: str) -> str:
    lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
    return "\n".join(lines)


def _coach_summary(total: float) -> str:
    if total >= 85:
        return "表现优秀，已经接近可以独立面对这个场景的水平。"
    if total >= 70:
        return "基本合格，结构没问题，但在细节和取舍的说明上还能更扎实。"
    if total >= 55:
        return "还比较生涩，建议先把要点列成提纲再练一遍。"
    return "差距比较明显，建议先看知识库里对应的条目，再找导师过一遍。"


def _advice_from(scored: list) -> list:
    weak = sorted(scored, key=lambda x: (x["score"], -x.get("weight", 0)))[:3]
    tips = []
    for w in weak:
        levels = w.get("levels") or {}
        anchor = levels.get("5") or levels.get(5) or ""
        tips.append(f"**{w['name']}**（{w['score']}/5）：{w.get('reason', '')}"
                    + (f"　→ 满分标准：{anchor}" if anchor else ""))
    return tips


# ================================================================ ⑤ 作业 / 代码批改

def rubrics() -> list:
    return [{"id": r.get("id"), "name": r.get("name"),
             "items": [{"id": i.get("id"), "name": i.get("name"),
                        "weight": i.get("weight", 25)} for i in (r.get("items") or [])]}
            for r in load_dir_json("rubrics")]


def review(rubric_id: str, text: str, user: str = "", title: str = "") -> dict:
    rubric = _load_rubric(rubric_id)
    if not rubric:
        return {"error": "评分标准不存在"}
    text = (text or "").strip()
    if not text:
        return {"error": "提交内容不能为空"}

    if LLM_CLIENT.enabled:
        items, llm_ok = _llm_score_items(rubric, text, f"作业批改：{title or rubric.get('name')}")
        mode = "llm" if llm_ok else "heuristic"
    else:
        items = _heuristic_score_items(rubric.get("items") or [], text)
        mode = "heuristic"

    total = _weighted_total(items)
    needs_review = [i for i in items if i.get("needs_review")]
    if user:
        STORE.add_event(user, "review", rubric_id, total,
                        {"dimensions": {"discipline": total,
                                        "professional": total},
                         "needs_review": len(needs_review)})
    return {
        "mode": mode, "items": items, "total": total,
        "needs_review": needs_review,
        "summary": _coach_summary(total),
        "rubric": {"id": rubric.get("id"), "name": rubric.get("name")},
    }


# ================================================================ ⑨ 周报

def weekly_submit(user: str, content: str, week_label: str = "") -> dict:
    """提交周报：复用作业批改引擎按 weekly-report 标准自动批改，存档并计入成长记录。
    批改结果只对周报本身负责——分数进入档案雷达（review 事件），但周报主要价值是
    让导师不用逐个追问"这周干了啥"。"""
    user = (user or "").strip() or "demo"
    content = (content or "").strip()
    if not content:
        return {"error": "周报内容不能为空"}
    if not week_label.strip():
        iso = datetime.now().isocalendar()
        week_label = f"{iso[0]} 年第 {iso[1]} 周"
    week_label = week_label.strip()[:40]

    rv = review("weekly-report", content, user, title=week_label)
    if rv.get("error"):                       # rubric 缺失时不拦着提交，保底存档
        rv = {}
    score = rv.get("total")
    mode = rv.get("mode", "none")
    wid = STORE.add_weekly(user, week_label, content, score, mode, rv)
    return {"ok": True, "id": wid, "week_label": week_label,
            "score": score, "mode": mode, "review": rv}


def weekly_list(user: str) -> dict:
    """我的周报历史。"""
    rows = STORE.weeklies(user)
    return {"mine": [{"id": r["id"], "week_label": r["week_label"],
                      "score": r["score"], "mode": r["mode"],
                      "content": r["content"], "review": r["review"],
                      "created_at": r["created_at"]} for r in rows]}


def weekly_all(limit: int = 100) -> dict:
    """全员周报（导师视角，双向可见原则）。"""
    rows = STORE.weeklies(None, limit)
    names = {u["id"]: u["name"] for u in STORE.users()}
    return {"all": [{"id": r["id"], "user": r["user"],
                     "user_name": names.get(r["user"], r["user"]),
                     "week_label": r["week_label"], "score": r["score"],
                     "mode": r["mode"], "content": r["content"],
                     "review": r["review"], "created_at": r["created_at"]}
                    for r in rows]}


# ================================================================ ⑩ 任务中心

MAX_UPLOAD_BYTES = 200 * 1024 * 1024      # 单请求附件总量上限 200MB（局域网工具够用）


def _save_uploads(kind: str, owner_id: int, files: list) -> None:
    """files: [{filename, content(bytes), ctype}]。磁盘只存 uuid 文件名，原始文件名进库。"""
    if not files:
        return
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    for f in files:
        content = f.get("content") or b""
        filename = (f.get("filename") or "attachment").strip() or "attachment"
        if not content:
            continue
        ext = re.sub(r"[^.\w]", "", os.path.splitext(filename)[1])[:10]
        stored = uuid.uuid4().hex + ext
        (FILES_DIR / stored).write_bytes(content)
        mime = f.get("ctype") or (mimetypes.guess_type(filename)[0] or "application/octet-stream")
        STORE.add_upload(kind, owner_id, filename, stored, len(content), mime)


def _upload_view(rows: list) -> list:
    return [{"id": r["id"], "filename": r["filename"], "size": r["size"],
             "mime": r["mime"]} for r in rows]


def task_create(title: str, description: str, creator: str,
                assignee: str = "", items=None, files=None) -> dict:
    """导师发布任务：按需求拆成多个子项（问题回答 / 需求 / 其他），可带资料附件。"""
    title = (title or "").strip()
    if not title:
        return {"error": "任务标题不能为空"}
    files = files or []
    total = sum(len(f.get("content") or b"") for f in files)
    if total > MAX_UPLOAD_BYTES:
        return {"error": "附件总量超过 200MB 上限"}
    creator = (creator or "").strip() or "demo"
    assignee = (assignee or "").strip()
    tid = STORE.add_task(title, (description or "").strip(), creator, assignee)
    n_items = 0
    for it in (items or []):
        it = it or {}
        t = str(it.get("title") or "").strip()
        if not t:
            continue
        itype = str(it.get("itype") or "general")
        if itype not in ITEM_TYPES:
            itype = "general"
        STORE.add_task_item(tid, t[:120], itype, str(it.get("content") or "").strip())
        n_items += 1
    _save_uploads("task", tid, files)
    STORE.add_event(creator, "task", f"task:{tid}", None,
                    {"title": title, "assignee": assignee, "files": len(files),
                     "items": n_items})
    return {"ok": True, "id": tid}


def tasks_list(user: str = "") -> dict:
    """任务列表：任务 + 资料附件 + 子项（每个子项带附件、反馈、当前用户完成状态）。"""
    user = (user or "").strip()
    names = {u["id"]: u["name"] for u in STORE.users()}
    out = []
    for t in STORE.tasks_all():
        items = []
        done_count = 0
        for it in STORE.task_items(t["id"]):
            fbs = STORE.feedbacks_for_item(it["id"])
            done_users = [f["user"] for f in fbs]
            my_done = user in done_users
            if my_done:
                done_count += 1
            items.append({
                "id": it["id"], "title": it["title"], "itype": it["itype"],
                "content": it["content"],
                "files": _upload_view(STORE.uploads_for("item", it["id"])),
                "feedbacks": [{"id": f["id"], "user": f["user"],
                               "user_name": names.get(f["user"], f["user"]),
                               "content": f["content"], "status": f["status"],
                               "note": f["note"], "created_at": f["created_at"],
                               "files": _upload_view(STORE.uploads_for("feedback", f["id"]))}
                              for f in fbs],
                "done_users": done_users,
                "my_done": my_done,
            })
        total_items = len(items)
        out.append({
            "id": t["id"], "title": t["title"], "description": t["description"],
            "creator": t["creator"], "creator_name": names.get(t["creator"], t["creator"]),
            "assignee": t["assignee"],
            "assignee_name": names.get(t["assignee"], "") if t["assignee"] else "",
            "created_at": t["created_at"],
            "files": _upload_view(STORE.uploads_for("task", t["id"])),
            "items": items,
            "items_total": total_items,
            "items_my_done": done_count,
            "my_done": total_items == 0 or done_count == total_items,
        })
    return {"tasks": out}


def task_feedback(task_id: int, item_id: int, user: str, content: str, files=None) -> dict:
    """新人按子项反馈：每个子项单独提交文字与附件，导师逐项确认。"""
    t = STORE.get_task(int(task_id or 0))
    if not t:
        return {"error": "任务不存在"}
    it = STORE.get_task_item(int(item_id or 0))
    if not it or it["task_id"] != t["id"]:
        return {"error": "子项不存在"}
    user = (user or "").strip() or "demo"
    content = (content or "").strip()
    files = files or []
    if not content and not files:
        return {"error": "反馈内容不能为空（文字或附件至少有一项）"}
    total = sum(len(f.get("content") or b"") for f in files)
    if total > MAX_UPLOAD_BYTES:
        return {"error": "附件总量超过 200MB 上限"}
    fid = STORE.add_feedback(int(task_id), int(item_id), user, content)
    _save_uploads("feedback", fid, files)
    # 「问题」类子项的反馈本身就是写给导师的问题：自动进导师的待回复队列，
    # 导师答复后经 ref 回流到这条反馈的点评里，新人无需跳去问答页。
    if it.get("itype") == "question":
        STORE.add_escalation(
            user,
            f"【任务提问】{t['title']} / {it['title']}：{content or '（见附件）'}",
            "任务子项提问", ref=f"feedback:{fid}")
    STORE.add_event(user, "task_feedback",
                    f"task:{int(task_id)}/item:{int(item_id)}", None, {})
    return {"ok": True, "id": fid}


def task_item_delete(item_id: int) -> dict:
    it = STORE.get_task_item(int(item_id or 0))
    if not it:
        return {"error": "子项不存在"}
    STORE.delete_task_item(int(item_id))
    return {"ok": True}


def ask_escalate(user: str, question: str, note: str = "", ref: str = "") -> dict:
    """新人在问答页主动联系导师：问题进入导师看板待回复，同时进入沉淀候选池。
    任务子项的提问也走这里（ref 指回反馈，导师回复直接回到任务卡片）。"""
    user = (user or "").strip() or "demo"
    question = (question or "").strip()
    if not question:
        return {"error": "问题不能为空"}
    note = (note or "").strip()[:300]
    reason = "新人主动求助" + ("：" + note if note else "")
    STORE.add_escalation(user, question, reason, ref)
    _record_candidate(question)
    STORE.add_event(user, "ask", "", None, {"escalated": True, "manual": True})
    return {"ok": True}


def my_escalations(user: str) -> dict:
    """我的求助记录（含导师回复）。提问不参与能力打分，这里只做状态可见。"""
    rows = STORE.my_escalations((user or "").strip())
    return {"items": [{"id": r["id"], "question": r["question"], "reason": r["reason"],
                       "answer": r.get("answer") or "", "status": r["status"],
                       "created_at": r["created_at"]} for r in rows]}


def escalation_answer(eid: int, answer: str) -> dict:
    """导师回复求助：答案写回后，新人在问答页「我的求助」里能看到。"""
    answer = (answer or "").strip()
    if not answer:
        return {"error": "回复内容不能为空"}
    n = STORE.answer_escalation(int(eid or 0), answer[:1000])
    if not n:
        return {"error": "求助不存在"}
    return {"ok": True}


ITEM_TYPES = ("question", "requirement", "general")


TEXT_EXTS = {".txt", ".md", ".py", ".java", ".js", ".ts", ".json", ".xml", ".yml", ".yaml",
             ".html", ".css", ".sql", ".gradle", ".cfg", ".ini", ".toml", ".c", ".cpp", ".h",
             ".go", ".rs", ".sh", ".bat", ".properties"}
DEP_FILES = {"requirements.txt", "package.json", "pom.xml", "build.gradle", "go.mod",
             "environment.yml", "setup.py"}


def projects_list() -> dict:
    return {"projects": load_projects()}


def project_import(name: str, description: str, files=None) -> dict:
    """导师导入项目（代码/文档）→ AI 自动生成该项目专属知识库。
    知识条目带 project 归属，与通用知识库、其他项目知识库互相独立。"""
    name = (name or "").strip()
    if not name:
        return {"error": "项目名称不能为空"}
    files = [f for f in (files or []) if (f.get("content") or b"")]
    if not files:
        return {"error": "请至少上传一个项目文件"}
    projects = load_projects()
    pid = "p" + uuid.uuid4().hex[:8]
    proj = {"id": pid, "name": name[:80], "description": (description or "").strip()[:300],
            "created_at": today_str()}
    projects.insert(0, proj)
    save_projects(projects)

    pdir = FILES_DIR / "projects" / pid
    pdir.mkdir(parents=True, exist_ok=True)
    tree, excerpts, dep_texts = [], [], []
    for f in files:
        content = f.get("content") or b""
        filename = (f.get("filename") or "file").strip() or "file"
        safe = re.sub(r"[^\w.\-]", "_", filename)[:80]
        stored = uuid.uuid4().hex[:8] + "_" + safe
        (pdir / stored).write_bytes(content)
        ext = os.path.splitext(filename)[1].lower()
        tree.append(f"{filename}（{len(content)} 字节）")
        if ext in TEXT_EXTS and len(excerpts) < 30:
            excerpts.append(f"=== {filename} ===\n" + content.decode("utf-8", "replace")[:3000])
        if os.path.basename(filename).lower() in DEP_FILES:
            dep_texts.append(f"=== {filename} ===\n" + content.decode("utf-8", "replace")[:2000])

    generated = []
    if LLM_CLIENT.enabled:
        context = (f"项目名称：{name}\n项目说明：{description or '（无）'}\n\n文件清单：\n"
                   + "\n".join(tree[:40]) + "\n\n内容摘录：\n" + "\n\n".join(excerpts[:20])[:9000])
        out = LLM_CLIENT.chat([
            {"role": "system", "content":
                "你是资深工程师兼新人导师。依据提供的项目文件清单与摘录，为加入该项目的新人生成知识库条目。"
                "只依据资料本身；资料里没有的写『待确认』，绝不编造。\n"
                '只输出 JSON 数组：[{"title":"条目标题","tags":["标签"],"body":"markdown 正文"}]\n'
                "生成 4-8 条，覆盖：项目概述、需要的软件环境与依赖（从依赖清单推断，没有就列待确认项）、"
                "目录/模块结构、关键流程、新人上手注意事项。"},
            {"role": "user", "content": context[:12000]},
        ], temperature=0.2, max_tokens=2000)
        entries = _parse_json_array(out) if out else None
        if entries:
            for e in entries:
                if not isinstance(e, dict) or not str(e.get("title") or "").strip():
                    continue
                generated.append({"title": str(e.get("title"))[:100],
                                  "tags": [str(x) for x in (e.get("tags") or [])][:6],
                                  "body": str(e.get("body") or "")})
    if not generated:
        # 未接模型：基于文件清单与依赖线索的保底生成（可解释、可编辑）
        body = "## 文件清单\n" + "\n".join("- " + t for t in tree[:40])
        if dep_texts:
            body += "\n\n## 依赖与环境线索\n" + "\n\n".join(dep_texts[:3])
        else:
            body += "\n\n## 依赖与环境\n未检测到依赖清单文件（requirements.txt / package.json / pom.xml 等），请导师补充。"
        generated = [
            {"title": f"{name}：项目资料概览", "tags": [name, "项目"], "body": body},
            {"title": f"{name}：上手指南（模板）", "tags": [name, "上手"],
             "body": f"## 项目说明\n{description or '（待导师补充）'}\n\n## 新人第一步\n1. 阅读上方资料清单\n2. 按依赖线索搭环境\n3. 有问题在问答页联系导师"},
        ]
    kdir = DATA_DIR / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    kb_ids = []
    for i, e in enumerate(generated, 1):
        kid = f"{pid}-kb-{i:02d}"
        fname = re.sub(r"[^\w\-]", "_", f"{kid}-{e['title']}")[:60] + ".md"
        front = (f"---\nid: {kid}\ntitle: {e['title']}\nproject: {pid}\n"
                 f"tags: {', '.join(e['tags']) or name}\nowner: 导师\n"
                 f"review_date: {today_str()}\nsource: 项目导入 AI 生成\n---\n\n")
        (kdir / fname).write_text(front + e["body"] + "\n", encoding="utf-8")
        kb_ids.append(kid)
    KB.load()
    return {"ok": True, "project": proj, "generated": len(generated),
            "kb_ids": kb_ids, "mode": "llm" if LLM_CLIENT.enabled else "template"}


def project_delete(pid: str) -> dict:
    """删除项目：连同 AI 生成的该项目知识条目与上传的源文件。"""
    pid = (pid or "").strip()
    projects = load_projects()
    kept = [p for p in projects if p.get("id") != pid]
    if len(kept) == len(projects):
        return {"error": "项目不存在"}
    save_projects(kept)
    removed = 0
    kdir = DATA_DIR / "knowledge"
    if kdir.is_dir():
        for p in kdir.glob(pid + "-kb-*.md"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    pdir = FILES_DIR / "projects" / pid
    if pdir.is_dir():
        for p in pdir.iterdir():
            try:
                p.unlink()
            except OSError:
                pass
        try:
            pdir.rmdir()
        except OSError:
            pass
    KB.load()
    return {"ok": True, "removed_kb": removed}


def tasks_ai_draft(description: str, project: str = "") -> dict:
    """AI 起草任务：导师给需求描述，生成标题/说明/子项草稿，导师修改后再发布。"""
    description = (description or "").strip()
    if not description:
        return {"error": "请先描述这个任务的需求"}
    if LLM_CLIENT.enabled:
        out = LLM_CLIENT.chat([
            {"role": "system", "content":
                "你是新人导师的助手。根据导师写下的需求描述，起草一个发给新人的任务。\n"
                '只输出 JSON 对象：{"title":"任务标题(20字内)","description":"任务说明(背景/要求/验收，markdown，150字内)",'
                '"items":[{"itype":"question|requirement|general","title":"子项标题","content":"子项说明"}]}\n'
                "拆 2-5 个子项：需要新人回答的用 question，需要阅读确认的资料用 requirement，其他用 general。"},
            {"role": "user", "content": f"需求描述：\n{description[:2000]}"
             + (f"\n\n关联项目：{project}" if project else "")},
        ], temperature=0.3, max_tokens=800)
        parsed = None
        if out:
            t = out.strip()
            if t.startswith("```"):
                t = re_sub_fence(t)
            s2, e2 = t.find("{"), t.rfind("}")
            if s2 != -1 and e2 != -1:
                try:
                    parsed = json.loads(t[s2:e2 + 1])
                except ValueError:
                    parsed = None
        if isinstance(parsed, dict) and str(parsed.get("title") or "").strip():
            items = []
            for it in parsed.get("items") or []:
                if not isinstance(it, dict) or not str(it.get("title") or "").strip():
                    continue
                itype = str(it.get("itype") or "general")
                items.append({"itype": itype if itype in ITEM_TYPES else "general",
                              "title": str(it.get("title"))[:120],
                              "content": str(it.get("content") or "")[:500]})
            return {"ok": True, "mode": "llm",
                    "draft": {"title": str(parsed.get("title"))[:80],
                              "description": str(parsed.get("description") or ""),
                              "items": items}}
    # 未接模型：按行拆分的保底草稿（第一行做标题，其余行做子项）
    lines = [l.strip("-• ").strip() for l in description.splitlines() if l.strip()]
    title = lines[0][:60] if lines else "新任务"
    items = [{"itype": "general", "title": (l[:60] or f"子项 {i}"), "content": l}
             for i, l in enumerate(lines[1:4], 1)]
    return {"ok": True, "mode": "template",
            "draft": {"title": title, "description": description, "items": items}}


def weekly_export_md() -> tuple:
    """把收到的全部周报导出为一个 Markdown 文件（Word/WPS 可直接打开）。"""
    names = {u["id"]: u["name"] for u in STORE.users()}
    rows = STORE.weeklies(None, 500)
    by_user = {}
    for r in rows:
        by_user.setdefault(r["user"], []).append(r)
    out = ["# 新人周报导出", "", f"- 导出时间：{today_str()}", f"- 周报总数：{len(rows)}", ""]
    for uid, rs in by_user.items():
        out.append(f"## {names.get(uid, uid)}（{uid}）")
        out.append("")
        for r in rs:
            score = f"{r['score']} 分" if r.get("score") is not None else "未评分"
            out.append(f"### {r['week_label']}（{r['created_at'][:10]} · {score}）")
            out.append("")
            out.append(r["content"].strip())
            out.append("")
    fname = "周报导出_" + today_str() + ".md"
    return (fname, "\n".join(out))


def feedback_confirm(feedback_id: int, note: str = "") -> dict:
    """导师确认反馈：submitted → confirmed，可附一句点评。"""
    n = STORE.confirm_feedback(int(feedback_id or 0), (note or "").strip()[:500])
    if not n:
        return {"error": "反馈不存在"}
    return {"ok": True}


def task_delete(task_id: int) -> dict:
    t = STORE.get_task(int(task_id or 0))
    if not t:
        return {"error": "任务不存在"}
    STORE.delete_task(int(task_id))
    return {"ok": True}


# ================================================================ ① 环境自检

def env_check_sets() -> list:
    return [{"id": c.get("id"), "name": c.get("name"),
             "count": len(c.get("checks") or [])}
            for c in load_dir_json("env_checks")]


def _run_one(c: dict) -> dict:
    t = c.get("type")
    base = {"id": c.get("id"), "name": c.get("name"), "type": t,
            "required": bool(c.get("required", False)),
            "fix": c.get("fix", ""), "detail": ""}
    try:
        if t == "command":
            cmd = c.get("cmd", "")
            shell = os.environ.get("COMSPEC") if os.name == "nt" else "/bin/sh"
            if os.name == "nt":
                proc = subprocess.run(["cmd", "/c", cmd], capture_output=True,
                                      text=True, timeout=20, errors="ignore")
            else:
                proc = subprocess.run([shell, "-c", cmd], capture_output=True,
                                      text=True, timeout=20, errors="ignore")
            out = (proc.stdout or "") + (proc.stderr or "")
            expect = c.get("expect", "")
            ok = (proc.returncode == 0) and (expect.lower() in out.lower() if expect else True)
            base.update(ok=ok, detail=(out.strip().splitlines() or [""])[0][:160])
        elif t == "path":
            p = Path_expand(c.get("path", ""))
            ok = p.exists()
            base.update(ok=ok, detail=f"{p} {'存在' if ok else '不存在'}")
        elif t == "env":
            var = c.get("var", "")
            val = os.environ.get(var, "")
            ok = bool(val) if not c.get("expect") else (val == c.get("expect"))
            base.update(ok=ok, detail=f"{var} = {val or '(未配置)'}")
        elif t == "tcp":
            host, port = c.get("host", "127.0.0.1"), int(c.get("port", 80))
            try:
                with socket.create_connection((host, port), timeout=float(c.get("timeout", 3))):
                    ok, detail = True, f"{host}:{port} 可达"
            except OSError as exc:
                ok, detail = False, f"{host}:{port} 不可达（{exc.__class__.__name__}）"
            base.update(ok=ok, detail=detail)
        else:
            base.update(ok=False, detail=f"未知检查类型 {t}")
    except subprocess.TimeoutExpired:
        base.update(ok=False, detail="命令超时（20s）")
    except Exception as exc:                      # noqa: BLE001 - 自检必须永不崩溃
        base.update(ok=False, detail=f"检查异常：{exc}")
    return base


def Path_expand(p: str):
    from pathlib import Path as _P
    return _P(os.path.expandvars(os.path.expanduser(p)))


def env_check(check_id: str, user: str = "") -> dict:
    cfg = None
    for c in load_dir_json("env_checks"):
        if c.get("id") == check_id:
            cfg = c
            break
    if not cfg:
        return {"error": "自检项不存在"}
    results = [_run_one(c) for c in (cfg.get("checks") or [])]
    passed = len([r for r in results if r.get("ok")])
    blocked = [r for r in results if not r.get("ok") and r.get("required")]
    out = {
        "id": cfg.get("id"), "name": cfg.get("name"), "results": results,
        "passed": passed, "total": len(results),
        "percent": round(passed / len(results) * 100) if results else 0,
        "blocked": len(blocked),
        "verdict": ("环境就绪，可以开工" if not blocked else
                    f"有 {len(blocked)} 项必检未通过，先按修复步骤处理"),
    }
    if user:
        STORE.add_event(user, "envcheck", check_id, out["percent"],
                        {"dimensions": {"discipline": out["percent"]}})
    return out


# ================================================================ ⑥ 成长档案

def profile(user: str) -> dict:
    dims_cfg = dimensions()
    scores = STORE.dimension_scores(user)
    radar = []
    for d in dims_cfg:
        v = scores.get(d["id"])
        radar.append({
            "id": d["id"], "name": d["name"],
            "score": round(v, 1) if v is not None else None,
            "baseline": d.get("baseline", 70),
            "anchors": d.get("anchors", {}),
            "gap": (round(v - d.get("baseline", 70), 1) if v is not None else None),
        })

    user_row = next((u for u in STORE.users() if u["id"] == user), None)
    track = (user_row or {}).get("track", "general")
    path = path_with_progress(track=track, user=user)

    events = STORE.events(user, limit=20)
    asked = [q for q in STORE.questions(400) if q["user"] == user]
    days = sorted({str(e["created_at"])[:10] for e in events}, reverse=True)
    streak = 0
    if days:
        from datetime import date, timedelta
        d0 = date.fromisoformat(days[0])
        if (date.today() - d0).days <= 1:
            streak = 1
            for i in range(1, len(days)):
                if date.fromisoformat(days[i]) == d0 - timedelta(days=streak):
                    streak += 1
                else:
                    break

    return {
        "user": user_row or {"id": user, "name": user, "track": track},
        "radar": radar,
        "path": {"id": path.get("id"), "name": path.get("name"),
                 "summary": path.get("summary"), "stages": path.get("stages")},
        "stats": {
            "asked": len(asked),
            "escalated": len([q for q in asked if q["escalated"]]),
            "coach_sessions": len(STORE.sessions(user)),
            "streak_days": streak,
        },
        "recent": events,
    }


# ================================================================ ⑦ 导师看板

STUCK_DAYS = 5


def dashboard() -> dict:
    """导师看板（精简版）：只关注三件事——回复新人问题、收新人消息、收周报。
    进度表/沉淀池/FAQ 草稿分别在新人档案、知识库页查看，看板不再堆砌。"""
    names = {u["id"]: u["name"] for u in STORE.users()}
    escalations = [{"id": e["id"], "user": e["user"],
                    "user_name": names.get(e["user"], e["user"]),
                    "question": e["question"], "reason": e["reason"],
                    "created_at": e["created_at"]}
                   for e in STORE.escalations("open")]
    messages = []
    for t in STORE.tasks_all():
        for it in STORE.task_items(t["id"]):
            for f in STORE.feedbacks_for_item(it["id"]):
                messages.append({
                    "id": f["id"], "task_id": t["id"], "task_title": t["title"],
                    "item_title": it["title"], "user": f["user"],
                    "user_name": names.get(f["user"], f["user"]),
                    "content": f["content"], "status": f["status"],
                    "note": f["note"], "created_at": f["created_at"]})
    messages.sort(key=lambda x: x["created_at"], reverse=True)
    messages = messages[:20]
    weeklies = [{"user": r["user"], "user_name": names.get(r["user"], r["user"]),
                 "week_label": r["week_label"], "score": r.get("score"),
                 "created_at": r["created_at"], "content": r["content"]}
                for r in STORE.weeklies(None, 50)]
    return {
        "escalations": escalations,
        "messages": messages,
        "weeklies": weeklies,
        "counters": {"pending_questions": len(escalations),
                     "messages": len(messages), "weeklies": len(weeklies)},
    }


def faq_drafts() -> dict:
    trigger = int(SETTINGS["retrieval"].get("draft_trigger_count", 2))
    drafts = [i for i in KB.faq["items"] if i.get("status") == "draft"]
    return {
        "drafts": drafts,
        "candidates": STORE.candidates("open"),
        "ready": [c for c in STORE.candidates("open") if c["count"] >= trigger],
        "trigger": trigger,
    }


def faq_make_draft(candidate_id: int) -> dict:
    cand = next((c for c in STORE.all_candidates() if c["id"] == candidate_id), None)
    if not cand:
        return {"error": "候选不存在"}
    q = cand["sample_question"]

    answer = None
    if LLM_CLIENT.enabled:
        hits = KB.search(q, top_k=3)
        ctx = _build_context(hits) if hits else ""
        answer = LLM_CLIENT.chat([
            {"role": "system", "content":
             "你是公司内部知识库编辑。根据提供的参考资料，为这个问题起草一条 FAQ 答案。\n"
             "要求：只依据参考资料；资料不足就写『待补充：』并列出需要导师补充的信息点；"
             "答案控制在 150 字内，面向新员工，口语化。直接输出答案正文。"},
            {"role": "user", "content": f"问题：{q}\n\n【参考资料】\n{ctx}"},
        ], temperature=0.2, max_tokens=500)

    if not answer:
        answer = ("待补充：知识库里还没有这条内容。\n\n"
                  f"问题原文：{q}\n"
                  "请导师补充标准答案后发布；发布后同类问题将自动命中，不再打扰导师。")

    item = {
        "id": KB.next_faq_id(),
        "q": q,
        "a": answer,
        "track": ["general"],
        "tags": ["自动沉淀"],
        "owner": SETTINGS["mentor"].get("escalate_contact", "导师"),
        "review_date": today_str(),
        "source": "新人提问自动沉淀",
        "status": "draft",
        "created_by": "auto-sediment",
        "from_candidate": candidate_id,
        "asked_count": cand["count"],
    }
    KB.faq["items"].append(item)
    save_json(DATA_DIR / "faq.json", KB.faq)
    KB.load()
    STORE.link_candidate_draft(candidate_id, item["id"])
    return {"draft": item}


def faq_publish(payload: dict) -> dict:
    fid = payload.get("id")
    for it in KB.faq["items"]:
        if it.get("id") == fid:
            # 守卫：占位答案一旦发布会进入检索池，同类问题会被自动回复这条
            # 「待补充」占位文案——零误答承诺就是这么被悄悄破坏的。
            new_a = str(payload.get("a") or it.get("a") or "").strip()
            if new_a.startswith("待补充"):
                return {"error": "答案还是『待补充』占位内容，发布后会被自动回复给新人。"
                                 "请先补上真实答案再发布。"}
            for k in ("q", "a", "owner", "source", "review_date"):
                if payload.get(k):
                    it[k] = payload[k]
            it["status"] = "published"
            it["tags"] = _as_list(payload.get("tags")) or it.get("tags") or []
            it["track"] = _as_list(payload.get("track")) or it.get("track") or ["general"]
            save_json(DATA_DIR / "faq.json", KB.faq)
            KB.load()
            return {"faq": it}
    return {"error": "草稿不存在"}


def faq_delete(fid: str) -> dict:
    before = len(KB.faq["items"])
    KB.faq["items"] = [i for i in KB.faq["items"] if i.get("id") != fid]
    if len(KB.faq["items"]) == before:
        return {"error": "条目不存在"}
    save_json(DATA_DIR / "faq.json", KB.faq)
    KB.load()
    return {"deleted": fid}
