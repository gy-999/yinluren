/* 引路人 · AI 导师 —— 前端（原生 JS，无构建） */
'use strict';

const state = {
  user: localStorage.getItem('mentor.user') || 'demo',
  meta: null,
  view: 'ask',
  chat: [],          // 问答记录
  coach: null,       // 当前陪练会话
  exam: null,        // 当前考试
};

const TITLES = {
  ask: '问答助手', path: '成长地图', coach: '陪练室', review: '作业批改',
  env: '环境自检', profile: '我的档案', dashboard: '导师看板', kb: '知识库', settings: '设置',
};

/* ---------------------------------------------------------------- 基础 */

async function api(path, opts) {
  const res = await fetch(path, opts);
  const text = await res.text();
  try { return JSON.parse(text); }
  catch (e) { return { error: '返回内容无法解析：' + text.slice(0, 200) }; }
}
const get = (p) => api(p);
const post = (p, body) => api(p, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
});

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function md(src) {
  if (!src) return '';
  let t = esc(src).replace(/\r\n/g, '\n');
  const blocks = [];
  t = t.replace(/```([\s\S]*?)```/g, (m, code) => {
    blocks.push('<pre><code>' + code.replace(/^\n/, '') + '</code></pre>');
    return '\u0000B' + (blocks.length - 1) + '\u0000';
  });
  const lines = t.split('\n');
  const out = [];
  let list = null;
  const closeList = () => { if (list) { out.push('</' + list + '>'); list = null; } };
  const inline = (s) => s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');

  for (const raw of lines) {
    const line = raw;
    let m;
    if ((m = line.match(/^\u0000B(\d+)\u0000$/))) { closeList(); out.push(blocks[+m[1]]); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      closeList(); const lv = Math.min(4, m[1].length + 2);
      out.push('<h' + lv + '>' + inline(m[2]) + '</h' + lv + '>'); continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul'; }
      out.push('<li>' + inline(line.replace(/^\s*[-*]\s+/, '')) + '</li>'); continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol'; }
      out.push('<li>' + inline(line.replace(/^\s*\d+[.)]\s+/, '')) + '</li>'); continue;
    }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) { closeList(); out.push('<hr>'); continue; }
    if (!line.trim()) { closeList(); continue; }
    closeList(); out.push('<p>' + inline(line) + '</p>');
  }
  closeList();
  return out.join('');
}

function toast(msg, ms) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), ms || 2400);
}

function scoreClass(v) { return v >= 80 ? 'ok' : v >= 60 ? 'warn' : 'bad'; }

/* 检索分数（约 0~3）换算成给人看的百分比 */
function relPct(v) { return Math.min(99, Math.round((v || 0) / 2.6 * 100)); }

function ring(score) {
  const c = score >= 80 ? '#0f9d58' : score >= 60 ? '#e0a33a' : '#d93025';
  const bg = score >= 80 ? '#e9f7ef' : score >= 60 ? '#fdf3e3' : '#fdecea';
  return `<div class="score-ring" style="background:${bg};color:${c}">${Math.round(score)}</div>`;
}

/* ---------------------------------------------------------------- 雷达图 */

function radarSvg(items) {
  const size = 260, cx = size / 2, cy = size / 2 + 4, R = 88;
  const n = items.length || 1;
  const pt = (i, r) => {
    const a = -Math.PI / 2 + i * 2 * Math.PI / n;
    return [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
  };
  let rings = '';
  for (const f of [0.25, 0.5, 0.75, 1]) {
    const pts = items.map((_, i) => pt(i, R * f).map(v => v.toFixed(1)).join(',')).join(' ');
    rings += `<polygon points="${pts}" fill="none" stroke="#e5e7eb" stroke-width="1"/>`;
  }
  let axes = '', labels = '';
  items.forEach((d, i) => {
    const [x, y] = pt(i, R);
    axes += `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#e5e7eb"/>`;
    const [lx, ly] = pt(i, R + 26);
    const anchor = Math.abs(lx - cx) < 8 ? 'middle' : (lx > cx ? 'start' : 'end');
    labels += `<text x="${lx.toFixed(1)}" y="${(ly + 4).toFixed(1)}" font-size="11.5" fill="#6b7280" text-anchor="${anchor}">${esc(d.name)}</text>`;
  });
  const base = items.map((d, i) => pt(i, R * Math.min(1, (d.baseline || 70) / 100)).map(v => v.toFixed(1)).join(',')).join(' ');
  const vals = items.map((d, i) => pt(i, R * Math.min(1, (d.score || 0) / 100)).map(v => v.toFixed(1)).join(',')).join(' ');
  const dots = items.map((d, i) => {
    const [x, y] = pt(i, R * Math.min(1, (d.score || 0) / 100));
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="#3d3af5"/>`;
  }).join('');
  return `<svg width="${size}" height="${size + 14}" viewBox="0 0 ${size} ${size + 14}">
    ${rings}${axes}
    <polygon points="${base}" fill="none" stroke="#c77700" stroke-width="1.5" stroke-dasharray="4 3"/>
    <polygon points="${vals}" fill="rgba(61,58,245,.18)" stroke="#3d3af5" stroke-width="2"/>
    ${dots}${labels}
  </svg>`;
}

/* ---------------------------------------------------------------- 视图：问答 */

function viewAsk() {
  const msgs = state.chat.map(m => {
    if (m.role === 'user') {
      return `<div class="msg me"><div class="avatar">我</div><div class="bubble">${esc(m.text)}</div></div>`;
    }
    let body = '';
    if (m.escalated) {
      body = `<div class="hint warn"><b>已转人工</b>：${esc(m.reason)}<br>请直接联系 ${esc(m.contact || '导师')}。
        这条问题已自动进入 FAQ 沉淀池，问的人多了就会变成知识条目。</div>`;
    } else {
      body = `<div class="md">${md(m.text)}</div>`;
      if (m.notice) body += `<div class="hint bad" style="margin-top:10px"><b>敏感操作提醒</b>：${esc(m.notice)}</div>`;
      if (m.caveat) body += `<div class="hint warn" style="margin-top:10px">${esc(m.caveat)}</div>`;
      if (m.sources && m.sources.length) {
        body += '<div class="sources"><div class="small muted" style="margin-bottom:5px">出处</div>' +
          m.sources.map(s => `<div class="source"><span class="tag ${s.kind === 'faq' ? 'accent' : ''}">${s.kind === 'faq' ? 'FAQ' : '知识'}</span>
            <span><b>${esc(s.title)}</b>　${esc(s.owner)}　${esc(s.source || '')}
            ${s.stale ? '<span class="tag warn">待复核</span>' : ''}</span></div>`).join('') + '</div>';
      }
      body += `<div class="small muted" style="margin-top:8px">相关度 ${relPct(m.confidence)}%　·　词覆盖 ${Math.round((m.coverage || 0) * 100)}%　·　${m.mode === 'llm' ? '模型生成（基于知识库）' : '知识库原文直出'}</div>`;
    }
    return `<div class="msg bot"><div class="avatar">引</div><div class="bubble">${body}</div></div>`;
  }).join('');

  return `<div class="chat stack">
    ${state.chat.length ? '' : `<div class="card"><div class="card-body">
      <div class="hint">问什么都可以：环境怎么搭、流程怎么走、这个需求该怎么理解、报销找谁……
        <b>答案一定附出处</b>；知识库里没有的，我不会编，会直接转给导师。</div>
      <div class="row" style="margin-top:12px">
        ${['开发环境怎么搭？', '需求评审的流程是什么？', '请假流程怎么走？', '代码提交规范有哪些？']
          .map(q => `<button class="btn sm" data-quick="${esc(q)}">${esc(q)}</button>`).join('')}
      </div></div></div>`}
    ${msgs}
    <div class="card"><div class="card-body">
      <div class="askbox">
        <textarea id="q" placeholder="输入你的问题…（Ctrl + Enter 发送）"></textarea>
        <button class="btn primary" id="send">发送</button>
      </div>
      <div class="row" style="margin-top:10px">
        <label class="small muted"><input type="checkbox" id="anon" ${state.anon ? 'checked' : ''}> 匿名提问（导师看板会显示"匿名"）</label>
      </div>
    </div></div>
  </div>`;
}

function bindAsk() {
  const ta = document.getElementById('q');
  const send = async () => {
    const q = ta.value.trim();
    if (!q) return;
    ta.value = '';
    state.chat.push({ role: 'user', text: q });
    render();
    const anon = document.getElementById('anon');
    state.anon = anon ? anon.checked : false;
    const res = await post('/api/ask', { user: state.user, question: q, anonymous: state.anon });
    state.chat.push({
      role: 'bot', text: res.answer || '', sources: res.sources || [],
      escalated: res.escalated, reason: res.reason, contact: res.contact,
      confidence: res.confidence || 0, mode: res.mode, caveat: res.caveat,
      notice: res.notice, coverage: res.coverage,
    });
    render();
    const v = document.getElementById('view');
    v.scrollTop = v.scrollHeight;
  };
  document.getElementById('send').onclick = send;
  ta.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
  });
  document.querySelectorAll('[data-quick]').forEach(b => {
    b.onclick = () => { ta.value = b.dataset.quick; send(); };
  });
  ta.focus();
}

/* ---------------------------------------------------------------- 视图：成长地图 */

async function viewPath() {
  const track = (state.meta.users.find(u => u.id === state.user) || {}).track || 'general';
  const data = await get(`/api/path?user=${encodeURIComponent(state.user)}&track=${encodeURIComponent(track)}`);
  if (data.error) return `<div class="card"><div class="card-body">${esc(data.error)}</div></div>`;

  const s = data.summary || {};
  const head = `<div class="card"><div class="card-body">
    <div class="spread">
      <div>
        <div style="font-weight:650;font-size:15px">${esc(data.name)}</div>
        <div class="muted small" style="margin-top:3px">${esc(data.description || '')}</div>
      </div>
      <div style="text-align:right;min-width:180px">
        <div class="small muted">已通关 ${s.passed} / ${s.total}</div>
        <div class="progress" style="margin-top:7px"><i style="width:${s.percent || 0}%"></i></div>
      </div>
    </div></div></div>`;

  const stages = (data.stages || []).map((st, i) => {
    const badge = st.status === 'passed' ? '<span class="tag ok">已通关</span>'
      : st.status === 'available' ? '<span class="tag accent">可以开始</span>'
        : '<span class="tag">未解锁</span>';
    const learn = (st.learn || []).map(l =>
      `<div class="source"><span class="tag">学</span><span>${esc(l.title)}${l.minutes ? `　约 ${l.minutes} 分钟` : ''}</span></div>`).join('');
    const prac = (st.practice || []).map(p =>
      `<div class="source"><span class="tag">练</span><span>${esc(p.title)}</span></div>`).join('');
    return `<div class="stage ${st.status}">
      <div class="stage-no">${st.status === 'passed' ? '✓' : i + 1}</div>
      <div class="stage-main">
        <div class="stage-title">${esc(st.name)} ${badge}
          <span class="small muted">${esc(st.window || '')}</span>
          ${st.best_score != null ? `<span class="small muted">最高分 ${st.best_score}</span>` : ''}
        </div>
        <div class="muted small" style="margin:6px 0 8px">${esc(st.goal || '')}</div>
        ${learn}${prac}
        <div class="row" style="margin-top:10px">
          ${st.status !== 'locked' && st.question_count
            ? `<button class="btn primary sm" data-exam="${esc(data.id)}|${esc(st.id)}">${st.status === 'passed' ? '再考一次' : '开始闯关考试'}（${st.question_count} 题）</button>`
            : ''}
          ${st.status === 'locked' ? '<span class="small muted">先通过前置关卡</span>' : ''}
        </div>
        <div id="exam-${esc(st.id)}"></div>
      </div></div>`;
  }).join('');

  return head + '<div style="margin-top:16px">' + stages + '</div>';
}

function bindPath() {
  document.querySelectorAll('[data-exam]').forEach(btn => {
    btn.onclick = async () => {
      const [pid, sid] = btn.dataset.exam.split('|');
      const data = await get(`/api/exam?path=${encodeURIComponent(pid)}&stage=${encodeURIComponent(sid)}`);
      const qs = data.questions || [];
      if (!qs.length) { toast('这一关还没有配置考题'); return; }
      const box = document.getElementById('exam-' + sid);
      box.innerHTML = `<div class="card" style="margin-top:14px"><div class="card-head">
          <h3>闯关考试</h3><span class="small muted">答完自动判分</span></div>
        <div class="card-body">
          ${qs.map((q, qi) => `<div class="q" data-qid="${esc(q.id)}">
            <div class="q-title"><span class="q-no">${qi + 1}.</span><span>${esc(q.q)}</span></div>
            ${(q.options || []).map((o, oi) =>
              `<div class="opt" data-opt="${oi}"><span>${String.fromCharCode(65 + oi)}.</span><span>${esc(o)}</span></div>`).join('')}
          </div>`).join('')}
          <div class="row" style="margin-top:16px">
            <button class="btn primary" id="submit-exam">提交答卷</button>
            <button class="btn" id="cancel-exam">取消</button>
          </div>
          <div id="exam-result" style="margin-top:14px"></div>
        </div></div>`;
      const picks = {};
      box.querySelectorAll('.opt').forEach(o => {
        o.onclick = () => {
          const q = o.closest('.q');
          q.querySelectorAll('.opt').forEach(x => x.classList.remove('sel'));
          o.classList.add('sel');
          picks[q.dataset.qid] = +o.dataset.opt;
        };
      });
      document.getElementById('cancel-exam').onclick = () => { box.innerHTML = ''; };
      document.getElementById('submit-exam').onclick = async () => {
        const res = await post('/api/exam', { user: state.user, path: pid, stage: sid, answers: picks });
        if (res.error) { toast(res.error); return; }
        const rbox = document.getElementById('exam-result');
        rbox.innerHTML = `<div class="row" style="align-items:center;gap:16px">
            ${ring(res.score)}
            <div><div style="font-weight:650">${res.passed ? '🎉 通关！' : '未达到 ' + res.pass_score + ' 分，再准备一下'}
              ${res.next ? `　下一关：${esc(res.next.name)}` : ''}</div>
              <div class="muted small">答对 ${res.correct} / ${res.total}　·　本关成绩已计入成长档案</div></div>
          </div>
          <div style="margin-top:12px">${res.details.map((d, i) => `<div class="q">
            <div class="q-title"><span class="q-no">${i + 1}.</span><span>${esc(d.q)}</span>
              <span class="tag ${d.ok ? 'ok' : 'bad'}">${d.ok ? '正确' : '错误'}</span></div>
            ${d.explain ? `<div class="hint" style="margin-top:6px">${md(d.explain)}</div>` : ''}
          </div>`).join('')}</div>`;
        toast(res.passed ? '通关成功' : '已记录本次成绩');
        setTimeout(() => render(), 2600);
      };
      box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    };
  });
}

/* ---------------------------------------------------------------- 视图：陪练室 */

async function viewCoach() {
  if (!state.coach) {
    const data = await get('/api/scenarios');
    const list = (data.scenarios || []).map(s => `<div class="card"><div class="card-body">
      <div class="spread">
        <div style="flex:1">
          <div style="font-weight:650">${esc(s.name)}
            <span class="tag ${s.track && s.track.includes('engineering') ? 'accent' : ''}">${(s.track || []).join(' / ')}</span>
            <span class="tag">难度 ${'★'.repeat(s.difficulty || 3)}</span></div>
          <div class="muted small" style="margin-top:6px">${esc(s.brief)}</div>
          <div class="small muted" style="margin-top:5px">对手角色：${esc(s.role)}</div>
        </div>
        <button class="btn primary" data-sc="${esc(s.id)}">开始陪练</button>
      </div></div></div>`).join('');
    return `<div class="hint">陪练室让你在**不占用导师时间**的情况下反复练手。结束后按评分标准逐维度打分，并给出改进建议。</div>
      <div class="stack" style="margin-top:14px">${list || '<div class="empty">还没有配置陪练场景（data/scenarios/*.json）</div>'}</div>`;
  }

  const c = state.coach;
  const bubbles = c.turns.map(t => `<div class="msg ${t.role === 'user' ? 'me' : 'bot'}">
    <div class="avatar">${t.role === 'user' ? '我' : '角'}</div>
    <div class="bubble"><div class="md">${md(t.content)}</div></div></div>`).join('');
  const result = c.result ? `<div class="card"><div class="card-head"><h3>评分反馈</h3>
      <span class="tag ${c.result.mode === 'llm' ? 'accent' : 'warn'}">${c.result.mode === 'llm' ? '模型评分' : '本地启发式评分，建议导师复核'}</span>
    </div><div class="card-body">
      <div class="row" style="gap:16px;align-items:center">${ring(c.result.total)}
        <div><div style="font-weight:650">${esc(c.result.summary)}</div></div></div>
      <div style="margin-top:14px">
        ${c.result.items.map(i => `<div style="margin-bottom:11px">
          <div class="spread"><b>${esc(i.name)}</b><span class="small muted">${i.score}/5　权重 ${i.weight}%</span></div>
          <div class="bar ${scoreClass(i.score / 5 * 100)}"><i style="width:${i.score / 5 * 100}%"></i></div>
          <div class="small muted" style="margin-top:4px">${esc(i.reason)}</div>
        </div>`).join('')}
      </div>
      ${c.result.advice && c.result.advice.length ? `<div style="margin-top:14px">
        <b>改进建议</b><div class="hint" style="margin-top:6px">${c.result.advice.map(a => '<div>' + md(a) + '</div>').join('')}</div></div>` : ''}
      ${c.result.model_answer ? `<div style="margin-top:14px"><b>导师会怎么答</b>
        <div class="hint ok" style="margin-top:6px">${md(c.result.model_answer)}</div></div>` : ''}
      <div class="row" style="margin-top:16px"><button class="btn" id="coach-back">换一个场景</button></div>
    </div></div>` : '';

  return `<div class="card">
      <div class="card-head"><h3>${esc(c.scenario.name)}</h3>
        <span class="small muted">回合 ${c.turns.filter(t => t.role === 'user').length} / ${c.scenario.max_turns || 6}</span></div>
      <div class="card-body">
        <div class="hint">${esc(c.scenario.brief)}</div>
        ${c.scenario.tips && c.scenario.tips.length ? `<div class="small muted" style="margin-top:8px">提示：${c.scenario.tips.map(esc).join('；')}</div>` : ''}
      </div></div>
    <div class="chat" style="margin-top:16px">${bubbles}</div>
    ${result || `<div class="card" style="margin-top:6px"><div class="card-body">
      <div class="askbox"><textarea id="coach-input" placeholder="你要说的话…（Ctrl + Enter 发送）"></textarea>
        <button class="btn primary" id="coach-send">说</button></div>
      <div class="row" style="margin-top:10px">
        <button class="btn sm" id="coach-finish">结束并评分</button>
        <button class="btn sm" id="coach-back">退出</button>
      </div></div></div>`}`;
}

function bindCoach() {
  document.querySelectorAll('[data-sc]').forEach(b => {
    b.onclick = async () => {
      toast('正在准备场景…');
      const res = await post('/api/coach/start', { user: state.user, scenario: b.dataset.sc });
      if (res.error) { toast(res.error); return; }
      state.coach = { session: res.session, turns: res.turns, scenario: res.scenario, result: null };
      render();
    };
  });
  const input = document.getElementById('coach-input');
  const send = async () => {
    const v = input.value.trim();
    if (!v) return;
    input.value = '';
    state.coach.turns.push({ role: 'user', content: v });
    render();
    const res = await post('/api/coach/reply', { session: state.coach.session, message: v });
    if (res.error) { toast(res.error); return; }
    state.coach.turns = res.turns;
    render();
    const el = document.getElementById('view'); el.scrollTop = el.scrollHeight;
  };
  if (input) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
    });
    document.getElementById('coach-send').onclick = send;
    input.focus();
  }
  const fin = document.getElementById('coach-finish');
  if (fin) fin.onclick = async () => {
    const res = await post('/api/coach/finish', { session: state.coach.session });
    if (res.error) { toast(res.error); return; }
    state.coach.result = res.result;
    render();
  };
  const back = document.getElementById('coach-back');
  if (back) back.onclick = () => { state.coach = null; render(); };
}

/* ---------------------------------------------------------------- 视图：作业批改 */

async function viewReview() {
  const data = await get('/api/rubrics');
  const rubrics = data.rubrics || [];
  const cur = state.reviewRubric || (rubrics[0] || {}).id || '';
  return `<div class="card"><div class="card-head"><h3>提交作业 / 代码，AI 先批一遍</h3>
      <span class="small muted">导师只需要看标红项</span></div>
    <div class="card-body">
      <div class="grid g2">
        <div>
          <label class="small muted">评分标准</label>
          <select class="input" id="rubric-sel">
            ${rubrics.map(r => `<option value="${esc(r.id)}" ${r.id === cur ? 'selected' : ''}>${esc(r.name)}</option>`).join('')}
          </select>
          <div id="rubric-detail" class="small muted" style="margin-top:10px"></div>
        </div>
        <div>
          <label class="small muted">标题（可选）</label>
          <input type="text" id="review-title" placeholder="例如：第一次需求分析作业">
        </div>
      </div>
      <div style="margin-top:14px">
        <label class="small muted">内容</label>
        <textarea id="review-text" placeholder="粘贴你的代码、方案、话术稿或作业正文…"></textarea>
      </div>
      <div class="row" style="margin-top:12px">
        <button class="btn primary" id="review-submit">提交批改</button>
        <span class="small muted">评分结果自动计入成长档案</span>
      </div>
      <div id="review-result" style="margin-top:16px"></div>
    </div></div>`;
  }

function bindReview() {
  const sel = document.getElementById('rubric-sel');
  const showDetail = async () => {
    state.reviewRubric = sel.value;
    const data = await get('/api/rubrics');
    const r = (data.rubrics || []).find(x => x.id === sel.value);
    document.getElementById('rubric-detail').innerHTML = r
      ? '评分维度：' + r.items.map(i => `${esc(i.name)}(${i.weight}%)`).join('、') : '';
  };
  sel.onchange = showDetail; showDetail();

  document.getElementById('review-submit').onclick = async () => {
    const text = document.getElementById('review-text').value.trim();
    if (!text) { toast('先粘贴要批改的内容'); return; }
    const box = document.getElementById('review-result');
    box.innerHTML = '<span class="spinner"></span> 正在批改…';
    const res = await post('/api/review', {
      rubric: sel.value, text, user: state.user,
      title: document.getElementById('review-title').value.trim(),
    });
    if (res.error) { box.innerHTML = esc(res.error); return; }
    box.innerHTML = `<div class="card"><div class="card-head">
        <h3>批改结果</h3>
        <span class="tag ${res.mode === 'llm' ? 'accent' : 'warn'}">${res.mode === 'llm' ? '模型评分' : '本地启发式评分'}</span>
      </div><div class="card-body">
        <div class="row" style="gap:16px;align-items:center">${ring(res.total)}
          <div><div style="font-weight:650">${esc(res.summary)}</div>
          ${res.needs_review.length ? `<div class="small" style="color:var(--warn)">${res.needs_review.length} 项 AI 拿不准，已标记给导师终审</div>` : ''}</div></div>
        <div style="margin-top:14px">${res.items.map(i => `<div style="margin-bottom:11px">
            <div class="spread"><b>${esc(i.name)}</b>
              <span>${i.needs_review ? '<span class="tag warn">待导师终审</span>' : ''}
              <span class="small muted">${i.score}/5　权重 ${i.weight}%</span></span></div>
            <div class="bar ${scoreClass(i.score / 5 * 100)}"><i style="width:${i.score / 5 * 100}%"></i></div>
            <div class="small muted" style="margin-top:4px">${esc(i.reason)}</div>
          </div>`).join('')}</div>
      </div></div>`;
  };
}

/* ---------------------------------------------------------------- 视图：环境自检 */

async function viewEnv() {
  const data = await get('/api/envcheck');
  const sets = data.sets || [];
  return `<div class="card"><div class="card-head"><h3>一键环境自检</h3>
      <span class="small muted">按检查项给出修复步骤，不用再来回问导师</span></div>
    <div class="card-body">
      <div class="row">
        <select class="input" id="env-sel" style="max-width:280px">
          ${sets.map(s => `<option value="${esc(s.id)}">${esc(s.name)}（${s.count} 项）</option>`).join('')}
        </select>
        <button class="btn primary" id="env-run">开始自检</button>
      </div>
      <div id="env-result" style="margin-top:16px"></div>
    </div></div>`;
}

function bindEnv() {
  document.getElementById('env-run').onclick = async () => {
    const box = document.getElementById('env-result');
    box.innerHTML = '<span class="spinner"></span> 正在逐项检查…';
    const id = document.getElementById('env-sel').value;
    const res = await get(`/api/envcheck?id=${encodeURIComponent(id)}&user=${encodeURIComponent(state.user)}`);
    if (res.error) { box.innerHTML = esc(res.error); return; }
    box.innerHTML = `<div class="row" style="gap:16px;align-items:center">${ring(res.percent)}
        <div><div style="font-weight:650">${esc(res.verdict)}</div>
        <div class="muted small">通过 ${res.passed} / ${res.total} 项</div></div></div>
      <table class="grid-t" style="margin-top:14px">
        <thead><tr><th style="width:44px"></th><th>检查项</th><th>结果</th><th>不通过时怎么办</th></tr></thead>
        <tbody>${res.results.map(r => `<tr>
          <td>${r.ok ? '<span class="tag ok">通过</span>' : '<span class="tag bad">失败</span>'}</td>
          <td><b>${esc(r.name)}</b>${r.required ? ' <span class="tag">必检</span>' : ''}
            <div class="small muted">${esc(r.detail || '')}</div></td>
          <td class="small muted">${esc(r.type)}</td>
          <td class="small ${r.ok ? 'muted' : ''}" style="${r.ok ? '' : 'color:#a5241b'}">${esc(r.ok ? '—' : (r.fix || '联系导师'))}</td>
        </tr>`).join('')}</tbody></table>`;
  };
}

/* ---------------------------------------------------------------- 视图：我的档案 */

async function viewProfile() {
  const p = await get(`/api/profile?user=${encodeURIComponent(state.user)}`);
  if (p.error) return `<div class="card"><div class="card-body">${esc(p.error)}</div></div>`;
  const st = p.stats || {};
  const radar = (p.radar || []).map(d => ({
    name: d.name, score: d.score || 0, baseline: d.baseline,
  }));
  return `<div class="grid g4">
      ${[['累计提问', st.asked], ['转人工', st.escalated], ['陪练场次', st.coach_sessions], ['连续活跃', st.streak_days + ' 天']]
        .map(([l, v]) => `<div class="card stat"><div class="n">${v == null ? '—' : v}</div><div class="l">${l}</div></div>`).join('')}
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><div class="card-head"><h3>能力雷达</h3>
        <span class="small muted">虚线 = 岗位基线</span></div>
        <div class="card-body radar-wrap">${radarSvg(radar)}</div>
        <div class="card-body" style="padding-top:0">
          ${(p.radar || []).map(d => `<div class="kv"><b>${esc(d.name)}</b>
            <span>${d.score == null ? '<span class="muted">暂无数据</span>'
              : `${d.score} 分　基线 ${d.baseline}　${d.gap >= 0 ? '<span class="tag ok">达标 +' + d.gap + '</span>' : '<span class="tag warn">待提升 ' + d.gap + '</span>'}`}</span></div>`).join('')}
        </div></div>
      <div class="card"><div class="card-head"><h3>${esc(p.path.name || '成长路径')}</h3>
        <span class="small muted">${(p.path.summary || {}).passed || 0} / ${(p.path.summary || {}).total || 0}</span></div>
        <div class="card-body scroll">
          ${(p.path.stages || []).map(s => `<div class="spread" style="padding:7px 0;border-bottom:1px solid var(--line-2)">
            <span>${s.status === 'passed' ? '✅' : s.status === 'available' ? '🔵' : '🔒'} ${esc(s.name)}</span>
            <span class="small muted">${s.best_score != null ? s.best_score + ' 分' : s.status === 'locked' ? '未解锁' : '待完成'}</span>
          </div>`).join('') || '<div class="empty">还没有配置关卡</div>'}
        </div></div>
    </div>
    <div class="card" style="margin-top:16px"><div class="card-head"><h3>最近记录</h3></div>
      <div class="card-body scroll">
        ${(p.recent || []).length ? (p.recent || []).map(e => `<div class="kv">
          <b>${esc(e.created_at.slice(5, 16).replace('T', ' '))}</b>
          <span><span class="tag">${esc({ ask: '提问', exam: '闯关', coach: '陪练', review: '批改', envcheck: '自检' }[e.kind] || e.kind)}</span>
          ${esc(e.ref || '')} ${e.score != null ? '　得分 ' + e.score : ''}</span></div>`).join('')
          : '<div class="empty">还没有记录，去提问或闯关吧</div>'}
      </div></div>`;
}

/* ---------------------------------------------------------------- 视图：导师看板 */

async function viewDashboard() {
  const d = await get('/api/dashboard');
  if (d.error) return `<div class="card"><div class="card-body">${esc(d.error)}</div></div>`;
  const c = d.counters || {}, a = d.alerts || {};

  const alerts = [];
  if ((a.stuck || []).length) alerts.push(`<div class="hint warn"><b>进度停滞预警</b>：${a.stuck.map(esc).join('、')} 已连续多日没有推进，建议主动找他们聊聊。</div>`);
  if ((a.overdue_knowledge || []).length) alerts.push(`<div class="hint warn"><b>知识过期</b>：${a.overdue_knowledge.length} 条知识已过复核日期，尽快确认是否仍然有效。</div>`);
  if (!alerts.length) alerts.push('<div class="hint ok">暂时没有需要处理的预警。</div>');

  return `<div class="grid g4">
      ${[['在培新人', c.newcomers], ['待人工回答', c.open_escalations], ['待确认 FAQ 草稿', c.drafts], ['知识过期', c.overdue]]
        .map(([l, v]) => `<div class="card stat"><div class="n">${v == null ? '—' : v}</div><div class="l">${l}</div></div>`).join('')}
    </div>
    <div class="card" style="margin-top:16px"><div class="card-head"><h3>预警与待办</h3></div>
      <div class="card-body stack">${alerts.join('')}
        ${(a.ready_to_draft || []).length ? `<div class="hint warn"><b>${a.ready_to_draft.length} 个问题已被重复问到</b>，建议沉淀成 FAQ（一键生成草稿）：
          <div style="margin-top:8px">${a.ready_to_draft.map(c2 => `<div class="spread" style="padding:4px 0">
            <span>「${esc(c2.sample_question)}」<span class="tag warn">被问 ${c2.count} 次</span></span>
            <button class="btn sm" data-draft="${c2.id}">生成 FAQ 草稿</button></div>`).join('')}</div></div>` : ''}
      </div></div>

    <div class="card" style="margin-top:16px"><div class="card-head"><h3>新人进度一览</h3>
      <span class="small muted">不排名，只看谁需要帮助</span></div>
      <div class="card-body" style="padding:0">
        <table class="grid-t"><thead><tr><th>新人</th><th>方向</th><th>通关进度</th><th>能力分</th><th>状态</th></tr></thead>
        <tbody>${(d.users || []).map(u => `<tr>
          <td><b>${esc(u.name)}</b><div class="small muted">${esc(u.id)}</div></td>
          <td><span class="tag">${esc(u.track || '—')}</span></td>
          <td style="min-width:150px"><div class="small muted">${u.passed} / ${u.total}</div>
            <div class="progress"><i style="width:${u.percent}%"></i></div></td>
          <td class="small">${Object.entries(u.dimensions || {}).map(([k, v]) => `${esc(k)} ${v}`).join('<br>') || '<span class="muted">—</span>'}</td>
          <td>${u.stuck ? `<span class="tag bad">停滞 ${u.idle_days} 天</span>` : '<span class="tag ok">正常</span>'}
            ${u.open_escalations ? `<span class="tag warn">待答 ${u.open_escalations}</span>` : ''}</td>
        </tr>`).join('') || '<tr><td colspan="5"><div class="empty">还没有新人档案</div></td></tr>'}</tbody></table>
      </div></div>

    <div class="grid g2" style="margin-top:16px">
      <div class="card"><div class="card-head"><h3>需要导师回答的问题</h3></div>
        <div class="card-body scroll">
          ${(a.escalations || []).length ? a.escalations.map(e => `<div class="kv">
            <b>${esc(e.user)}</b><span>${esc(e.question)}<div class="small muted">${esc(e.reason)}　·　${esc(e.created_at.slice(5, 16).replace('T', ' '))}</div></span></div>`).join('')
            : '<div class="empty">没有待处理的转人工问题</div>'}
        </div></div>
      <div class="card"><div class="card-head"><h3>最近提问</h3></div>
        <div class="card-body scroll">
          ${(d.recent_questions || []).map(q => `<div class="kv"><b>${esc(q.user)}</b>
            <span>${esc(q.question)} ${q.escalated ? '<span class="tag bad">未答上</span>' : '<span class="tag ok">已自动回答</span>'}</span></div>`).join('')
            || '<div class="empty">还没有提问记录</div>'}
        </div></div>
    </div>

    <div class="card" style="margin-top:16px"><div class="card-head"><h3>待确认的 FAQ 草稿</h3>
      <span class="small muted">确认后即进入检索池，同类问题不再打扰你</span></div>
      <div class="card-body">
        ${(a.pending_drafts || []).length ? a.pending_drafts.map(f => `<div style="padding:12px 0;border-bottom:1px solid var(--line-2)">
          <div class="spread"><b>${esc(f.q)}</b><span class="tag warn">被问 ${f.asked_count || 1} 次</span></div>
          <textarea data-faq-a="${esc(f.id)}" style="margin-top:9px;min-height:88px">${esc(f.a)}</textarea>
          <div class="row" style="margin-top:8px">
            <button class="btn primary sm" data-faq-pub="${esc(f.id)}">确认发布</button>
            <button class="btn sm" data-faq-del="${esc(f.id)}">删除</button>
          </div></div>`).join('') : '<div class="empty">没有待确认的草稿。新人问到知识库没有的问题时，这里会自动出现草稿。</div>'}
      </div></div>

    <div class="card" style="margin-top:16px"><div class="card-head"><h3>沉淀候选池</h3>
      <span class="small muted">AI 没答上来的问题，会被自动聚类到这里</span></div>
      <div class="card-body scroll">
        ${(a.candidates || []).length ? a.candidates.map(c2 => `<div class="kv">
          <b>${c2.count >= (c2._t || 2) ? '<span class="tag bad">高频</span>' : '<span class="tag">低</span>'}</b>
          <span>「${esc(c2.sample_question)}」<span class="small muted">被问 ${c2.count} 次</span>
          <button class="btn sm" data-draft="${c2.id}" style="margin-left:8px">生成草稿</button></span></div>`).join('')
          : '<div class="empty">候选池为空</div>'}
      </div></div>`;
}

function bindDashboard() {
  document.querySelectorAll('[data-draft]').forEach(b => {
    b.onclick = async () => {
      const res = await post('/api/faq/draft', { candidate_id: +b.dataset.draft });
      if (res.error) { toast(res.error); return; }
      toast('已生成 FAQ 草稿，请在下方确认');
      render();
    };
  });
  document.querySelectorAll('[data-faq-pub]').forEach(b => {
    b.onclick = async () => {
      const id = b.dataset.faqPub;
      const ta = document.querySelector(`[data-faq-a="${id}"]`);
      const res = await post('/api/faq/publish', { id, a: ta ? ta.value : '' });
      if (res.error) { toast(res.error); return; }
      toast('已发布，后续同类问题会自动命中');
      render();
    };
  });
  document.querySelectorAll('[data-faq-del]').forEach(b => {
    b.onclick = async () => {
      await post('/api/faq/delete', { id: b.dataset.faqDel });
      toast('已删除'); render();
    };
  });
}

/* ---------------------------------------------------------------- 视图：知识库 */

async function viewKb() {
  const q = state.kbQuery || '';
  const data = await get('/api/kb' + (q ? '?q=' + encodeURIComponent(q) : ''));
  const items = data.items || [];
  return `<div class="card"><div class="card-head"><h3>知识库</h3>
      <span class="small muted">每条知识都有责任人和复核日期，过期会被标红</span></div>
    <div class="card-body">
      <div class="row">
        <input type="text" id="kb-q" value="${esc(q)}" placeholder="搜索知识条目与 FAQ…" style="max-width:420px">
        <button class="btn primary" id="kb-search">搜索</button>
        ${q ? '<button class="btn" id="kb-clear">清空</button>' : ''}
      </div>
    </div></div>
    <div class="card" style="margin-top:16px"><div class="card-body" style="padding:0">
      <table class="grid-t"><thead><tr><th>类型</th><th>标题</th><th>责任人</th><th>复核日期</th><th></th></tr></thead>
        <tbody>${items.map(i => `<tr>
          <td>${i.kind === 'faq' ? '<span class="tag accent">FAQ</span>' : '<span class="tag">知识</span>'}</td>
          <td><b>${esc(i.title)}</b>${i.snippet ? `<div class="small muted">${esc(i.snippet)}…</div>` : ''}
            ${i.tags && i.tags.length ? `<div style="margin-top:4px">${i.tags.map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>` : ''}</td>
          <td class="small">${esc(i.owner || '—')}</td>
          <td class="small">${esc(i.review_date || '—')} ${i.stale ? '<span class="tag warn">待复核</span>' : ''}</td>
          <td>${i.score ? `<span class="small muted">${(i.score * 100).toFixed(0)}%</span>` : ''}
            <button class="btn sm" data-kb="${esc(i.id)}">查看</button></td>
        </tr>`).join('') || '<tr><td colspan="5"><div class="empty">没有找到内容</div></td></tr>'}</tbody></table>
    </div></div>
    <div id="kb-detail"></div>`;
}

function bindKb() {
  const input = document.getElementById('kb-q');
  const doSearch = () => { state.kbQuery = input.value.trim(); render(); };
  document.getElementById('kb-search').onclick = doSearch;
  input.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
  const clr = document.getElementById('kb-clear');
  if (clr) clr.onclick = () => { state.kbQuery = ''; render(); };
  document.querySelectorAll('[data-kb]').forEach(b => {
    b.onclick = async () => {
      const d = await get('/api/kb/' + encodeURIComponent(b.dataset.kb));
      const box = document.getElementById('kb-detail');
      box.innerHTML = `<div class="card" style="margin-top:16px"><div class="card-head">
          <h3>${esc(d.title || d.q || d.id)}</h3>
          <button class="btn sm" onclick="document.getElementById('kb-detail').innerHTML=''">关闭</button></div>
        <div class="card-body">
          <div class="row small muted" style="margin-bottom:12px">
            <span>责任人：${esc(d.owner || '—')}</span><span>复核日期：${esc(d.review_date || '—')}</span>
            <span>来源：${esc(d.source || '—')}</span>
            ${d.stale ? '<span class="tag warn">已过复核日期</span>' : ''}</div>
          <div class="md">${md(d.body || '')}</div></div></div>`;
      box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    };
  });
}

/* ---------------------------------------------------------------- 视图：设置 */

async function viewSettings() {
  const d = await get('/api/settings');
  const s = d.settings || {}, llm = s.llm || {};
  return `<div class="card"><div class="card-head"><h3>大模型配置（可选）</h3>
      <span class="tag ${d.llm_status && d.llm_status.enabled ? 'ok' : 'warn'}">${d.llm_status && d.llm_status.enabled ? '已接入' : '未接入 · 本地模式'}</span></div>
    <div class="card-body">
      <div class="hint">不填也能用：未接入时，问答直接返回知识库原文并附出处，陪练切换为「结构化引导自省模式」，
        批改使用可解释的本地启发式评分。<b>填入 OpenAI 兼容接口后，问答、陪练、批改会自动升级为模型生成。</b></div>
      <div class="grid g2" style="margin-top:14px">
        <div><label class="small muted">Base URL</label>
          <input type="text" id="s-base" value="${esc(llm.base_url || '')}" placeholder="https://api.deepseek.com/v1"></div>
        <div><label class="small muted">模型名</label>
          <input type="text" id="s-model" value="${esc(llm.model || '')}" placeholder="deepseek-chat"></div>
      </div>
      <div style="margin-top:12px"><label class="small muted">API Key</label>
        <input type="password" id="s-key" value="${esc(llm.api_key || '')}" placeholder="sk-...">
        <div class="small muted" style="margin-top:5px">也可以改用环境变量 MENTOR_LLM_BASE_URL / MENTOR_LLM_API_KEY / MENTOR_LLM_MODEL（优先级更高）。</div></div>
      <div class="row" style="margin-top:16px">
        <button class="btn primary" id="s-save">保存并生效</button>
        <button class="btn" id="s-test">测试连接</button>
        <span class="small muted">配置保存在 data/runtime/settings.json，不会提交到版本库</span>
      </div>
      <div id="s-result" style="margin-top:12px"></div>
    </div></div>
  <div class="card" style="margin-top:16px"><div class="card-head"><h3>检索与沉淀参数</h3></div>
    <div class="card-body">
      <div class="kv"><b>命中阈值</b><span>${(s.retrieval || {}).match_threshold} —— 覆盖率正常时，低于此相关度判定"知识库没有"，自动转人工</span></div>
      <div class="kv"><b>低覆盖率阈值</b><span>${(s.retrieval || {}).strong_threshold} —— 覆盖率低于 ${(s.retrieval || {}).min_coverage} 时改用这个更高的门槛，避免被单个高频词带偏</span></div>
      <div class="kv"><b>沉淀触发次数</b><span>${(s.retrieval || {}).draft_trigger_count} —— 同一问题被问几次后自动生成 FAQ 草稿</span></div>
      <div class="kv"><b>转人工联系人</b><span>${esc((s.mentor || {}).escalate_contact || '导师')}</span></div>
    </div></div>
  ${issuesCard()}`;
}

/* 内容文件校验结果。内容文件是给人手改的，静默失效比崩溃更危险。 */
function issuesCard() {
  const issues = (state.meta || {}).issues || [];
  const errs = issues.filter(i => i.level === 'error');
  const warns = issues.filter(i => i.level === 'warn');
  if (!issues.length) {
    return `<div class="card" style="margin-top:16px"><div class="card-head"><h3>内容文件校验</h3>
      <span class="tag ok">全部通过</span></div>
      <div class="card-body"><div class="hint ok">知识库、FAQ、关卡地图、评分标准、陪练场景、环境自检项，全部格式正确、引用有效。</div></div></div>`;
  }
  const row = (i) => `<div class="kv"><b>${i.level === 'error' ? '<span class="tag bad">错误</span>' : '<span class="tag warn">提醒</span>'}</b>
    <span><code>${esc(i.where)}</code> ${esc(i.message)}</span></div>`;
  return `<div class="card" style="margin-top:16px"><div class="card-head"><h3>内容文件校验</h3>
      <span class="tag ${errs.length ? 'bad' : 'warn'}">${errs.length} 个错误 · ${warns.length} 个提醒</span></div>
    <div class="card-body">
      ${errs.length ? '<div class="hint bad" style="margin-bottom:12px">有错误的文件会被整体忽略（例如 JSON 里少一个逗号，整个 faq.json 都不会生效）。请修掉后再刷新页面。</div>' : ''}
      <div class="scroll">${[...errs, ...warns].map(row).join('')}</div>
    </div></div>`;
}

function bindSettings() {
  document.getElementById('s-save').onclick = async () => {
    const payload = { llm: {
      enabled: true,
      base_url: document.getElementById('s-base').value.trim(),
      model: document.getElementById('s-model').value.trim(),
      api_key: document.getElementById('s-key').value.trim(),
    }};
    if (!payload.llm.base_url || !payload.llm.model) {
      payload.llm.enabled = false;
      toast('Base URL 与模型名为空，已保持本地模式');
    }
    const res = await post('/api/settings', { settings: payload });
    toast(res.ok ? '已保存并生效' : '保存失败');
    state.meta = await get('/api/meta');
    renderBadges();
    render();
  };
  document.getElementById('s-test').onclick = async () => {
    const box = document.getElementById('s-result');
    box.innerHTML = '<span class="spinner"></span> 正在测试…';
    const res = await post('/api/ask', { user: state.user, question: '你好，请回复"连接正常"四个字' });
    box.innerHTML = res.mode === 'llm'
      ? '<div class="hint ok">连接正常，模型已可用。</div>'
      : '<div class="hint warn">没有走通模型（可能未保存配置、Key 无效或网络不通）。当前仍是本地模式，功能不受影响。</div>';
  };
}

/* ---------------------------------------------------------------- 渲染 */

const VIEWS = {
  ask: [viewAsk, bindAsk], path: [viewPath, bindPath], coach: [viewCoach, bindCoach],
  review: [viewReview, bindReview], env: [viewEnv, bindEnv], profile: [viewProfile, () => {}],
  dashboard: [viewDashboard, bindDashboard], kb: [viewKb, bindKb], settings: [viewSettings, bindSettings],
};

let rendering = false;
async function render() {
  if (rendering) return;
  rendering = true;
  try {
    document.getElementById('view-title').textContent = TITLES[state.view] || '';
    document.querySelectorAll('.nav-item').forEach(b =>
      b.classList.toggle('active', b.dataset.view === state.view));
    const el = document.getElementById('view');
    const spec = VIEWS[state.view];
    el.innerHTML = '<div class="empty"><span class="spinner"></span> 加载中…</div>';
    const html = await spec[0]();
    el.innerHTML = html;
    spec[1]();
  } catch (e) {
    document.getElementById('view').innerHTML =
      '<div class="hint bad">页面渲染出错：' + esc(e.message) + '</div>';
    console.error(e);
  } finally {
    rendering = false;
  }
}

function renderBadges() {
  const m = state.meta || {};
  const llm = m.llm || {};
  const badge = document.getElementById('mode-badge');
  badge.textContent = llm.enabled ? '大模型已接入' : '本地模式';
  badge.classList.toggle('on', !!llm.enabled);
  const k = m.knowledge || {};
  const issues = m.issues || [];
  const errs = issues.filter(i => i.level === 'error').length;
  document.getElementById('kb-stat').innerHTML =
    `${k.entries || 0} 条知识 · ${k.faq_published || 0} 条 FAQ` +
    (errs ? `<br><span style="color:#d93025">内容文件有 ${errs} 个错误，去「设置」查看</span>`
          : (issues.length ? `<br><span style="color:#c77700">内容文件有 ${issues.length} 条提醒</span>` : ''));
}

async function boot() {
  state.meta = await get('/api/meta');
  if (state.meta.error) {
    document.getElementById('view').innerHTML = '<div class="hint bad">无法连接后端：' + esc(state.meta.error) + '</div>';
    return;
  }
  const sel = document.getElementById('user-select');
  const users = state.meta.users || [];
  if (!users.find(u => u.id === state.user)) state.user = users.length ? users[0].id : 'demo';
  sel.innerHTML = users.map(u =>
    `<option value="${esc(u.id)}" ${u.id === state.user ? 'selected' : ''}>${esc(u.name)}（${esc(u.track || '通用')}）</option>`).join('');
  sel.onchange = () => {
    state.user = sel.value;
    localStorage.setItem('mentor.user', state.user);
    state.chat = []; state.coach = null;
    toast('已切换到 ' + sel.options[sel.selectedIndex].text);
    render();
  };
  document.querySelectorAll('.nav-item').forEach(b => {
    b.onclick = () => { state.view = b.dataset.view; render(); };
  });
  renderBadges();
  render();
}

boot();
