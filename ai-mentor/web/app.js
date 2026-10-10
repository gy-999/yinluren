/* 引路人 · AI 导师 —— 前端（原生 JS，无构建） */
'use strict';

const ROLE = window.APP_ROLE || 'newcomer';   // 双端分离：index.html=新人端，mentor.html=导师端

const state = {
  user: localStorage.getItem('mentor.user') || 'demo',
  token: localStorage.getItem('mentor.token') || '',
  project: localStorage.getItem('mentor.project') || '',
  meta: null,
  view: ROLE === 'mentor' ? 'dashboard' : 'ask',
  chat: [],          // 问答记录
  coach: null,       // 当前陪练会话
  exam: null,        // 当前考试
};

const TITLES = {
  ask: '问答助手', tasks: '任务中心', weekly: '周报', path: '成长地图', coach: '陪练室',
  env: '环境自检', profile: '我的档案', dashboard: '导师看板', kb: '知识库', settings: '设置',
};

/* ---------------------------------------------------------------- 基础 */

async function api(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign({ 'X-Token': state.token }, opts.headers || {});
  const res = await fetch(path, opts);
  if (res.status === 401 || res.status === 403) {
    const data = await res.json().catch(() => ({ error: 'HTTP ' + res.status }));
    if (data.auth_required) showLogin(data.error);
    return data;
  }
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

async function viewAsk() {
  const mine = await get('/api/escalations/mine?user=' + encodeURIComponent(state.user));
  const mineItems = (mine.items || []).slice(0, 8);
  const msgs = state.chat.map((m, idx) => {
    if (m.role === 'user') {
      return `<div class="msg me"><div class="avatar">我</div><div class="bubble">${esc(m.text)}</div></div>`;
    }
    let body = '';
    if (m.escalated) {
      body = `<div class="hint warn"><b>已转人工</b>：${esc(m.reason)}<br>导师回复后会出现在下方「我的求助」里，也可直接找 ${esc(m.contact || '导师')}。
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
      body += `<div class="small muted" style="margin-top:8px">相关度 ${relPct(m.confidence)}%　·　词覆盖 ${Math.round((m.coverage || 0) * 100)}%　·　${m.mode === 'llm' ? 'AI + 知识库生成' : '知识库原文直出'}</div>`;
      body += `<div class="row" style="margin-top:10px">
        <button class="btn sm" data-solved="${idx}">✅ 已解决</button>
        <button class="btn sm" data-esc="${idx}">✉ 没答好？联系导师</button>
        ${m.solved ? '<span class="tag ok">已解决</span>' : ''}
        ${m.escNote ? '<span class="tag warn">已转导师</span>' : ''}
      </div>`;
    }
    return `<div class="msg bot"><div class="avatar">引</div><div class="bubble">${body}</div></div>`;
  }).join('');

  const mineHtml = mineItems.map(e => `<div class="kv" style="align-items:flex-start">
    <b>${e.status === 'done' ? '<span class="tag ok">已回复</span>' : '<span class="tag warn">待导师回复</span>'}</b>
    <span>${esc(e.question)}
      <div class="small muted">${esc(e.reason)} · ${esc(String(e.created_at).slice(5, 16).replace('T', ' '))}</div>
      ${e.answer ? `<div class="hint ok" style="margin-top:5px"><b>导师回复</b>：${esc(e.answer)}</div>` : ''}
    </span></div>`).join('');

  return `<div class="chat stack">
    ${state.chat.length ? '' : `<div class="card"><div class="card-body">
      <div class="hint">问什么都可以：环境怎么搭、流程怎么走、这个需求该怎么理解、报销找谁……
        <b>答案由 AI 基于知识库生成，一定附出处</b>；没答好可以一键转给导师，回复会回到这个页面。</div>
      <div class="row" style="margin-top:12px">
        ${['开发环境怎么搭？', '需求评审的流程是什么？', '请假流程怎么走？', '代码提交规范有哪些？']
          .map(q => `<button class="btn sm" data-quick="${esc(q)}">${esc(q)}</button>`).join('')}
      </div></div></div>`}
    ${msgs}
    <div class="card"><div class="card-body">
      <div class="row" style="align-items:center;gap:10px;margin-bottom:10px">
        <label class="small muted">知识范围</label>
        <select id="proj-sel" style="max-width:240px">
          <option value="">通用知识库</option>
          ${((state.meta && state.meta.projects) || []).map(p => `<option value="${esc(p.id)}" ${state.project === p.id ? 'selected' : ''}>项目：${esc(p.name)}</option>`).join('')}
        </select>
        <span class="muted small">选中项目后，回答优先命中该项目的知识库（各项目互相独立）</span>
      </div>
      <div class="askbox">
        <textarea id="q" placeholder="输入你的问题…（Ctrl + Enter 发送）"></textarea>
        <button class="btn primary" id="send">发送</button>
      </div>
      <div class="row" style="margin-top:10px">
        <label class="small muted"><input type="checkbox" id="anon" ${state.anon ? 'checked' : ''}> 匿名提问（导师看板会显示"匿名"）</label>
      </div>
    </div></div>
    <div class="card" style="margin-top:14px"><div class="card-head"><h3>✉ 联系导师</h3>
        <span class="small muted">不用等 AI：把问题直接发给导师，回复回到下方「我的求助」</span></div>
      <div class="card-body">
        <textarea id="contact-q" rows="2" placeholder="写下你的问题，直接发给导师"></textarea>
        <div class="row" style="margin-top:8px">
          <input type="text" id="contact-note" placeholder="补充说明（可留空）" style="flex:1">
          <button class="btn primary sm" id="contact-send">发送给导师</button>
        </div>
      </div></div>
    ${mineItems.length ? `<div class="card"><div class="card-head"><h3>我的求助</h3>
      <span class="small muted">点「联系导师」后，导师的回复会出现在这里</span></div>
      <div class="card-body">${mineHtml}</div></div>` : ''}
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
    const res = await post('/api/ask', { user: state.user, question: q, anonymous: state.anon, project: state.project || '' });
    state.chat.push({
      role: 'bot', text: res.answer || '', sources: res.sources || [], q: q,
      escalated: res.escalated, reason: res.reason, contact: res.contact,
      confidence: res.confidence || 0, mode: res.mode, caveat: res.caveat,
      notice: res.notice, coverage: res.coverage,
    });
    render();
    const v = document.getElementById('view');
    v.scrollTop = v.scrollHeight;
  };
  const psel = document.getElementById('proj-sel');
  if (psel) psel.onchange = () => {
    state.project = psel.value;
    localStorage.setItem('mentor.project', state.project);
    toast(psel.value ? '已切换到项目知识库' : '已切换到通用知识库');
  };
  const csend = document.getElementById('contact-send');
  if (csend) csend.onclick = async () => {
    const q = document.getElementById('contact-q').value.trim();
    if (!q) { toast('请写下你的问题'); return; }
    const r = await post('/api/ask/escalate', {
      user: state.user, question: q,
      note: document.getElementById('contact-note').value.trim(),
    });
    if (r.error) { toast(r.error); return; }
    document.getElementById('contact-q').value = '';
    document.getElementById('contact-note').value = '';
    toast('已发给导师，回复会出现在「我的求助」');
    render();
  };
  document.getElementById('send').onclick = send;
  ta.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
  });
  document.querySelectorAll('[data-quick]').forEach(b => {
    b.onclick = () => { ta.value = b.dataset.quick; send(); };
  });
  document.querySelectorAll('[data-solved]').forEach(b => {
    b.onclick = () => {
      const m = state.chat[+b.dataset.solved];
      if (m) m.solved = true;
      toast('好的，这条就先这样');
      render();
    };
  });
  document.querySelectorAll('[data-esc]').forEach(b => {
    b.onclick = async () => {
      const m = state.chat[+b.dataset.esc];
      if (!m) return;
      const note = prompt('想补充给导师的说明（可留空）：') || '';
      const r = await post('/api/ask/escalate', { user: state.user, question: m.q || '', note });
      if (r.error) { toast(r.error); return; }
      m.escNote = true;
      toast('已转给导师，回复会出现在「我的求助」里');
      render();
    };
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
  const c = d.counters || {};

  const escList = (d.escalations || []).map(e => `<div class="kv" style="align-items:flex-start">
      <b>${esc(e.user_name)}</b><span>${esc(e.question)}
        <div class="small muted">${esc(e.reason)} · ${esc(String(e.created_at).slice(5, 16).replace('T', ' '))}</div>
        <div class="row" style="margin-top:6px">
          <input type="text" data-ans-input="${e.id}" placeholder="回复答案（新人端可见）" style="flex:1;min-width:220px">
          <button class="btn sm primary" data-ans="${e.id}">回复并关闭</button>
        </div></span></div>`).join('');

  const msgs = (d.messages || []).map(m => `<div class="kv" style="align-items:flex-start">
      <b>${esc(m.user_name)}</b><span>【${esc(m.task_title)} / ${esc(m.item_title)}】${md(m.content)}
        <span class="tag ${m.status === 'confirmed' ? 'ok' : 'warn'}">${m.status === 'confirmed' ? '已确认' : '待确认'}</span>
        <span class="muted small">${esc(String(m.created_at).slice(5, 16).replace('T', ' '))}</span></span></div>`).join('');

  const weeks = (d.weeklies || []).map(w => `<div class="kv" style="align-items:flex-start">
      <b>${esc(w.user_name)}</b><span>${esc(w.week_label)}
        <span class="tag ${w.score >= 80 ? 'ok' : w.score >= 60 ? 'warn' : 'bad'}">${w.score != null ? w.score + ' 分' : '—'}</span>
        <details style="margin-top:4px"><summary class="small muted" style="cursor:pointer">查看内容</summary>
          <div class="md" style="margin-top:6px">${md(w.content)}</div></details></span></div>`).join('');

  const projects = (state.meta && state.meta.projects) || [];
  const projList = projects.length ? projects.map(p => `<div class="kv"><b>${esc(p.name)}</b>
      <span class="muted small">${esc(p.description || '')} · 导入于 ${esc(p.created_at)}</span></div>`).join('')
      : '<div class="muted small">还没有导入项目</div>';

  return `<div class="grid g4">
      ${[['待回复问题', c.pending_questions], ['新消息', c.messages], ['收到周报', c.weeklies]]
        .map(([l, v]) => `<div class="card stat"><div class="n">${v == null ? '—' : v}</div><div class="l">${l}</div></div>`).join('')}
    </div>

    <div class="card" style="margin-top:16px"><div class="card-head"><h3>新人问题 · 回复后新人端可见</h3></div>
      <div class="card-body scroll">${escList || '<div class="empty">暂时没有待回复的问题</div>'}</div></div>

    <div class="card" style="margin-top:16px"><div class="card-head"><h3>收件箱 · 新人消息与周报</h3>
      <a class="btn sm" href="/api/weekly/export" download="周报导出.md">⬇ 导出全部周报为文件</a></div>
      <div class="card-body">
        <div class="small muted" style="margin-bottom:6px">任务反馈（新人消息）</div>
        ${msgs || '<div class="empty">还没有收到消息</div>'}
        <div class="small muted" style="margin:12px 0 6px">周报</div>
        ${weeks || '<div class="empty">还没有收到周报</div>'}
      </div></div>

    <div class="card" style="margin-top:16px"><div class="card-head"><h3>项目知识库</h3>
        <span class="small muted">导入项目文件，AI 自动生成该项目专属知识库；各项目互相独立，新人端按项目分别提问</span></div>
      <div class="card-body">
        ${projList}
        <div style="margin-top:10px;border-top:1px solid var(--line-2);padding-top:10px">
          <div class="grid g2">
            <div><label class="small muted">项目名称</label>
              <input type="text" id="pj-name" placeholder="如：卫星遥测项目"></div>
            <div><label class="small muted">项目说明（可选）</label>
              <input type="text" id="pj-desc" placeholder="一句话说明这个项目"></div>
          </div>
          <div class="row" style="margin-top:10px">
            <input type="file" id="pj-files" multiple style="max-width:360px">
            <button class="btn primary" id="pj-import">导入并生成知识库</button>
          </div>
          <div class="small muted" style="margin-top:6px">支持代码与文本文件（依赖清单如 requirements.txt / package.json 会被重点分析）；未接入大模型时生成资料清单与依赖线索的保底版本。</div>
        </div>
      </div></div>`;
}

function bindDashboard() {
  document.querySelectorAll('[data-ans]').forEach(b => {
    b.onclick = async () => {
      const input = document.querySelector('[data-ans-input="' + b.dataset.ans + '"]');
      const answer = (input && input.value.trim()) || '';
      if (!answer) { toast('请填写回复内容'); return; }
      const r = await post('/api/escalation/answer', { id: +b.dataset.ans, answer });
      toast(r.error ? r.error : '已回复，新人可在问答页看到');
      if (!r.error) render();
    };
  });
  const imp = document.getElementById('pj-import');
  if (imp) imp.onclick = async () => {
    const name = document.getElementById('pj-name').value.trim();
    if (!name) { toast('请填写项目名称'); return; }
    const fd = new FormData();
    fd.append('name', name);
    fd.append('description', document.getElementById('pj-desc').value);
    for (const f of document.getElementById('pj-files').files) fd.append('files', f);
    imp.disabled = true;
    toast('正在导入并生成知识库…');
    const r = await postForm('/api/projects/import', fd);
    imp.disabled = false;
    if (r.error) { toast(r.error); return; }
    toast('项目已导入，AI 生成了 ' + r.generated + ' 条知识');
    state.meta = await get('/api/meta');
    render();
  };
}

/* ---------------------------------------------------------------- 视图：知识库 */

async function viewKb() {
  const drafts = await get('/api/faq/drafts');

  const q = state.kbQuery || '';
  const data = await get('/api/kb' + (q ? '?q=' + encodeURIComponent(q) : ''));
  const items = data.items || [];
  return `${faqCard(drafts)}<div class="card"><div class="card-head"><h3>知识库</h3>
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

function faqCard(drafts) {
  const a = drafts || {};
  const trigger = a.trigger || 2;
  const pending = a.drafts || [];
  const cands = a.candidates || [];
  return `<div class="card" style="margin-bottom:16px"><div class="card-head"><h3>FAQ 沉淀</h3>
      <span class="small muted">新人重复问的问题自动变草稿，确认后进入检索池，下一届不再打扰</span></div>
    <div class="card-body">
      <div class="small muted">待确认草稿</div>
      ${pending.length ? pending.map(f => `<div style="padding:10px 0;border-bottom:1px solid var(--line-2)">
        <div class="spread"><b>${esc(f.q)}</b><span class="tag warn">被问 ${f.asked_count || 1} 次</span></div>
        <textarea data-faq-a="${esc(f.id)}" style="margin-top:8px;min-height:80px">${esc(f.a)}</textarea>
        <div class="row" style="margin-top:6px">
          <button class="btn primary sm" data-faq-pub="${esc(f.id)}">确认发布</button>
          <button class="btn sm" data-faq-del="${esc(f.id)}">删除</button>
        </div></div>`).join('') : '<div class="muted small">暂无待确认草稿</div>'}
      <div class="small muted" style="margin:12px 0 6px">沉淀候选池（同一问题被问 ${trigger} 次即可生成草稿）</div>
      ${cands.length ? cands.slice(0, 10).map(c2 => `<div class="kv"><b>${c2.count >= trigger ? '<span class="tag bad">高频</span>' : '<span class="tag">低</span>'}</b>
        <span>「${esc(c2.sample_question)}」<span class="small muted">被问 ${c2.count} 次</span>
        <button class="btn sm" data-draft="${c2.id}" style="margin-left:8px">生成草稿</button></span></div>`).join('') : '<div class="muted small">候选池为空</div>'}
    </div></div>`;
}

function bindKb() {
  document.querySelectorAll('[data-draft]').forEach(b => {
    b.onclick = async () => {
      const res = await post('/api/faq/draft', { candidate_id: +b.dataset.draft });
      if (res.error) { toast(res.error); return; }
      toast('已生成 FAQ 草稿，请在上方确认');
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
      const res = await post('/api/faq/delete', { id: b.dataset.faqDel });
      toast(res.error ? res.error : '已删除');
      if (!res.error) render();
    };
  });
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
      ${d.llm_status && d.llm_status.last_error ? `<div class="hint bad">上次模型调用失败：${esc(d.llm_status.last_error)}</div>` : ''}
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
    if (res.mode === 'llm') {
      box.innerHTML = '<div class="hint ok">连接正常，模型已可用。</div>';
      return;
    }
    const st = await get('/api/settings');
    const err = (st.llm_status || {}).last_error;
    box.innerHTML = '<div class="hint warn">没有走通模型，当前仍是本地模式（功能不受影响）。'
      + (err ? '失败原因：' + esc(err) : '可能未保存配置、Key 无效或网络不通。') + '</div>';
  };
}

/* ---------------------------------------------------------------- 视图：任务中心 */

const fmtSize = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB'
  : n > 1024 ? Math.round(n / 1024) + ' KB' : n + ' B';

const fileIcon = (mime, name) => {
  const m = mime || '';
  if (m.startsWith('image/')) return '🖼️';
  if (m.startsWith('video/')) return '🎬';
  if (m.startsWith('audio/')) return '🎵';
  const n = (name || '').toLowerCase();
  if (['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.md', '.txt'].some(e => n.endsWith(e))) return '📄';
  return '📎';
};

const fileUrl = (f) => '/api/files/' + f.id
  + ((state.meta && state.meta.auth_required && state.token)
    ? '?token=' + encodeURIComponent(state.token) : '');

const postForm = (p, fd) => api(p, { method: 'POST', body: fd });

function attachmentHtml(f) {
  const icon = fileIcon(f.mime, f.filename);
  const url = fileUrl(f);
  if ((f.mime || '').startsWith('video/')) {
    return `<div class="kv"><b>${icon} ${esc(f.filename)}</b><span>${fmtSize(f.size)}</span></div>
      <video controls preload="metadata" src="${url}" style="max-width:100%;border-radius:8px;margin:6px 0"></video>`;
  }
  if ((f.mime || '').startsWith('image/')) {
    return `<div class="kv"><b>${icon} ${esc(f.filename)}</b><span>${fmtSize(f.size)}</span></div>
      <img src="${url}" alt="${esc(f.filename)}" loading="lazy"
        style="max-width:100%;max-height:340px;border-radius:8px;margin:6px 0;display:block">`;
  }
  return `<div class="kv"><b>${icon} ${esc(f.filename)}</b>
    <span><a href="${url}" download="${esc(f.filename)}">下载</a> · ${fmtSize(f.size)}</span></div>`;
}

const ITYPE_LABEL = { question: '问题回答', requirement: '需求', general: '其他' };

async function viewTasks() {
  const d = await get('/api/tasks?user=' + encodeURIComponent(state.user));
  const users = (state.meta && state.meta.users) || [];
  const tasks = d.tasks || [];

  const fbBlock = (it) => `
    <div style="margin-top:8px">
      ${it.feedbacks.length ? it.feedbacks.map(f => `<div class="kv" style="align-items:flex-start">
        <b>${esc(f.user_name)}</b>
        <span><span class="tag ${f.status === 'confirmed' ? 'ok' : 'warn'}">${f.status === 'confirmed' ? '导师已确认' : '待导师确认'}</span>
          ${f.content ? md(f.content) : ''}
          ${f.note ? `<div class="hint" style="margin-top:4px">导师点评：${esc(f.note)}</div>` : ''}
          ${(f.files || []).map(attachmentHtml).join('')}
          <span class="muted small">${esc(f.created_at)}</span>
          ${ROLE === 'mentor' && f.status !== 'confirmed' ? `<button class="btn sm" data-confirm="${f.id}" style="margin-top:6px">✓ 确认</button>` : ''}
        </span></div>`).join('') : '<div class="muted small">这个子项还没有反馈</div>'}
    </div>`;

  const itemBlock = (t, it) => `
    <div style="border-top:1px solid var(--line-2);padding:10px 0">
      <div class="spread"><span><span class="tag ${it.itype === 'question' ? 'accent' : ''}">${ITYPE_LABEL[it.itype] || '其他'}</span>
        <b>${esc(it.title)}</b></span>
        <span class="small muted">${it.my_done ? '<span class="tag ok">我已完成</span>' : '待我完成'}</span></div>
      ${it.content ? `<div class="md" style="margin-top:6px">${md(it.content)}</div>` : ''}
      ${(it.files || []).length ? `<div style="margin-top:6px">${it.files.map(attachmentHtml).join('')}</div>` : ''}
      ${fbBlock(it)}
      ${ROLE === 'newcomer' && !it.my_done ? `<div style="margin-top:8px">
        <textarea id="fb-text-${it.id}" rows="2" placeholder="完成情况说明（可只传附件）"></textarea>
        <div class="row" style="margin-top:6px">
          <input type="file" id="fb-files-${it.id}" multiple style="max-width:300px">
          <button class="btn primary sm" data-fb="${it.id}" data-task="${t.id}">提交该子项</button>
        </div>
      </div>` : ''}
    </div>`;

  const taskCard = (t) => `
    <div class="card" style="margin-top:14px">
      <div class="card-head"><h3>${esc(t.title)}</h3>
        <span class="tag ${t.my_done ? 'ok' : 'warn'}">${t.items_total ? '子项 ' + t.items_my_done + '/' + t.items_total : '无需反馈'}</span></div>
      <div class="card-body">
        <div class="muted small">${esc(t.creator_name)} 发布于 ${esc(t.created_at)}
          · ${t.assignee ? '指定给 ' + esc(t.assignee_name) : '全员'}</div>
        ${t.description ? `<div class="md" style="margin-top:8px">${md(t.description)}</div>` : ''}
        ${(t.files || []).length ? `<div style="margin-top:8px"><div class="small muted">资料附件</div>${t.files.map(attachmentHtml).join('')}</div>` : ''}
        ${t.items.map(it => itemBlock(t, it)).join('') || '<div class="muted small" style="margin-top:8px">这个任务没有拆子项，看说明即可</div>'}
        ${ROLE === 'mentor' ? `<div class="row" style="margin-top:10px;justify-content:flex-end">
          <button class="btn small" data-del="${t.id}">删除任务</button></div>` : ''}
      </div>
    </div>`;

  const pubCard = ROLE === 'mentor' ? `
    <div class="card"><div class="card-head"><h3>发布新任务</h3>
        <span class="muted small">按需求拆成多个子项：问题回答 / 需求 / 其他；资料附件全员可见</span></div>
      <div class="card-body">
        <div style="margin-bottom:12px"><label class="small muted">让 AI 起草（粘贴需求描述，自动填好整张表单，可修改）</label>
          <div class="row" style="margin-top:4px">
            <textarea id="t-ai" rows="2" placeholder="需求描述：要做什么、给谁做、验收标准…" style="flex:1"></textarea>
            <button class="btn sm" id="t-ai-btn">✨ AI 起草</button>
          </div></div>
        <div class="grid g2">
          <div><label class="small muted">任务标题</label>
            <input type="text" id="t-title" placeholder="如：登录模块开发任务"></div>
          <div><label class="small muted">指定给</label>
            <select id="t-assignee"><option value="">全员</option>
              ${users.map(u => `<option value="${esc(u.id)}">${esc(u.name)}</option>`).join('')}</select></div>
        </div>
        <div style="margin-top:10px"><label class="small muted">任务说明</label>
          <textarea id="t-desc" rows="3" placeholder="任务背景、截止时间、验收标准…"></textarea></div>
        <div style="margin-top:10px"><label class="small muted">资料附件（文档 / 图片 / 视频）</label>
          <input type="file" id="t-files" multiple style="max-width:340px"></div>
        <div style="margin-top:12px"><label class="small muted">任务子项</label>
          <div id="item-rows"></div>
          <button class="btn sm" id="add-item" style="margin-top:6px">+ 添加子项</button></div>
        <div class="row" style="margin-top:12px">
          <button class="btn primary" id="t-submit">发布任务</button>
        </div>
      </div></div>` : '';
  return `${pubCard}
    <div id="tasks-list">${tasks.map(taskCard).join('') ||
      '<div class="empty" style="margin-top:16px">还没有任务</div>'}</div>`;
}

function bindTasks() {
  let rowN = 0;
  const rows = document.getElementById('item-rows');
  const addItemRow = (data) => {
    if (!rows) return;
    const n = rowN++;
    const div = document.createElement('div');
    div.style.cssText = 'border:1px solid var(--line-2);border-radius:8px;padding:10px;margin-top:8px';
    const itype = (data && data.itype) || 'general';
    div.innerHTML = `
      <div class="row" style="gap:8px">
        <select data-itype="${n}" style="max-width:150px">
          <option value="question" ${itype === 'question' ? 'selected' : ''}>问题（写给导师）</option>
          <option value="requirement" ${itype === 'requirement' ? 'selected' : ''}>需求</option>
          <option value="general" ${itype === 'general' ? 'selected' : ''}>其他</option>
        </select>
        <input type="text" data-ititle="${n}" placeholder="子项标题，如：确认需求文档" style="flex:1" value="${esc((data && data.title) || '')}">
        <button class="btn sm" data-rowdel="${n}">删</button>
      </div>
      <textarea data-icontent="${n}" rows="2" placeholder="说明：要回答的问题 / 需求要点 / 要做的事" style="margin-top:6px">${esc((data && data.content) || '')}</textarea>`;
    div.querySelector('[data-rowdel]').onclick = () => div.remove();
    rows.appendChild(div);
  };
  const addBtn = document.getElementById('add-item');
  if (addBtn) { addBtn.onclick = addItemRow; addItemRow(); }

  const aiBtn = document.getElementById('t-ai-btn');
  if (aiBtn) aiBtn.onclick = async () => {
    const desc = document.getElementById('t-ai').value.trim();
    if (!desc) { toast('请先粘贴需求描述'); return; }
    aiBtn.disabled = true;
    aiBtn.textContent = '生成中…';
    const r = await post('/api/tasks/ai-draft', { description: desc });
    aiBtn.disabled = false;
    aiBtn.textContent = '✨ AI 起草';
    if (r.error) { toast(r.error); return; }
    const dr = r.draft || {};
    document.getElementById('t-title').value = dr.title || '';
    document.getElementById('t-desc').value = dr.description || '';
    if (rows) rows.innerHTML = '';
    rowN = 0;
    (dr.items || []).forEach(it => addItemRow(it));
    toast(r.mode === 'llm' ? 'AI 草稿已填入，请检查修改后发布' : '已按行拆成草稿（未接大模型），请修改后发布');
  };

  const tSubmit = document.getElementById('t-submit');
  if (tSubmit) tSubmit.onclick = async () => {
    const fd = new FormData();
    fd.append('user', state.user);
    fd.append('title', document.getElementById('t-title').value);
    fd.append('description', document.getElementById('t-desc').value);
    fd.append('assignee', document.getElementById('t-assignee').value);
    for (const f of document.getElementById('t-files').files) fd.append('files', f);
    document.querySelectorAll('[data-ititle]').forEach(inp => {
      const n = inp.dataset.ititle;
      fd.append('item_title_' + n, inp.value);
      const tsel = document.querySelector('[data-itype="' + n + '"]');
      fd.append('item_itype_' + n, tsel ? tsel.value : 'general');
      const cta = document.querySelector('[data-icontent="' + n + '"]');
      fd.append('item_content_' + n, cta ? cta.value : '');
    });
    const r = await postForm('/api/tasks', fd);
    toast(r.error ? r.error : '任务已发布');
    if (!r.error) render();
  };

  document.querySelectorAll('[data-fb]').forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.fb;
      const fd = new FormData();
      fd.append('user', state.user);
      fd.append('task_id', btn.dataset.task);
      fd.append('item_id', id);
      fd.append('content', document.getElementById('fb-text-' + id).value);
      for (const f of document.getElementById('fb-files-' + id).files) fd.append('files', f);
      const r = await postForm('/api/tasks/feedback', fd);
      toast(r.error ? r.error : '反馈已提交');
      if (!r.error) render();
    };
  });
  document.querySelectorAll('[data-confirm]').forEach(btn => {
    btn.onclick = async () => {
      const note = prompt('导师点评（可留空）：') || '';
      const r = await post('/api/feedback/confirm', { id: +btn.dataset.confirm, note });
      toast(r.error ? r.error : '已确认');
      if (!r.error) render();
    };
  });
  document.querySelectorAll('[data-del]').forEach(btn => {
    btn.onclick = async () => {
      if (!confirm('删除这个任务及其全部子项、反馈与附件？')) return;
      const r = await post('/api/tasks/delete', { id: +btn.dataset.del });
      toast(r.error ? r.error : '已删除');
      if (!r.error) render();
    };
  });
}

/* ---------------------------------------------------------------- 视图：周报 */

async function viewWeekly() {
  const [mine, all] = await Promise.all([
    get('/api/weekly?user=' + encodeURIComponent(state.user)),
    get('/api/weekly/all'),
  ]);
  const mineList = mine.mine || [];
  const allList = all.all || [];

  const itemHtml = (i) => `<div class="kv" style="align-items:flex-start">
    <b>${esc(i.name)} · ${i.score}/5</b>
    <span>${esc(i.reason || '—')}</span></div>`;

  const mineHtml = mineList.map(r => `
    <details class="card" style="margin-top:10px">
      <summary style="cursor:pointer;padding:10px 14px">
        <b>${esc(r.week_label)}</b>
        <span class="tag ${r.score >= 80 ? 'ok' : r.score >= 60 ? 'warn' : 'bad'}">${r.score != null ? r.score + ' 分' : '未评分'}</span>
        <span class="muted small">${esc(r.created_at)}</span></summary>
      <div class="card-body">
        <div class="md">${md(r.content)}</div>
        ${(r.review && r.review.items) ? `<div style="margin-top:10px">${r.review.items.map(itemHtml).join('')}</div>` : ''}
      </div>
    </details>`).join('');

  const allHtml = allList.slice(0, 30).map(r => `
    <div class="kv" style="align-items:flex-start">
      <b>${esc(r.user_name)}</b>
      <span>${esc(r.week_label)}
        <span class="tag ${r.score >= 80 ? 'ok' : r.score >= 60 ? 'warn' : 'bad'}">${r.score != null ? r.score + ' 分' : '—'}</span>
        <details style="margin-top:4px"><summary class="small muted" style="cursor:pointer">查看内容</summary>
          <div class="md" style="margin-top:6px">${md(r.content)}</div></details>
      </span>
    </div>`).join('');

  const composeCard = ROLE === 'newcomer' ? `
    <div class="card"><div class="card-head"><h3>写本周周报</h3>
        <span class="muted small">提交后按周报标准自动批改，并计入成长记录</span></div>
      <div class="card-body">
        <div><label class="small muted">周次（留空自动按当前周编号）</label>
          <input type="text" id="w-label" placeholder="如：2026 年第 41 周"></div>
        <div style="margin-top:10px"><label class="small muted">周报内容</label>
          <textarea id="w-content" rows="7"
            placeholder="建议结构：① 本周完成了什么（结果 + 数据）② 遇到什么问题、怎么处理的 ③ 下周计划"></textarea></div>
        <div class="row" style="margin-top:10px">
          <button class="btn primary" id="w-submit">提交周报</button>
        </div>
        <div id="w-result" style="margin-top:12px"></div>
      </div></div>` : '';
  return `${composeCard}
    <div class="card" style="margin-top:16px"><div class="card-head"><h3>我的周报历史</h3>
        <span class="muted small">${mineList.length} 篇</span></div>
      <div class="card-body">${mineHtml || '<div class="muted small">还没有提交过周报</div>'}</div></div>
    <div class="card" style="margin-top:16px"><div class="card-head"><h3>全员周报</h3>
        <span class="muted small">最近 ${Math.min(30, allList.length)} 篇</span></div>
      <div class="card-body">${allHtml || '<div class="muted small">暂无</div>'}</div></div>`;
}

function bindWeekly() {
  const wSubmit = document.getElementById('w-submit');
  if (wSubmit) wSubmit.onclick = async () => {
    const content = document.getElementById('w-content').value.trim();
    if (!content) { toast('周报内容不能为空'); return; }
    const box = document.getElementById('w-result');
    box.innerHTML = '<span class="spinner"></span> 正在提交并批改…';
    const r = await post('/api/weekly', {
      user: state.user,
      content,
      week_label: document.getElementById('w-label').value.trim(),
    });
    if (r.error) { box.innerHTML = `<div class="hint bad">${esc(r.error)}</div>`; return; }
    const rv = r.review || {};
    box.innerHTML = `
      <div class="row" style="align-items:center;gap:16px">
        ${r.score != null ? ring(r.score) : ''}
        <div><div style="font-weight:650">已存档 · ${esc(r.week_label)}</div>
          <div class="muted small">批改方式：${r.mode === 'llm' ? '大模型' : r.mode === 'heuristic' ? '本地启发式' : '无'}${rv.summary ? ' · ' + esc(rv.summary) : ''}</div></div>
      </div>
      ${(rv.items || []).map(i => `<div class="kv" style="align-items:flex-start">
        <b>${esc(i.name)} · ${i.score}/5</b><span>${esc(i.reason || '—')}</span></div>`).join('')}`;
    render();
  };
}

/* ---------------------------------------------------------------- 渲染 */

const VIEWS = {
  ask: [viewAsk, bindAsk], tasks: [viewTasks, bindTasks], weekly: [viewWeekly, bindWeekly],
  path: [viewPath, bindPath], coach: [viewCoach, bindCoach],
  env: [viewEnv, bindEnv], profile: [viewProfile, () => {}],
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

/* ---------------------------------------------------------------- 登录（可选身份保护） */

function showLogin(msg, prefUser) {
  const mask = document.getElementById('login-mask');
  if (!mask) return;
  const users = ((state.meta && state.meta.users) || [])
    .filter(u => (u.role || 'newcomer') === ROLE);   // 登录页只出现本端角色的账号
  const sel = document.getElementById('login-user');
  sel.innerHTML = users.map(u =>
    `<option value="${esc(u.id)}">${esc(u.name)}（${ROLE === 'mentor' ? '导师' : esc(u.track || '通用')}）</option>`).join('');
  if (prefUser) sel.value = prefUser;
  document.getElementById('login-msg').textContent =
    msg || (ROLE === 'mentor' ? '请选择导师账号并输入 PIN' : '请选择新人账号并输入 PIN');
  document.getElementById('login-pin').value = '';
  mask.classList.remove('hidden');
  setTimeout(() => document.getElementById('login-pin').focus(), 60);
}

async function doLogin() {
  const user = document.getElementById('login-user').value;
  const pin = document.getElementById('login-pin').value.trim();
  const r = await post('/api/login', { user, pin });
  if (r.error) {
    document.getElementById('login-msg').textContent = r.error;
    return;
  }
  if ((r.user.role || 'newcomer') !== ROLE) {
    document.getElementById('login-msg').textContent =
      ROLE === 'mentor' ? '这是新人账号，请回新人端登录' : '这是导师账号，请去导师端登录';
    return;
  }
  state.token = r.token;
  state.user = r.user.id;
  localStorage.setItem('mentor.token', state.token);
  localStorage.setItem('mentor.user', state.user);
  document.getElementById('login-mask').classList.add('hidden');
  toast('欢迎，' + r.user.name);
  state.chat = []; state.coach = null;
  await boot();
}

async function boot() {
  state.meta = await get('/api/meta');
  if (state.meta.error) {
    document.getElementById('view').innerHTML = '<div class="hint bad">无法连接后端：' + esc(state.meta.error) + '</div>';
    return;
  }
  const users = state.meta.users || [];
  const me = users.find(u => u.id === state.user);
  const sel = document.getElementById('user-select');
  const whoEl = document.getElementById('whoami');
  const outEl = document.getElementById('logout-btn');
  const openEl = document.getElementById('identity-open');
  if (state.meta.auth_required) {
    // 双端分离：token 对应的账号必须与本端角色一致，否则视为未登录
    if (!state.token || !me || (me.role || 'newcomer') !== ROLE) {
      if (state.token) { state.token = ''; localStorage.removeItem('mentor.token'); }
      showLogin();
      return;
    }
    if (whoEl && outEl && openEl) {
      whoEl.textContent = me.name + '（' + (me.role === 'mentor' ? '导师' : '新人') + '）';
      whoEl.classList.remove('hidden');
      outEl.classList.remove('hidden');
      openEl.style.display = 'none';
      outEl.onclick = async () => {
        try { await post('/api/logout', {}); } catch (e) {}
        state.token = '';
        localStorage.removeItem('mentor.token');
        state.chat = []; state.coach = null;
        showLogin('已退出，请重新登录');
      };
    }
  } else if (sel) {
    // 开放模式（users.json 里没人配 pin）：保留自由切换
    if (!me) state.user = users.length ? users[0].id : 'demo';
    sel.innerHTML = users.map(u =>
      `<option value="${esc(u.id)}" ${u.id === state.user ? 'selected' : ''}>${esc(u.name)}（${esc(u.track || '通用')}）</option>`).join('');
    sel.onchange = () => {
      state.user = sel.value;
      localStorage.setItem('mentor.user', state.user);
      state.chat = []; state.coach = null;
      toast('已切换到 ' + sel.options[sel.selectedIndex].text);
      render();
    };
  }
  document.querySelectorAll('.nav-item').forEach(b => {
    b.onclick = () => { state.view = b.dataset.view; render(); };
  });
  renderBadges();
  render();
}

document.getElementById('login-btn').onclick = doLogin;
document.getElementById('login-pin').addEventListener('keydown', e => {
  if (e.key === 'Enter') doLogin();
});

boot();
