# -*- coding: utf-8 -*-
"""引路人 · 业务层
八个模块：环境自检 / 知识问答 / 关卡闯关 / 陪练室 / 作业批改 / 成长档案 / 导师看板 / FAQ 沉淀

所有模块都遵循同一原则：
  接入大模型时用模型生成，未接入时**降级为本地可用模式**，绝不让功能变成不可用。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import uuid
from datetime import datetime

from core import (
    DATA_DIR, KB, LLM_CLIENT, SETTINGS, STORE, load_dir_json, load_json,
    save_json, sensitive_hit, is_overdue, tokenize, today_str, validate_data, _as_list,
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
        "dimensions": dimensions(),
        "users": users,
        "llm": LLM_CLIENT.status,
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
                          u.get("cohort", ""), u.get("joined_at", ""))


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


def ask(user: str, question: str, anonymous: bool = False, track: str | None = None) -> dict:
    question = (question or "").strip()
    if not question:
        return {"error": "问题不能为空"}

    rcfg = SETTINGS["retrieval"]
    top_k = int(rcfg.get("top_k", 4))
    match_th = float(rcfg.get("match_threshold", 0.9))
    strong_th = float(rcfg.get("strong_threshold", 1.6))
    min_cov = float(rcfg.get("min_coverage", 0.30))

    hits = KB.search(question, top_k=top_k)
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

    # 命中已发布 FAQ → 计数
    if hits[0]["kind"] == "faq":
        KB.bump_faq_hit(hits[0]["id"])

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
            # 不下发答案
            return [{k: v for k, v in q.items() if k != "answer"}
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
        scored = _llm_score_items(rubric, user_text, "陪练对话")
        mode = "llm"
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


def _llm_score_items(rubric: dict, text: str, kind: str) -> list:
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
        return _heuristic_score_items(items, text)
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
    return result


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
        items = _llm_score_items(rubric, text, f"作业批改：{title or rubric.get('name')}")
        mode = "llm"
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
    users = STORE.users()
    all_prog = STORE.all_progress()
    now = datetime.now()

    rows = []
    for u in users:
        mine = [p for p in all_prog if p["user"] == u["id"]]
        done = len([p for p in mine if p["status"] == "passed"])
        path = path_with_progress(track=u.get("track") or "general", user=u["id"])
        total = path.get("summary", {}).get("total") or 0
        last = max([p["updated_at"] for p in mine], default="")
        idle_days = None
        if last:
            try:
                idle_days = (now - datetime.fromisoformat(last)).days
            except ValueError:
                idle_days = None
        stuck = bool(idle_days is not None and idle_days >= STUCK_DAYS and done < total)
        escal = [e for e in STORE.escalations() if e["user"] == u["id"]]
        rows.append({
            "id": u["id"], "name": u["name"], "track": u.get("track"),
            "passed": done, "total": total,
            "percent": round(done / total * 100) if total else 0,
            "idle_days": idle_days, "stuck": stuck,
            "open_escalations": len(escal),
            "dimensions": STORE.dimension_scores(u["id"]),
        })

    cands = STORE.candidates("open")
    trigger = int(SETTINGS["retrieval"].get("draft_trigger_count", 2))
    drafts = [i for i in KB.faq["items"] if i.get("status") == "draft"]
    unanswerable = [q for q in STORE.questions(200) if q["escalated"]]

    return {
        "users": rows,
        "alerts": {
            "stuck": [r["name"] for r in rows if r["stuck"]],
            "escalations": STORE.escalations()[:12],
            "ready_to_draft": [c for c in cands if c["count"] >= trigger],
            "candidates": cands[:15],
            "overdue_knowledge": KB.overdue()[:15],
            "pending_drafts": drafts,
            "sessions_need_review": [
                s for s in STORE.sessions(limit=15) if s["status"] == "finished"][:8],
        },
        "counters": {
            "newcomers": len(users),
            "open_escalations": len(STORE.escalations()),
            "drafts": len(drafts),
            "candidates": len(cands),
            "overdue": len(KB.overdue()),
            "questions_total": len(STORE.questions(1000)),
        },
        "recent_questions": [
            {"user": ("匿名" if q["anonymous"] else q["user"]), "question": q["question"],
             "escalated": bool(q["escalated"]), "score": q["top_score"],
             "created_at": q["created_at"]}
            for q in STORE.questions(25)],
    }


# ================================================================ ⑧ FAQ 沉淀

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
        "hits": 0,
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
