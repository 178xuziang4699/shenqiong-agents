/* 单页看板：只做取数与渲染，不含业务逻辑。 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  sid: null, health: null, view: 'home', tab: 'chat',
  channel: 'chat',                                  // 当前沙箱通道：chat | work
  sidByChannel: { chat: null, work: null },         // 每个通道独立会话，历史互不串
  catalog: null, catTab: 'skills', catFilter: '全部', engine: null,
  activeSkill: null,
};

const CHANNEL_LABEL = { chat: '问答', work: '办事' };

/* ---------------- 基础工具 ---------------- */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/* 独立管理页地址：发布通道要求携带 sharecode，静态链接与动态气泡共用 */
function adminHref() {
  const s = new URLSearchParams(location.search).get('sharecode');
  return '/admin' + (s ? '?sharecode=' + encodeURIComponent(s) : '');
}
$$('[data-admin-link]').forEach(a => { a.href = adminHref(); });

/* 内联 SVG 图标：不依赖 emoji 字体，避免无字体环境显示空方框 */
const ICON_PATHS = {
  text:   '<path d="M4 5h16M4 10h16M4 15h10M4 20h7"/><path d="M17 16.5l2.2 2.2 3-3.4"/>',
  table:  '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M9 4v16M15 4v16"/>',
  lock:   '<rect x="4" y="10" width="16" height="11" rx="2.5"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 15v2"/>',
  file:   '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  gear:   '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v2.2M12 19.3v2.2M4.2 4.2l1.6 1.6M18.2 18.2l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.2 19.8l1.6-1.6M18.2 5.8l1.6-1.6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/>',
  brain:  '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8A3 3 0 0 0 8 18a3 3 0 0 0 4 2V4.5A2.5 2.5 0 0 0 9 4zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5.8A3 3 0 0 1 16 18a3 3 0 0 1-4 2"/>',
  plug:   '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-6 6 6 6 0 0 1-6-6zM12 17v4"/>',
  image:  '<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="M4 17l5-5 3.5 3.5L16 12l4 4"/>',
  clock:  '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  chart:  '<path d="M4 20h16M6 17v-5M11 17V7M16 17v-8M20 17V4"/>',
  code:   '<path d="M8.5 7L4 12l4.5 5M15.5 7L20 12l-4.5 5M13.5 4l-3 16"/>',
  shield: '<path d="M12 2.5l8 3v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10v-6z"/><path d="M9 12l2 2 4-4"/>',
  globe:  '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 3.8 5.6 3.8 9S14.5 18.5 12 21c-2.5-2.5-3.8-5.6-3.8-9S9.5 5.5 12 3z"/>',
  calc:   '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M7 7h10M7.5 12h.01M12 12h.01M16.5 12h.01M7.5 16h.01M12 16h.01M16.5 16h.01"/>',
  cloud:  '<path d="M7 18a4 4 0 0 1-.5-8A5.5 5.5 0 0 1 17 9.5a3.8 3.8 0 0 1 .5 8.5z"/><path d="M10 14h4M12 12v4"/>',
  spark:  '<path d="M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4z"/><path d="M18.5 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
};
function iconSvg(key, size = 20) {
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none"
    stroke="currentColor" stroke-width="1.7" stroke-linecap="round"
    stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[key] || ICON_PATHS.spark}</svg>`;
}

function t(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

function stTag(s) {
  const cls = /^(done|ok)$/.test(s) ? 'done'
    : /^(failed|error)$/.test(s) ? 'failed'
    : /^(running|pending)$/.test(s) ? 'running'
    : 'skipped';
  return `<span class="st ${cls}">${esc(s)}</span>`;
}

function asArray(v) {
  if (Array.isArray(v)) return v;
  if (typeof v === 'string' && v.trim()) {
    try { const p = JSON.parse(v); return Array.isArray(p) ? p : []; } catch (e) { return []; }
  }
  return [];
}

function jblock(obj, max = 900) {
  if (obj === null || obj === undefined) return '';
  let s;
  try { s = JSON.stringify(obj, null, 2); } catch (e) { s = String(obj); }
  if (s.length > max) s = s.slice(0, max) + `\n…（已截断，共 ${s.length} 字符）`;
  return `<details><summary>输入 / 输出 / 错误</summary><pre>${esc(s)}</pre></details>`;
}

async function api(path, opt = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opt });
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch (e) { body = { raw: text }; }
  if (!res.ok) {
    const d = body.detail || body;
    const err = new Error(typeof d === 'string' ? d : (d.message || JSON.stringify(d)));
    err.payload = d;
    err.status = res.status;
    throw err;
  }
  return body;
}

/* ---------------- 视图 / 标签切换 ---------------- */
function setView(view) {
  state.view = view;
  $$('.view').forEach(v => v.classList.toggle('on', v.id === `view-${view}`));
  // 手机端顶栏高亮同步（桌面端该元素为 display:none，无影响）
  $$('#m-topbar .m-nav button').forEach(b =>
    b.classList.toggle('on', b.dataset.mview === (view === 'catalog' ? 'catalog' : 'home')));
  const fabs = $('.fabs');
  if (fabs) fabs.style.display = view === 'home' ? 'flex' : 'none';
  if (view === 'home') {
    $$('#nav .nav-item').forEach(b => b.classList.toggle('on', b.dataset.view === 'home'));
    setTimeout(() => $('#msg-home')?.focus(), 50);
  }
  hidePopovers();
}

function setTab(tab) {
  state.tab = tab;
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  $$('.panel').forEach(p => p.classList.toggle('on', p.dataset.panel === tab));
  if (tab === 'diag') loadLogs();
  if (tab === 'library') loadLibrary();
  if (tab === 'reg') { loadTools(); loadSkills(); }
}

function gotoSession(tab = 'chat') {
  setView('session');
  setTab(tab);
  $$('#nav .nav-item').forEach(b => b.classList.remove('on'));
}

$$('#tabs button').forEach(btn => { btn.onclick = () => setTab(btn.dataset.tab); });
$('#btn-back').onclick = () => setView('home');

/* ---------------- 健康度 ---------------- */
async function loadHealth() {
  const box = $('#health');
  try {
    const h = await api('/health');
    state.health = h;
    box.innerHTML = `<span class="dot"></span>运行中 · ${h.agents} Agent · ${h.tools} 工具 · ${h.skills} 技能`;
    $('#pop-avatar').innerHTML = `<div class="pop-title">运行状态</div>
      <div style="padding:2px 8px 6px;font-size:12px;line-height:1.9;color:var(--fg-2)">
        Agent <b>${h.agents}</b> · MCP 工具 <b>${h.tools}</b> · 技能 <b>${h.skills}</b><br>
        会话 <b>${h.sessions}</b> · 存储 <b>SQLite</b><br>
        确认模式 <b>${esc(h.confirm_mode || 'approve')}</b>（${h.confirm_mode === 'prompt' ? '终端人工确认' : '自动放行'}）
      </div>`;
  } catch (e) {
    box.innerHTML = `<span class="dot bad"></span>连不上后端：${esc(e.message)}`;
  }
}

/* ---------------- 模型 / 我的 API 接入状态 ---------------- */
let presetsCache = null;

async function loadPresets() {
  if (!presetsCache) {
    try { presetsCache = await api('/api/llm/presets'); }
    catch (e) { presetsCache = []; }
  }
  return presetsCache;
}

async function loadModel() {
  const bar = $('#model-bar');
  if (!bar) return;
  try {
    const m = await api('/api/llm');
    state.engine = m;
    const cpModel = $('#cp-model');
    if (cpModel) cpModel.textContent = '模型：' + (m.enabled ? (m.model || '已接入') : '离线应答');
    // 服务重启后从持久化配置恢复，自动做一次真实连通性探测，避免状态条误显「尚未验证」
    if (m.enabled && m.verified !== true && !state.llmAutoTested) {
      state.llmAutoTested = true;
      api('/api/llm/test', { method: 'POST' }).then(() => loadModel()).catch(() => {});
    }
    const live = m.enabled && m.verified === true;
    const homeLbl = $('#home-api-label');
    if (homeLbl) homeLbl.textContent = live ? '模型已接入 · 管理' : '添加我的 API';
    const homeBtn = $('#btn-home-api');
    if (homeBtn) homeBtn.classList.toggle('live', live);
    const presets = await loadPresets();
    const ok = m.enabled && m.verified === true;
    if (state.llmOpen === undefined) state.llmOpen = false;
    const models = m.available_models || [];
    const opts = models.length
      ? '<select id="model-select" title="切换本次运行使用的模型">' +
        (models.includes(m.model) ? '' : `<option value="${esc(m.model)}" selected>${esc(m.model)}</option>`) +
        models.map(n => `<option value="${esc(n)}" ${n === m.model ? 'selected' : ''}>${esc(n)}</option>`).join('') +
        '</select>'
      : '';
    const status = !m.enabled ? '离线规则应答'
      : ok ? '已接入 ' + m.model
      : (m.verify_error ? '连接失败：' + String(m.verify_error).slice(0, 70)
                        : '已配置 ' + m.model + '（尚未验证）');
    const presetOpts = presets.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
    const cfgBtn = `<button id="btn-llm-toggle" class="mb-toggle">${state.llmOpen ? '收起配置' : '＋ 添加我的 API'}</button>`;
    const panel = state.llmOpen ? `
      <div class="mb-panel">
        <div class="mb-grid">
          <label>服务商<select id="llm-preset">${presetOpts}</select></label>
          <label>服务地址<input id="llm-url" placeholder="https://api.deepseek.com/v1" value="${esc(m.enabled && m.base_url !== '-' ? m.base_url : '')}"></label>
          <label>模型名<input id="llm-model" placeholder="deepseek-chat（Ollama 可留空自动选型）" value="${esc(m.enabled ? m.model : '')}"></label>
          <label>API Key<input id="llm-key" type="password" autocomplete="off" placeholder="${m.has_api_key ? '已配置，留空表示不修改' : 'sk-...（Ollama 不需要）'}"></label>
        </div>
        <div class="mb-actions">
          <button id="btn-llm-save" class="primary">保存并测试连接</button>
          <button id="btn-llm-clear">恢复离线</button>
        </div>
        <div class="mb-hint" id="llm-hint"></div>
        <div class="mb-hint">密钥只存本次服务进程内存，不写文件、不入库、不进日志；想重启后仍生效，把 AP_LLM_BASE_URL / AP_LLM_API_KEY / AP_LLM_MODEL 写进项目根目录的 .env。</div>
      </div>` : '';
    const msgLine = state.llmMsg
      ? `<div class="mb-msg ${state.llmMsg.type}">${esc(state.llmMsg.text)}</div>` : '';
    bar.innerHTML = `
      <div class="mb-line">
        <span class="mb-dot ${ok ? 'ok' : 'off'}"></span>
        <b>${esc(status)}</b>
        <span class="mb-sub">${esc(m.enabled ? m.base_url + (m.has_api_key ? ' · 密钥已填' : '') : '未配置模型服务')}</span>
        ${opts}${cfgBtn}
      </div>
      <div class="mb-note">${m.auto_reason ? esc(m.auto_reason) + ' · ' : ''}${ok
        ? '多轮对话由你的模型直接回应，历史会落盘并支持刷新回放'
        : '填入服务地址与 API Key，点「保存并测试连接」即可接入；未接入时使用离线应答'}</div>
      ${msgLine}${panel}`;

    const sel = $('#model-select');
    if (sel) sel.onchange = async () => {
      try {
        await api('/api/llm/model', { method: 'POST', body: JSON.stringify({ model: sel.value }) });
        await loadModel();
      } catch (e) { alert('切换模型失败：' + e.message); }
    };
    const toggle = $('#btn-llm-toggle');
    if (toggle) toggle.onclick = () => { state.llmMsg = null; state.llmOpen = !state.llmOpen; loadModel(); };

    const psel = $('#llm-preset');
    if (psel) {
      const applyPreset = () => {
        const p = presets.find(x => x.id === psel.value) || {};
        if (p.base_url !== undefined) $('#llm-url').value = p.base_url;
        $('#llm-model').value = p.model || '';
        $('#llm-hint').textContent = p.hint || '';
        psel.dataset.provider = p.provider || 'auto';
      };
      psel.value = (m.enabled && m.provider === 'ollama') ? 'ollama' : 'deepseek';
      applyPreset();
      psel.onchange = applyPreset;
      // 已接入过时以实际配置为准，不被预设覆盖
      if (m.enabled && m.base_url !== '-') $('#llm-url').value = m.base_url;
      if (m.enabled) $('#llm-model').value = m.model || '';
    }

    const save = $('#btn-llm-save');
    if (save) save.onclick = async () => {
      const url = ($('#llm-url')?.value || '').trim();
      const key = ($('#llm-key')?.value || '').trim();
      const model = ($('#llm-model')?.value || '').trim();
      const provider = psel?.dataset.provider || 'auto';
      if (!url) { alert('请先填写服务地址，或先选一个服务商预设'); return; }
      save.disabled = true; save.textContent = '测试中…';
      try {
        await api('/api/llm/config', { method: 'POST', body: JSON.stringify({
          base_url: url, provider, model, api_key: key }) });
        const t = await api('/api/llm/test', { method: 'POST' });
        state.llmMsg = t.ok
          ? { type: 'ok', text: `连接成功：${t.model} 已可用，可以直接开始对话` }
          : { type: 'err', text: '连接失败：' + (t.error || '未知原因') };
        if (t.ok) state.llmOpen = false;
      } catch (e) {
        state.llmMsg = { type: 'err', text: '配置失败：' + e.message };
      }
      await loadModel();
    };

    const clear = $('#btn-llm-clear');
    if (clear) clear.onclick = async () => {
      await api('/api/llm/clear', { method: 'POST' });
      state.llmMsg = null; state.llmOpen = true;
      await loadModel();
    };
  } catch (e) {
    bar.innerHTML = `<span class="mb-dot off"></span><b>模型状态未知</b><span class="mb-sub">${esc(e.message)}</span>`;
  }
}

/* ---------------- 会话列表 ---------------- */
let sessionsSeq = 0;

async function loadSessions() {
  const ul = $('#sessions');
  // 竞态保护：发完消息立刻切通道时，旧通道的响应后到也不能覆盖新通道的列表。
  // 只接受「最后一次请求」且「请求时通道仍等于当前通道」的结果。
  const seq = ++sessionsSeq;
  const ch = state.channel;
  let rows = [];
  // 沙箱：只拉取当前通道的会话，两套通道的历史在界面上也不串
  try { rows = await api(`/api/sessions?limit=40&channel=${ch}`); } catch (e) {
    if (seq !== sessionsSeq || ch !== state.channel) return;
    ul.innerHTML = `<li class="empty err">${esc(e.message)}</li>`;
    return;
  }
  if (seq !== sessionsSeq || ch !== state.channel) return;
  if (!rows.length) {
    ul.innerHTML = `<li class="empty">${CHANNEL_LABEL[ch]}通道还没有会话，发一条消息即可新建。</li>`;
    return;
  }
  ul.innerHTML = rows.map(r => `
    <li data-sid="${esc(r.session_id)}" data-channel="${esc(r.channel || 'chat')}" class="${r.session_id === state.sid ? 'on' : ''}">
      <div class="sid"><span class="txt">${esc(r.session_id)}</span>
        <button class="del-btn" data-del="${esc(r.session_id)}" title="删除该会话">&#128465;</button>
        <span class="st ${r.status === 'completed' ? 'done' : r.status === 'failed' ? 'failed' : 'running'}">${esc(r.status)}</span>
      </div>
      <div class="meta">${CHANNEL_LABEL[r.channel] || r.channel || '问答'}通道 · ${r.step_index} 步 · ${esc((r.intent || '').slice(0, 24))} · ${esc(t(r.updated_at))}</div>
    </li>`).join('');
  $$('#sessions li[data-sid]').forEach(li => {
    li.onclick = () => {
      const c = li.dataset.channel;
      if (c && c !== state.channel) setChannel(c);
      openSession(li.dataset.sid);
    };
  });
  $$('#sessions button[data-del]').forEach(b => {
    b.onclick = (e) => { e.stopPropagation(); delSession(b.dataset.del); };
  });
  applySearchFilter();
}

/* 删除一个会话（带确认），删除当前会话则同时清空对话区 */
async function delSession(sid) {
  if (!sid) return;
  if (!confirm('删除该会话及其全部记录？此操作不可撤销。')) return;
  try {
    await api(`/api/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE' });
    if (state.sid === sid) {
      state.sid = null;
      $('#crumb-sid').textContent = 'session: -';
      $('#answer').innerHTML = '';
    }
    Object.keys(state.sidByChannel).forEach(c => {
      if (state.sidByChannel[c] === sid) state.sidByChannel[c] = null;
    });
    await loadSessions();
  } catch (e) { alert('删除失败：' + e.message); }
}

function markActive() {
  $$('#sessions li[data-sid]').forEach(li => {
    li.classList.toggle('on', li.dataset.sid === state.sid);
  });
}

async function openSession(sid, tab = 'chat') {
  state.sid = sid;
  state.sidByChannel[state.channel] = sid;
  $('#crumb-sid').textContent = `session: ${sid}`;
  $('#side-foot')?.setAttribute('data-sid', sid);
  markActive();
  gotoSession(tab);
  await restoreChat(sid);
  await Promise.allSettled([loadTasks(), loadChain(), loadFlow(), loadTools(), loadSkills()]);
}

/* 从落盘快照回放该会话的完整对话，刷新页面后多轮记录不丢 */
async function restoreChat(sid) {
  const box = $('#answer');
  box.innerHTML = '<div class="empty">正在回放对话…</div>';
  try {
    const d = await api(`/api/sessions/${encodeURIComponent(sid)}/replay`);
    const chain = d.chain || [];
    // 办事轮次的自然语言收口也写入了快照，刷新后同样能回放（两个通道通用）
    const turns = chain.filter(s =>
      (s.phase === 'chat.respond' || s.phase === 'task.summarize') &&
      s.output && s.output.answer && s.input && s.input.text);
    const first = chain.find(s => s.input && s.input.text);
    if (first) {
      const t = $('#crumb-title');
      if (t) t.textContent = String(first.input.text).replace(/\s+/g, ' ').slice(0, 26) || '新对话';
    }
    if (!turns.length) {
      box.innerHTML = chain.some(s => s.phase === 'plan')
        ? '<div class="empty">这是办事会话：任务计划与执行结果见「任务与作业」「执行链」页。</div>'
        : '<div class="empty">这个会话还没有对话记录，发一条消息开始聊。</div>';
      return;
    }
    box.innerHTML = turns.map(s => {
      const o = s.output || {};
      return renderTurn(s.input?.text || '', {
        mode: 'chat', answer: o.answer, citations: o.citations || [],
        engine: o.engine || null, skill: o.skill, skill_raw: o.skill_raw,
        office: o.office, artifacts: o.artifacts || [], elapsed_ms: o.elapsed_ms || 0,
      }, null);
    }).join('');
    bindArtifactActions(box);
    hydrateOfficeCards(box);
    bindWbActions(box);
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    box.innerHTML = `<div class="empty err">回放失败：${esc(e.message)}</div>`;
  }
}

/* 用户消息：右侧浅灰圆角小气泡，无「你」标签、无边框 */
function bubbleUser(text) { return wbUser(text); }
function wbUser(text) {
  return `<div class="wb-user"><div class="wb-user-bubble">${esc(text)}</div></div>`;
}

/* ---------------- WorkBuddy 风格：思考说明 + 逐条执行过程 ---------------- */
function elapsedZh(ms) {
  if (!ms || ms < 0) return '已完成';
  if (ms < 1000) return '已思考 ' + ms + 'ms';
  const s = ms / 1000;
  if (s < 60) return '已思考 ' + s.toFixed(1) + 's';
  return '已思考 ' + Math.floor(s / 60) + 'm' + Math.round(s % 60) + 's';
}

/* 步骤全部来自本轮真实发生的动作，不编造未执行的动作 */
function buildMeta(j) {
  const off = j.office || null;
  const skill = j.skill || null;
  const cites = j.citations || [];
  const eng = j.engine || {};
  const plan = j.plan || [];
  const steps = [];
  const push = (t, s) => steps.push({ t, s: s || 'done' });
  let thought = '';
  push('理解你的问题');
  if (off) {
    const zh = off.doc_type_zh || '文档';
    const topic = off.topic || off.title || '这个主题';
    thought = `明白，这次要产出一份<b>${esc(zh)}</b>，主题是「${esc(topic)}」。我先定结构、再逐节展开，最后保存成文件给你。`;
    push(`识别办公需求：${zh}`);
    if (off.topic) push(`确定主题：${off.topic}`);
    const byModel = /模型|deepseek|gpt|qwen|claude/i.test(off.source_zh || '');
    push(byModel ? `调用 ${esc(eng.model || '已接入模型')} 撰写全文` : '按结构化模板撰写全文');
    if (off.path) push(`保存文件 ${off.path}`);
    push('整理交付');
  } else if (skill) {
    thought = `收到，用<b>【${esc(skill)}】</b>来处理这段内容，我把你的输入填进主参数后直接执行，结果整理成可读的文字。`;
    push(`加载技能：${skill}`);
    push('把输入填入技能主参数');
    push('执行技能并核对结果');
  } else if (plan.length) {
    thought = `明白，这件事需要拆成 <b>${plan.length} 个子任务</b>，我按依赖关系依次派发，每一步都留痕，方便回放和诊断。`;
    plan.forEach(p => push(`${p.task_id} ${p.title}`,
      p.status === 'failed' ? 'fail' : p.status === 'skipped' ? 'skip' : 'done'));
    push('汇总执行结果');
  } else {
    thought = `明白，我先<b>理解你的意思</b>，再给出贴题的回答，不会把话题带偏。`;
    if (cites.length) push(`检索资料库，命中 ${cites.length} 条依据`);
    push(eng && eng.enabled ? `调用 ${esc(eng.model || '已接入模型')} 生成回复` : '使用本地离线应答');
    push('整理回复');
  }
  return { thought, steps, elapsed: elapsedZh(j.elapsed_ms) };
}

const WB_AVATAR = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.5l1.8 5.2 5.2 1.8-5.2 1.8L12 16.5l-1.8-5.2L5 9.5l5.2-1.8z"/><path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg>`;

function metaHtml(j, pending) {
  if (pending) {
    return `<div class="wb-head"><span class="wb-avatar">${WB_AVATAR}</span><b>WorkBuddy</b>
      <span class="wb-elapsed">正在思考…</span></div>
      <div class="wb-thought">我先理解你的需求，再决定直接回答、写文档，还是拆成子任务执行。</div>
      <div class="wb-proc"><div class="wb-proc-title">执行过程<span class="wb-caret">⌄</span></div>
        <div class="wb-steps"><div class="wb-step running"><span class="wb-si dot"></span><span>理解你的问题</span></div></div>
      </div>`;
  }
  const m = buildMeta(j);
  const rows = m.steps.map(s => {
    const ic = s.s === 'fail' ? '✕' : s.s === 'skip' ? '–' : '✓';
    return `<div class="wb-step ${s.s}"><span class="wb-si">${ic}</span><span>${esc(s.t)}</span></div>`;
  }).join('');
  return `<div class="wb-head"><span class="wb-avatar">${WB_AVATAR}</span><b>WorkBuddy</b>
      <span class="wb-elapsed">${esc(m.elapsed)}</span></div>
    <div class="wb-thought">${m.thought}</div>
    <details class="wb-proc" open>
      <summary>执行过程 · ${m.steps.length} 个步骤<span class="wb-caret">⌄</span></summary>
      <div class="wb-steps">${rows}</div>
    </details>`;
}

/* ---------------- Markdown → HTML（把交付文档渲染成可读正文） ---------------- */
function mdInline(s) {
  return esc(s || '')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function mdToHtml(text) {
  const out = [];
  let inUl = false, inOl = false, inTable = false;
  const closeLists = () => {
    if (inUl) { out.push('</ul>'); inUl = false; }
    if (inOl) { out.push('</ol>'); inOl = false; }
  };
  const closeTable = () => { if (inTable) { out.push('</tbody></table>'); inTable = false; } };
  for (const raw of String(text || '').split('\n')) {
    const l = raw.trim();
    if (!l) { closeLists(); closeTable(); continue; }
    if (l.startsWith('|') && l.endsWith('|')) {
      closeLists();
      const cells = l.slice(1, -1).split('|').map(c => c.trim());
      if (cells.length && cells.every(c => /^:?-{2,}:?$/.test(c))) continue;
      if (!inTable) {
        out.push('<table><thead><tr>' + cells.map(c => `<th>${mdInline(c)}</th>`).join('') + '</tr></thead><tbody>');
        inTable = true;
      } else {
        out.push('<tr>' + cells.map(c => `<td>${mdInline(c)}</td>`).join('') + '</tr>');
      }
      continue;
    }
    closeTable();
    if (l.startsWith('### ')) { closeLists(); out.push('<h4>' + mdInline(l.slice(4)) + '</h4>'); continue; }
    if (l.startsWith('## ')) { closeLists(); out.push('<h3>' + mdInline(l.slice(3)) + '</h3>'); continue; }
    if (l.startsWith('# ')) { closeLists(); out.push('<h2>' + mdInline(l.slice(2)) + '</h2>'); continue; }
    if (l.startsWith('> ')) { closeLists(); out.push('<blockquote>' + mdInline(l.slice(2)) + '</blockquote>'); continue; }
    if (l.startsWith('```')) { closeLists(); continue; }
    if (/^---+$/.test(l)) { closeLists(); out.push('<hr>'); continue; }
    if (/^[-*]\s+/.test(l)) {
      if (inOl) { out.push('</ol>'); inOl = false; }
      if (!inUl) { out.push('<ul>'); inUl = true; }
      out.push('<li>' + mdInline(l.replace(/^[-*]\s+/, '')) + '</li>'); continue;
    }
    if (/^\d+\.\s+/.test(l)) {
      if (inUl) { out.push('</ul>'); inUl = false; }
      if (!inOl) { out.push('<ol>'); inOl = true; }
      out.push('<li>' + mdInline(l.replace(/^\d+\.\s+/, '')) + '</li>'); continue;
    }
    closeLists();
    out.push('<p>' + mdInline(l) + '</p>');
  }
  closeLists(); closeTable();
  return out.join('');
}

/* 交付文档：轻量文件行（正文默认折叠），不再是带蓝色边框的大卡片 */
function officeCard(o, content) {
  const body = content
    ? mdToHtml(content)
    : '<div class="doc-loading"><span class="spin"></span> 正在读取文档…</div>';
  return `<div class="doc-card wb-file" data-doc-path="${esc(o.path || '')}">
    <div class="doc-head">
      <span class="wb-file-ico"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/></svg></span>
      <div class="doc-main">
        <b>${esc(o.title || '交付文档')}</b>
        <span class="doc-meta">${esc(o.doc_type_zh || '文档')} · ${o.chars ?? '—'} 字 · ${esc(o.source_zh || '')} · ${o.written ? '已保存到文件' : '仅生成'}</span>
        <span class="doc-path">${esc(o.path || '')}</span>
      </div>
    </div>
    <div class="doc-actions">
      <button data-doc-toggle="${esc(o.path || '')}">查看全文</button>
      <button data-doc-download="${esc(o.path || '')}">下载 .md</button>
      <button data-doc-copy="${esc(o.path || '')}">复制正文</button>
    </div>
    <div class="doc-body md-render" style="display:none">${body}</div>
  </div>`;
}

/* 代码产物：同样收敛成一行文件 + 折叠代码 */
function artifactRow(a) {
  const doc = a.language === '文档';
  return `<div class="artifact-card wb-file">
    <div class="art-head"><span class="wb-file-ico">&#9636;</span><b>${esc(a.path || '产物')}</b></div>
    <div class="art-meta">${esc(a.language || '文件')} · ${a.lines ?? '—'} 行 ·
      ${a.bytes ?? '—'} 字节 · ${a.written ? '已写入磁盘' : '仅生成'}</div>
    <div class="art-actions">
      <button data-view-code="${esc(a.path)}">${doc ? '查看全文' : '查看代码'}</button>
      <button data-download="${esc(a.path)}">下载文件</button>
    </div>
    <pre class="code-view" style="display:none"></pre>
  </div>`;
}

/* 兼容旧调用（历史代码路径）；新渲染统一走 renderTurn */
function bubbleAssistant(answer, cites, engine, skillName, skillRaw, office) {
  return renderTurn('', {
    mode: 'chat', answer, citations: cites || [], engine, skill: skillName,
    skill_raw: skillRaw, office, artifacts: [], elapsed_ms: 0,
  }, null);
}

/* 一轮完整对话：用户气泡 + AI 头像/思考说明/执行清单 + 极简正文 + 文件行 */
function renderTurn(userText, j, usedSkill) {
  if (j.mode !== 'chat' && !j.answer) {
    const oc = j.outcome || {};
    const bits = [`已派发 ${(j.plan || []).length} 个子任务，${oc.status === 'completed' ? '全部完成' : '执行结束'}。`];
    if ((oc.failed || []).length) bits.push('失败：' + oc.failed.map(f => f.task_id || f).join('、') + '。');
    if ((oc.skipped || []).length) bits.push('条件跳过：' + oc.skipped.map(s => s.task_id || s).join('、') + '。');
    j.answer = bits.join('');
  }
  const off = j.office || null;
  const answerHtml = esc(j.answer || '').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  const cite = (j.citations || []).length
    ? `<div class="wb-cite">参考：${j.citations.map(esc).join('；')}</div>` : '';
  const raw = j.skill_raw
    ? `<details class="raw-details"><summary>查看原始结构化数据</summary><pre>${esc(JSON.stringify(j.skill_raw, null, 2))}</pre></details>`
    : '';
  const doc = off ? officeCard(off, off.content || null) : '';
  // 办公文档已由文件行呈现，同一产物不再重复渲染成「代码文件」
  const arts = off ? '' : (j.artifacts || []).map(artifactRow).join('');
  const planNote = (j.plan || []).length
    ? `<div class="wb-cite"><a href="${adminHref()}" style="color:inherit;text-decoration:underline">打开运维管理页</a>：查看每一步真实快照、执行链与流转图；异常可诊断后断点续跑。</div>`
    : '';
  const skill = usedSkill?.display || j.skill;
  const skillLine = skill ? `<div class="wb-skill">已使用技能：${esc(skill)}</div>` : '';
  return `<div class="wb-turn">${wbUser(userText)}
    <div class="wb-ai">${metaHtml(j)}${skillLine}
      <div class="wb-answer">${answerHtml}</div>
      ${raw}${doc}${arts}${cite}${planNote}
      <div class="wb-actions">
        <button data-wb-copy="1" title="复制本轮回答">复制</button>
        ${off ? '<span class="wb-actions-note">文档已保存，可查看全文或下载</span>' : ''}
      </div>
    </div></div>`;
}

function bindWbActions(root) {
  $$('[data-wb-copy]', root).forEach(b => {
    b.onclick = async () => {
      const ai = b.closest('.wb-ai');
      const text = ai?.querySelector('.wb-answer')?.innerText || '';
      try {
        await navigator.clipboard.writeText(text);
        b.textContent = '已复制 ✓';
        setTimeout(() => { b.textContent = '复制'; }, 1600);
      } catch (e) { alert('复制失败：' + e.message); }
    };
  });
}

/* ---------------- 发送 ---------------- */
function activeInput() {
  return state.view === 'home' ? $('#msg-home') : $('#msg');
}

$('#btn-send').onclick = send;
$('#btn-send-home').onclick = send;

async function send() {
  const input = activeInput();
  const message = (input?.value || '').trim();
  if (!message) { input?.focus(); return; }

  const btns = [$('#btn-send'), $('#btn-send-home')];
  btns.forEach(b => { if (b) b.disabled = true; });

  // 发送即进入会话视图，过程与结果都在里面看
  setView('session');
  setTab('chat');
  $$('#nav .nav-item').forEach(b => b.classList.remove('on'));
  const box = $('#answer');
  if (box.querySelector('.empty')) box.innerHTML = '';
  const ct = $('#crumb-title');
  if (ct && (ct.textContent === '新对话' || !ct.textContent)) {
    ct.textContent = message.replace(/\s+/g, ' ').slice(0, 26);
  }
  box.insertAdjacentHTML('beforeend',
    `<div id="pending-turn"><div class="wb-turn">${wbUser(message)}
      <div class="wb-ai">${metaHtml(null, true)}</div></div></div>`);
  box.scrollTop = box.scrollHeight;

  try {
    const usedSkill = state.activeSkill;
    const j = await api('/api/chat', {
      method: 'POST', body: JSON.stringify({
        message, session_id: state.sid, skill: usedSkill?.name || null,
        channel: state.channel }),
    });
    if (j.session_id) {
      state.sid = j.session_id;
      state.sidByChannel[state.channel] = j.session_id;
    }
    $('#crumb-sid').textContent = `session: ${state.sid}`;
    const pending = $('#pending-turn');
    if (pending) pending.outerHTML = renderTurn(message, j, usedSkill);
    else box.insertAdjacentHTML('beforeend', renderTurn(message, j, usedSkill));
    bindArtifactActions(box);
    bindWbActions(box);
    hydrateOfficeCards(box);
    box.scrollTop = box.scrollHeight;
    if (input) input.value = '';
    if (state.activeSkill) { state.activeSkill = null; renderSkillChip(); }
    updateSendState();
    await loadSessions();
    markActive();
    await Promise.allSettled([loadTasks(), loadChain(), loadFlow()]);
  } catch (e) {
    const p = e.payload || {};
    const isolation = p.error_code === 'CHANNEL_ISOLATION';
    const interrupted = p.error_code === 'INTERRUPTED' ||
      (e.status === 409 && !isolation);
    const failHtml = `<div class="wb-turn">${wbUser(message)}
      <div class="wb-ai"><div class="wb-head"><span class="wb-avatar">${WB_AVATAR}</span>
        <b>WorkBuddy</b><span class="wb-elapsed">${isolation ? '通道隔离' : '执行中断'}</span></div>
        <div class="wb-thought">${isolation ? '这条消息没有跨到另一个通道，会话归属保持不变。' : '这轮没能跑完，我先把已发生的动作记下来，方便你继续。'}</div>
        <div class="wb-answer err">${esc(e.message)}</div>
        ${isolation ? '<div class="wb-cite">请点顶部「问答 / 办事」切回该会话所属通道，或新建一个会话。</div>' : ''}
        ${interrupted ? '<div class="wb-cite">状态已落盘，到「诊断 / 恢复」页可断点续跑。</div>' : ''}
      </div></div>`;
    const pending = $('#pending-turn');
    if (pending) pending.outerHTML = failHtml;
    else box.insertAdjacentHTML('beforeend', failHtml);
    if (p.session_id) {
      state.sid = p.session_id;
      state.sidByChannel[state.channel] = p.session_id;
      $('#crumb-sid').textContent = `session: ${state.sid}`;
    }
    await loadSessions();
  } finally {
    btns.forEach(b => { if (b) b.disabled = false; });
  }
}

/* 办事模式也统一成「执行过程清单 + 一句汇报 + 文件行」，不再堆表格和彩色标签 */
function renderAnswer(message, j, usedSkill) {
  if (j.mode !== 'chat') {
    const oc = j.outcome || {};
    j.answer = j.answer || `已派发 ${(j.plan || []).length} 个子任务，${oc.status === 'completed' ? '全部完成' : '执行结束'}。` +
      ((oc.failed || []).length ? `失败：${oc.failed.map(f => f.task_id || f).join('、')}。` : '') +
      ((oc.skipped || []).length ? `条件跳过：${oc.skipped.map(s => s.task_id || s).join('、')}。` : '');
  }
  return renderTurn(message, j, usedSkill);
}

/* ---------------- 技能直达 / 办事产物预览 ---------------- */
function renderSkillChip() {
  const box = $('#skill-chip');
  if (!box) return;
  const s = state.activeSkill;
  if (!s) { box.innerHTML = ''; box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = `
    <span class="skill-chip"><span>&#9889;</span>已选技能：<b>${esc(s.display)}</b>
      <span class="sc-id">${esc(s.name)}</span>
      <button id="btn-skill-clear" title="取消该技能">&times;</button></span>
    <div class="skill-tip">发送后会自动调用这个技能，结果直接回到对话里。</div>`;
  $('#btn-skill-clear').onclick = () => { state.activeSkill = null; renderSkillChip(); };
}

function useSkill(item) {
  if (!item || !item.name) return;
  gotoSession('chat');
  state.activeSkill = item;
  renderSkillChip();
  const box = $('#msg');
  if (box) {
    box.value = `用【${item.display}】处理以下内容：\n`;
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  }
  const ab = $('#answer');
  if (ab) ab.scrollTop = ab.scrollHeight;
}

async function viewCode(path, btn) {
  const card = btn.closest('.artifact-card');
  const pre = card && card.querySelector('.code-view');
  if (!pre) return;
  if (pre.style.display !== 'none') {
    pre.style.display = 'none'; btn.textContent = '查看代码'; return;
  }
  btn.disabled = true; btn.textContent = '读取中…';
  try {
    const j = await api(`/api/artifact?path=${encodeURIComponent(path)}`);
    pre.textContent = j.content;
    pre.style.display = 'block';
    btn.textContent = '收起代码';
  } catch (e) {
    pre.textContent = '读取失败：' + e.message;
    pre.style.display = 'block';
    btn.textContent = '查看代码';
  } finally { btn.disabled = false; }
}

async function downloadCode(path) {
  try {
    const j = await api(`/api/artifact?path=${encodeURIComponent(path)}`);
    const url = URL.createObjectURL(new Blob([j.content], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url; a.download = path.split('/').pop() || 'artifact.txt';
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (e) { alert('下载失败：' + e.message); }
}

function bindArtifactActions(root) {
  $$('[data-view-code]', root).forEach(b => {
    b.onclick = () => viewCode(b.dataset.viewCode, b);
  });
  $$('[data-download]', root).forEach(b => {
    b.onclick = () => downloadCode(b.dataset.download);
  });
  bindOfficeActions(root);
}

/* ---------------- 办公交付卡片交互 ---------------- */
function bindOfficeActions(root) {
  $$('[data-doc-toggle]', root).forEach(b => {
    b.onclick = () => {
      const card = b.closest('.doc-card');
      const body = card && card.querySelector('.doc-body');
      if (!body) return;
      const open = body.style.display !== 'none';
      body.style.display = open ? 'none' : 'block';
      b.textContent = open ? '查看全文' : '收起正文';
    };
  });
  $$('[data-doc-download]', root).forEach(b => {
    b.onclick = () => downloadCode(b.dataset.docDownload);
  });
  $$('[data-doc-copy]', root).forEach(b => {
    b.onclick = async () => {
      const card = b.closest('.doc-card');
      const path = card && card.dataset.docPath;
      try {
        const j = await api(`/api/artifact?path=${encodeURIComponent(path)}`);
        await navigator.clipboard.writeText(j.content);
        b.textContent = '已复制 ✓';
        setTimeout(() => { b.textContent = '复制正文'; }, 1800);
      } catch (e) { alert('复制失败：' + e.message); }
    };
  });
}

/* 刷新回放时，快照只存文档路径，正文从产物文件按需读取 */
async function hydrateOfficeCards(root) {
  for (const card of $$('.doc-card', root)) {
    const body = card.querySelector('.doc-body');
    if (!body || !body.querySelector('.doc-loading')) continue;
    const path = card.dataset.docPath;
    try {
      const j = await api(`/api/artifact?path=${encodeURIComponent(path)}`);
      body.innerHTML = mdToHtml(j.content);
    } catch (e) {
      body.innerHTML = `<div class="empty err">读取文档失败：${esc(e.message)}</div>`;
    }
  }
}

/* 首页办公能力条：真实来自 /api/office/types，不是写死的宣传语 */
async function loadOfficeTypes() {
  const box = $('#home-caps');
  if (!box) return;
  try {
    const d = await api('/api/office/types');
    const names = (d.types || []).map(t => t.name);
    box.innerHTML = '<span class="hc-label">办公能力</span>' +
      names.slice(0, 10).map(n => `<span class="hc-chip">${esc(n)}</span>`).join('') +
      `<span class="hc-more">共 ${d.total} 类文档，说一句就能生成</span>`;
  } catch (e) { box.innerHTML = ''; }
}

/* ---------------- 首页交互 ---------------- */
function updateSendState() {
  const v = ($('#msg-home')?.value || '').trim();
  $('#btn-send-home')?.classList.toggle('ready', !!v);
}
$('#msg-home').oninput = updateSendState;

// 场景标签 / 建议卡片 / 快捷芯片：点击填入并直接发送
$$('.scene, .sg, .chip').forEach(btn => {
  btn.onclick = () => {
    const target = state.view === 'home' ? $('#msg-home') : $('#msg');
    if (!target) return;
    target.value = btn.dataset.q;
    updateSendState();
    send();
  };
});

/* 输入框「+」：三个入口都接平台已有真实功能，不做空壳 */
function bindComposerPlus() {
  const btn = $('#cp-plus');
  if (!btn) return;
  btn.onclick = (e) => {
    e.stopPropagation();
    const old = $('#cp-menu');
    if (old) { old.remove(); return; }
    const menu = document.createElement('div');
    menu.id = 'cp-menu';
    menu.className = 'cp-menu';
    menu.innerHTML = `
      <button data-go="skill"><b>&#9889; 选用技能</b><span>从技能市场挑一个，点击即带标签使用</span></button>
      <button data-go="library"><b>&#9636; 添加资料</b><span>把文档入库，回答会自动带引用</span></button>
      <button data-go="model"><b>&#9670; 模型设置</b><span>接入或切换你自己的 API</span></button>`;
    $('.composer').appendChild(menu);
    $$('#cp-menu button').forEach(b => b.onclick = () => {
      const go = b.dataset.go;
      menu.remove();
      if (go === 'skill') gotoCatalog();
      else if (go === 'library') gotoSessionWithoutResetNav('library');
      else {
        const t = $('#btn-llm-toggle');
        if (t) { if (!state.llmOpen) t.click(); else t.focus(); }
      }
    });
  };
  document.addEventListener('click', (e) => {
    const m = $('#cp-menu');
    if (m && !m.contains(e.target) && e.target !== btn) m.remove();
  });
}
bindComposerPlus();

/* ---------------- 沙箱通道：用户显式选择，消息内容不参与 ---------------- */
function syncChannelUI() {
  $$('#mode-switch .mode, #chan-switch .chan').forEach(b => {
    b.classList.toggle('on', (b.dataset.channel || b.dataset.mode) === state.channel);
  });
  const tag = $('#crumb-channel');
  if (tag) tag.textContent = CHANNEL_LABEL[state.channel] + '通道';
  const home = $('#msg-home');
  if (home) home.placeholder = '在这个通道里，聊天、写文档、跑多步办事都可以…';
  const chatBox = $('#msg');
  if (chatBox) chatBox.placeholder = '今天想做点什么？聊天、写文档、办事都行';
}

/* 切换通道 = 换一套隔离的沙箱：保存当前通道会话，切到目标通道自己的会话。
   消息内容永远不参与，也不自动改写通道归属。 */
function setChannel(ch, force = false) {
  ch = ch === 'work' ? 'work' : 'chat';
  if (ch === state.channel && !force) { syncChannelUI(); return; }
  state.sidByChannel[state.channel] = state.sid;
  state.channel = ch;
  state.sid = state.sidByChannel[ch] || null;
  $('#crumb-sid').textContent = `session: ${state.sid || '-'}`;
  const t = $('#crumb-title'); if (t) t.textContent = '新对话';
  const box = $('#answer');
  if (box) box.innerHTML = state.sid
    ? '<div class="empty">正在回放该通道的会话…</div>'
    : `<div class="empty">新会话 · ${CHANNEL_LABEL[ch]}通道：聊天和办事都在这里完成，不会跳到另一个通道。</div>`;
  syncChannelUI();
  if (state.sid) restoreChat(state.sid);
  loadSessions();
  updateSendState();
}

/* 首页胶囊 + 会话顶栏，同一个沙箱开关 */
$$('#mode-switch .mode, #chan-switch .chan').forEach(btn => {
  btn.onclick = () => setChannel(btn.dataset.channel || btn.dataset.mode);
});
syncChannelUI();

// Enter 发送（Shift+Enter 换行）
[$('#msg-home'), $('#msg')].forEach(box => {
  if (!box) return;
  box.onkeydown = e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  };
});

/* ---------------- 弹出层 ---------------- */
function hidePopovers() {
  ['#pop-plus', '#pop-route', '#pop-avatar'].forEach(s => { const el = $(s); if (el) el.style.display = 'none'; });
  const m = $('#pop-more'); if (m) m.style.display = 'none';
}

$('#btn-plus').onclick = e => {
  e.stopPropagation();
  const p = $('#pop-plus'), opening = p.style.display === 'none';
  hidePopovers();
  p.style.display = opening ? 'block' : 'none';
};

$$('#pop-plus button').forEach(b => {
  b.onclick = () => {
    hidePopovers();
    const act = b.dataset.act;
    if (act === 'new') newSession();
    if (act === 'tools' || act === 'skills') { gotoCatalog(); return; }
    if (act === 'library') gotoSession('library');
    if (act === 'routing') openRouting();
  };
});

$('#btn-route').onclick = e => { e.stopPropagation(); openRouting(); };
$('#pop-route-close').onclick = hidePopovers;

async function openRouting() {
  const p = $('#pop-route'), body = $('#route-body');
  hidePopovers();
  p.style.display = 'block';
  body.innerHTML = '<span class="spin"></span> 读取配置中…';
  const kv = `
    <div class="kv"><b>当前通道</b><span>${CHANNEL_LABEL[state.channel]}通道（用户显式选择）</span></div>
    <div class="kv"><b>切换方式</b><span>点「问答 / 办事」；每个通道有自己独立的会话</span></div>
    <div class="kv"><b>关键词</b><span>不参与判定：请、帮我、分析一下、帮我办件事 等都不会切通道</span></div>
    <div class="kv"><b>两边都能</b><span>正常对话、写文档、跑多步办事</span></div>
    <div class="kv"><b>隔离边界</b><span>会话 / 历史 / 快照 / 日志按通道分开，跨通道发送会被拒绝</span></div>`;
  try {
    const d = await api('/api/routing');
    const rules = (d.rules || []).map(r => `<div class="rule-row">
      <span class="rn">${esc(r.name)}</span>
      <span class="rm">${esc(r.mode)} · 权重 ${esc(r.weight)} · ${esc(r.match_type)} · ${r.patterns.length} 条</span>
    </div>`).join('');
    body.innerHTML = kv +
      `<div class="preview">${esc(d.note || '关键词规则已下线，不参与模式判定。')}</div>` +
      `<details style="margin-top:8px"><summary>查看已下线的历史关键词规则（仅可观测）</summary>${rules}</details>`;
  } catch (e) { body.innerHTML = kv + `<div class="err">${esc(e.message)}</div>`; }
}

$('#btn-avatar').onclick = e => {
  e.stopPropagation();
  const p = $('#pop-avatar'), opening = p.style.display === 'none';
  hidePopovers();
  p.style.display = opening ? 'block' : 'none';
};

document.addEventListener('click', e => {
  if (!e.target.closest('.popover') && !e.target.closest('#btn-plus') &&
      !e.target.closest('#btn-route') && !e.target.closest('#btn-avatar') &&
      !e.target.closest('#btn-more')) hidePopovers();
});

/* ---------------- 侧栏导航 ---------------- */
function newSession() {
  state.sid = null;
  state.sidByChannel[state.channel] = null;
  $('#crumb-sid').textContent = 'session: -';
  markActive();
  $('#answer').innerHTML = `<div class="empty">新会话：发送后自动创建 session_id。</div>`;
  ['#tasks', '#chain', '#flow', '#diag', '#logs', '#library'].forEach(s => { const el = $(s); if (el) el.innerHTML = ''; });
  setView('home');
  $('#msg-home').focus();
}

$$('#nav .nav-item').forEach(btn => {
  btn.onclick = () => {
    $$('#nav .nav-item').forEach(b => b.classList.toggle('on', b === btn));
    const v = btn.dataset.view;
    if (v === 'home') { setView('home'); return; }
    if (v === 'skills') { gotoCatalog(); return; }
    if (v === 'library') { gotoSessionWithoutResetNav('library'); return; }
    if (v === 'more') { setView('more'); loadMore(); return; }
  };
});

function gotoSessionWithoutResetNav(tab) {
  setView('session');
  setTab(tab);
}

function toggleMoreMenu(btn) {
  let m = $('#pop-more');
  if (!m) {
    m = document.createElement('div');
    m.id = 'pop-more';
    m.className = 'popover';
    m.style.cssText = 'position:fixed;left:12px;top:158px;z-index:30;display:none';
    m.innerHTML = `
      <div class="pop-title">更多入口</div>
      <button data-go="diag">&#9888; 一键诊断与断点续跑</button>
      <button data-go="chain">&#9201; 执行链回放</button>
      <button data-go="flow">&#8644; 任务流转图</button>
      <button data-go="reg">&#9638; 工具与技能</button>
      <button data-go="library">&#9636; 资料库</button>
      <button data-go="routing">&#9881; 查看路由规则</button>`;
    document.body.appendChild(m);
    $$('#pop-more button').forEach(b => {
      b.onclick = () => {
        hidePopovers();
        if (b.dataset.go === 'routing') { setView('home'); openRouting(); return; }
        gotoSessionWithoutResetNav(b.dataset.go);
        if (b.dataset.go === 'diag' && state.sid) runDiagnose();
      };
    });
  }
  const opening = m.style.display === 'none';
  hidePopovers();
  m.style.display = opening ? 'block' : 'none';
}

$('#btn-collapse').onclick = () => $('.layout').classList.toggle('collapsed');
$('#btn-search').onclick = () => {
  const box = $('#search-box');
  box.style.display = box.style.display === 'none' ? 'block' : 'none';
  if (box.style.display === 'block') $('#search-input').focus();
};
$('#search-input').oninput = applySearchFilter;
function applySearchFilter() {
  const q = ($('#search-input')?.value || '').toLowerCase();
  $$('#sessions li[data-sid]').forEach(li => {
    li.style.display = !q || li.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

$('#fab-kb').onclick = gotoCatalog;
$('#fab-doc').onclick = () => {
  gotoSessionWithoutResetNav('diag');
  if (state.sid) runDiagnose();
};

/* ---------------- 任务与作业 ---------------- */
function needSid(target) {
  if (state.sid) return false;
  const el = $(target);
  if (el) el.innerHTML = `<div class="empty">先发一条消息，或从左侧选一个会话。</div>`;
  return true;
}

async function loadTasks() {
  const box = $('#tasks');
  if (needSid('#tasks')) return;
  try {
    const data = await api(`/api/sessions/${encodeURIComponent(state.sid)}/tasks`);
    const tasks = data.tasks || [];
    if (!tasks.length) {
      box.innerHTML = `<div class="empty">该会话没有派发记录（对话模式不产生子任务）。</div>`;
      return;
    }
    const rows = tasks.map(task => {
      const deps = asArray(task.depends_on);
      const main = `<tr>
        <td class="mono">${esc(task.task_id)}</td><td>${esc(task.title)}</td>
        <td class="mono">${esc(task.assignee)}</td><td>${stTag(task.status)}</td>
        <td class="mono">${deps.join(', ') || '—'}</td><td class="mono">${task.attempts}</td></tr>`;
      const jobs = (task.jobs || []).map(j => `
        <tr class="job">
          <td class="mono">└ ${esc(j.job_id)}</td>
          <td colspan="2" class="mono">${esc(j.action)} : ${esc(j.target || '')}</td>
          <td>${stTag(j.status)}</td><td class="mono">${esc(j.agent_id)}</td>
          <td class="mono">尝试 ${j.attempts}</td></tr>`).join('');
      return main + jobs;
    }).join('');
    box.innerHTML = `<table>
      <thead><tr><th class="mono">ID</th><th>子任务</th><th>指派</th><th>状态</th><th>依赖</th><th>重试</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  } catch (e) { box.innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

/* ---------------- 执行链 ---------------- */
async function loadChain() {
  const box = $('#chain');
  if (needSid('#chain')) return;
  try {
    const data = await api(`/api/sessions/${encodeURIComponent(state.sid)}/replay`);
    const chain = data.chain || [];
    if (!chain.length) { box.innerHTML = `<div class="empty">暂无快照。</div>`; return; }
    box.innerHTML = `<div class="tl">` + chain.map(s => {
      const cls = /^(ok|done)$/.test(s.status) ? 'ok'
        : s.status === 'interrupted' ? 'interrupted' : s.status === 'failed' ? 'failed' : '';
      const h = s.handoff ? ` <span class="tag">${esc(s.handoff.from_agent)} → ${esc(s.handoff.to_agent)}</span>` : '';
      return `<div class="step ${cls}">
        <span class="ms">${esc(t(s.created_at))}</span>
        <span class="idx">step ${s.step_index}</span>
        <span class="ag">${esc(s.agent_id)}</span>
        <span class="ph">${esc(s.phase)} · ${esc(s.status)}</span>${h}
        ${jblock({ input: s.input, output: s.output, error: s.error })}
      </div>`;
    }).join('') + `</div>`;
  } catch (e) { box.innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

/* ---------------- 流转图 ---------------- */
const VERDICT_CLASS = { pass: 'pass', bounce: 'bounce', escalate: 'escalate' };

async function loadFlow() {
  const box = $('#flow');
  if (needSid('#flow')) return;
  try {
    const data = await api(`/api/sessions/${encodeURIComponent(state.sid)}/handoffs`);
    const hs = data.handoffs || [];
    if (!hs.length) { box.innerHTML = `<div class="empty">暂无流转记录。</div>`; return; }
    const counts = hs.reduce((a, h) => { a[h.verdict || 'other'] = (a[h.verdict || 'other'] || 0) + 1; return a; }, {});
    const sum = Object.entries(counts).map(([k, v]) => `<span class="tag">${esc(k)} ${v}</span>`).join(' ');
    box.innerHTML = `<div style="margin-bottom:12px">${sum}</div>` +
      hs.map(h => `
        <div class="flow-item ${VERDICT_CLASS[h.verdict] || ''}">
          <div class="from">${esc(h.from_agent)}</div><div class="arrow">──▶</div>
          <div class="body"><div class="reason">${esc(h.reason)}</div>
            <div class="v">step ${h.step_index}${h.task_id ? ' · ' + esc(h.task_id) : ''}${h.verdict ? ' · ' + esc(h.verdict) : ''}</div></div>
          <div class="arrow">──▶</div><div class="to">${esc(h.to_agent)}</div>
        </div>`).join('');
  } catch (e) { box.innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

/* ---------------- 资料库 ---------------- */
$('#btn-kb').onclick = () => loadLibrary(true);
$('#kb-q').onkeydown = e => { if (e.key === 'Enter') loadLibrary(true); };

async function loadLibrary(force = false) {
  const box = $('#library');
  const q = ($('#kb-q')?.value || '').trim();
  const url = `/api/knowledge${q || force ? `?q=${encodeURIComponent(q)}` : ''}`;
  try {
    const d = await api(url);
    const docs = (d.documents || []).map(x => `<tr>
      <td class="mono">${esc(x.source)}</td><td>${esc(x.title)}</td>
      <td class="mono">${x.chunks} 块</td><td class="mono">${esc(x.created_at)}</td></tr>`).join('');
    let hits = '';
    if (d.search) {
      hits = `<h3 class="sec">检索结果 · ${esc(d.search.query)}</h3>` +
        ((d.search.hits || []).map(h => `<div class="hit">
          <div class="h"><span class="mono">${esc(h.citation)}</span>
            <span>rank ${h.rank} · score ${h.score}</span></div>
          <div>${esc((h.content || '').slice(0, 320))}</div></div>`).join('') ||
          `<div class="empty">${esc(d.search.note || '未召回相关内容')}</div>`);
    }
    box.innerHTML = `<h3 class="sec" style="margin-top:0">已入库文档（共 ${d.chunk_count} 个切块）</h3>
      ${docs ? `<table><thead><tr><th class="mono">来源</th><th>标题</th><th>切块</th><th>入库时间</th></tr></thead>
      <tbody>${docs}</tbody></table>` : '<div class="empty">知识库为空，可用 CLI 执行 ingest 入库。</div>'}
      ${hits}`;
  } catch (e) { box.innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

/* ---------------- 诊断 / 恢复 ---------------- */
$('#btn-diag').onclick = runDiagnose;
$('#btn-resume').onclick = runResume;

async function runDiagnose() {
  const box = $('#diag');
  if (needSid('#diag')) return;
  box.innerHTML = `<div class="empty"><span class="spin"></span> 取证中…</div>`;
  try {
    const [d, a] = await Promise.all([
      api(`/api/sessions/${encodeURIComponent(state.sid)}/diagnose`),
      api(`/api/sessions/${encodeURIComponent(state.sid)}/audit`).catch(() => ({ audit: [] })),
    ]);
    if (!d.found) { box.innerHTML = `<div class="empty err">${esc(d.message || '会话不存在')}</div>`; return; }
    const ls = d.last_success, ff = d.first_failure;
    box.innerHTML = `
      <table><tbody>
        <tr><th>会话状态</th><td>${esc(d.session_status)} · mode=${esc(d.mode)} · 已分配 ${d.total_steps} 步</td></tr>
        <tr><th>最后成功步骤</th><td>${ls ? `step ${ls.step_index} · <span class="mono">${esc(ls.agent_id)}</span> · ${esc(ls.phase)}` : '无'}</td></tr>
        <tr><th>首个失败点</th><td>${ff ? `step ${ff.step_index} · <span class="mono">${esc(ff.agent_id)}</span> · ${esc(ff.phase)}（${esc(ff.status)}）` : '无失败记录'}</td></tr>
        <tr><th>错误归类</th><td>${esc(d.error_class)} — ${esc(d.error_class_desc || '')}</td></tr>
        <tr><th>未处理异常</th><td>${d.unhandled_error ? '<span class="err">是</span>' : '否'}</td></tr>
        <tr><th>建议动作</th><td><span class="tag warn">${esc(d.suggested_action)}</span></td></tr>
        <tr><th>可恢复</th><td>${d.resumable ? '<span style="color:var(--ok)">是</span>' : '否'}</td></tr>
        <tr><th>未完成子任务</th><td>${(d.unfinished_tasks || []).map(x => `${esc(x.task_id)}(${esc(x.status)})`).join('、') || '—'}</td></tr>
      </tbody></table>
      <h3 class="sec">诊断报告原文</h3><pre>${esc(d.report)}</pre>
      <h3 class="sec">工具调用审计（最近 8 条）</h3>
      ${(a.audit || []).slice(-8).reverse().map(r => `
        <div class="flow-item"><div class="from">step ${r.step_index}</div><div class="arrow">·</div>
          <div class="body"><div class="reason mono">${esc(r.tool)}</div>
            <div class="v">${esc(r.agent_id)} · ${esc(r.permission)} · ${r.duration_ms}ms · ${esc(r.args_digest)}</div></div>
          <div class="to" style="color:${r.status === 'ok' ? 'var(--ok)' : 'var(--bad)'}">${esc(r.status)}</div>
        </div>`).join('') || '<div class="empty">暂无审计记录。</div>'}`;
  } catch (e) { box.innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
  loadLogs();
}

async function runResume() {
  const box = $('#diag');
  if (needSid('#diag')) return;
  box.innerHTML = `<div class="empty"><span class="spin"></span> 读取断点并续跑…</div>`;
  try {
    const j = await api(`/api/sessions/${encodeURIComponent(state.sid)}/resume`, { method: 'POST' });
    box.innerHTML = `<div class="card">
      <div><b>${j.ok ? '恢复完成' : '未恢复'}</b>
        <span class="tag ${j.ok ? 'ok' : 'bad'}">${esc(j.action || '-')}</span>
        <span class="tag">${esc(j.outcome_status || '-')}</span></div>
      <div class="hint" style="margin:8px 0 0">复用子任务：${(j.reused_tasks || []).join(', ') || '无'}</div>
      ${j.reason ? `<div class="cite err">${esc(j.reason)}</div>` : ''}
      <pre style="margin-top:10px">${esc(j.report || '')}</pre></div>`;
    await Promise.allSettled([loadTasks(), loadChain(), loadFlow()]);
    await loadSessions(); markActive();
  } catch (e) { box.innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

async function loadLogs() {
  const box = $('#logs');
  if (needSid('#logs')) return;
  try {
    const data = await api(`/api/sessions/${encodeURIComponent(state.sid)}/logs?limit=80`);
    const rows = data.logs || [];
    if (!rows.length) { box.innerHTML = `<div class="empty">暂无日志。</div>`; return; }
    const color = { ERROR: 'var(--bad)', WARNING: 'var(--warn)', INFO: '#667085', DEBUG: '#98a2b3' };
    box.innerHTML = `<div class="logs">` + rows.map(r => `<div>
      <span style="color:#98a2b3;flex:none">step ${String(r.step_index).padStart(2, '0')}</span>
      <span style="color:${color[r.level] || '#667085'};width:58px;flex:none;font-weight:500">${esc(r.level)}</span>
      <span style="color:#3b6fd4;width:116px;flex:none">${esc(r.agent_id || '-')}</span>
      <span style="color:#8a94a6;flex:none">${esc(r.module)}</span>
      <span style="color:#1f2329;flex:1">${esc(r.message)}</span>
      ${r.error_code ? `<span class="tag bad" style="flex:none">${esc(r.error_code)}</span>` : ''}
    </div>`).join('') + `</div>
      <div class="hint" style="margin-top:10px">共 ${data.total} 条，显示最后 ${rows.length} 条。每条日志都带 session_id / agent_id / step_index。</div>`;
  } catch (e) { box.innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

/* ---------------- 注册表 ---------------- */
async function loadTools() {
  const box = $('#tools');
  try {
    const rows = await api('/api/tools');
    box.innerHTML = `<table>
      <thead><tr><th>工具名称</th><th class="mono">工具 ID</th><th>权限</th><th>超时</th><th>服务 / 传输</th><th>说明</th></tr></thead>
      <tbody>${rows.map(r => `<tr>
        <td><span class="zh-cell"><span class="mini-ic" style="color:${esc(r.icon_color)}">${iconSvg(r.icon, 15)}</span>${esc(r.display_name || r.name)}</span></td>
        <td class="mono sub">${esc(r.name)}</td>
        <td><span class="tag ${r.permission === 'write' || r.permission === 'dangerous' ? 'warn' : 'ok'}">${r.permission === 'write' || r.permission === 'dangerous' ? '写操作' : '只读'}</span></td>
        <td class="mono">${r.timeout_seconds}s</td>
        <td class="mono">${esc(r.server)} / ${esc(r.transport)}</td>
        <td>${esc(r.description || '')}</td></tr>`).join('')}</tbody></table>`;
  } catch (e) { box.innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

async function loadSkills() {
  const box = $('#skills');
  try {
    const rows = await api('/api/skills');
    box.innerHTML = `<table>
      <thead><tr><th>技能名称</th><th class="mono">技能 ID</th><th>触发条件</th><th>依赖工具</th><th>版本</th></tr></thead>
      <tbody>${rows.map(r => {
      const tg = r.triggers || {};
      const trig = []
        .concat((tg.intents || []).map(x => `意图：${x}`))
        .concat((tg.keywords || []).map(x => `关键词：${x}`))
        .concat((tg.regex || []).map(x => `正则：${x}`));
      const tools = (r.dependencies && r.dependencies.tools) || [];
      return `<tr>
        <td><span class="zh-cell"><span class="mini-ic" style="color:${esc(r.icon_color)}">${iconSvg(r.icon, 15)}</span>${esc(r.display_name || r.name)}</span>
          <div class="sub">${esc(r.description || '')}</div></td>
        <td class="mono sub">${esc(r.name)}</td>
        <td style="font-size:11.5px">${trig.map(esc).join('<br>') || '—'}</td>
        <td class="mono">${tools.map(esc).join(', ') || '—'}</td>
        <td class="mono">${esc(r.version || '-')}</td></tr>`;
    }).join('')}</tbody></table>`;
  } catch (e) { box.innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}

/* ---------------- 能力目录：技能 / 专家 / 连接器 / 宿主对照 ---------------- */
function gotoCatalog(tab) {
  if (tab) state.catTab = tab;
  setView('catalog');
  $$('#nav .nav-item').forEach(b => b.classList.toggle('on', b.dataset.view === 'skills'));
  $$('#cat-tabs button').forEach(b => b.classList.toggle('on', b.dataset.cat === state.catTab));
  loadCatalog();
}

async function loadCatalog() {
  try {
    state.catalog = await api('/api/capabilities');
    renderCatalog();
  } catch (e) {
    $('#cat-grid').innerHTML = `<div class="empty err">${esc(e.message)}</div>`;
  }
}

function catItems() {
  const d = state.catalog;
  if (!d) return [];
  const zhBySkill = Object.fromEntries(
    (d.skills || []).map(s => [s.name, s.display_name || s.name]));
  const mapLocal = n => zhBySkill[n] || n;
  if (state.catTab === 'skills') {
    return d.skills.map(s => ({
      name: s.display_name || s.name, sub: s.name, desc: s.zh_desc || s.description,
      color: s.icon_color, icon: s.icon, cat: s.zh_category || s.category,
      badge: s.badge_zh || '本地技能', verified: s.verified, ms: s.last_ms,
      probe: 'skill', target: s.name, kind: 'skill',
      use: { name: s.name, display: s.display_name || s.name },
    }));
  }
  if (state.catTab === 'tools') {
    return d.tools.map(t => ({
      name: t.display_name || t.name, sub: t.name, desc: t.zh_desc || t.description,
      color: t.icon_color, icon: t.icon,
      cat: (t.permission === 'write' || t.permission === 'dangerous') ? '写操作' : '只读',
      badge: t.badge_zh || 'MCP 工具', verified: t.verified, ms: t.last_ms,
      probe: 'tool', target: t.name, kind: 'tool',
    }));
  }
  if (state.catTab === 'experts') {
    return d.experts.map(e => ({
      name: e.display_name || e.id, sub: e.id, desc: e.zh_desc || e.description,
      color: e.icon_color, icon: e.icon, cat: e.zh_category || e.role,
      badge: e.badge_zh || '智能体', verified: true, ms: null, probe: null, kind: 'expert',
    }));
  }
  return [
    ...d.host_skills.map(h => ({
      name: h.display_name || h.name, sub: h.name, desc: h.zh_desc || h.note,
      color: h.icon_color, icon: h.icon, cat: h.zh_category || '宿主技能',
      badge: h.badge_zh || '宿主技能', verified: true, ms: null,
      probe: 'skill', target: h.local_skill, kind: 'host', map: mapLocal(h.local_skill),
      use: h.local_skill ? { name: h.local_skill, display: h.display_name || h.name } : null,
    })),
    ...d.host_connectors.map(h => ({
      name: h.display_name || h.name, sub: h.name, desc: h.zh_desc || h.note,
      color: h.icon_color, icon: h.icon, cat: h.zh_category || '宿主连接器',
      badge: h.badge_zh || '宿主连接器', verified: true, ms: null,
      probe: 'skill', target: h.local_skill, kind: 'host', map: mapLocal(h.local_skill),
      use: h.local_skill ? { name: h.local_skill, display: h.display_name || h.name } : null,
    })),
  ];
}

function renderCatalog() {
  const d = state.catalog;
  if (!d) return;
  const s = d.summary;
  $('#cat-summary').innerHTML = `
    <span class="tag ok">技能 ${s.skills} · 已验证 ${s.skills_verified}</span>
    <span class="tag ok">MCP 工具 ${s.tools} · 已验证 ${s.tools_verified}</span>
    <span class="tag">专家 ${s.experts}</span>
    <span class="tag">宿主技能 ${s.host_skills}</span>
    <span class="tag">宿主连接器 ${s.host_connectors}</span>
    <span class="tag ${s.report && s.report.all_ok ? 'ok' : 'warn'}">
      ${s.report && s.report.all_ok ? '全量试运行通过' : '部分未验证'}</span>
    <span>${esc(s.generated_at || '尚未生成验证报告')}</span>`;

  const items = catItems();
  const cats = ['全部', ...Array.from(new Set(items.map(x => x.cat)))];
  if (!cats.includes(state.catFilter)) state.catFilter = '全部';
  $('#cat-filters').innerHTML = cats.map(c =>
    `<button data-filter="${esc(c)}" class="${c === state.catFilter ? 'on' : ''}">${esc(c)}</button>`).join('');
  $$('#cat-filters button').forEach(b => {
    b.onclick = () => { state.catFilter = b.dataset.filter; renderCatalog(); };
  });

  const q = ($('#cat-q')?.value || '').trim().toLowerCase();
  const shown = items.filter(x =>
    (state.catFilter === '全部' || x.cat === state.catFilter) &&
    (!q || `${x.name} ${x.sub || ''} ${x.desc}`.toLowerCase().includes(q)));

  $('#cat-grid').innerHTML = shown.length ? shown.map(x => `
    <div class="cap-card ${x.kind === 'host' ? 'host' : ''}"${x.use ? ' data-use="' + esc(JSON.stringify(x.use)) + '"' : ''}>
      <div class="cap-head">
        <div class="cap-icon" style="background:${esc(x.color)}1a;color:${esc(x.color)}">${iconSvg(x.icon, 21)}</div>
        <div class="cap-title">
          <div class="cap-name" title="${esc(x.name)}">${esc(x.name)}</div>
          <div class="cap-id">${esc(x.sub || '')}</div>
        </div>
        <span class="cap-badge">${esc(x.badge)}</span>
      </div>
      <div class="cap-desc" title="${esc(x.desc)}">${esc(x.desc || '暂无说明')}</div>
      ${x.map ? `<div class="host-map">→ 本地等价：${esc(x.map)}</div>` : ''}
      <div class="cap-meta">
        <span class="tag ${x.verified ? 'ok' : 'warn'}">${x.verified ? '已验证可运行' : '未验证'}</span>
        <span>${esc(x.badge)}</span>${x.ms != null ? `<span>${x.ms}ms</span>` : ''}
      </div>
      <div class="cap-foot">
        <span class="cap-kind">${esc(x.cat)}</span>
        ${x.use ? `<button data-use="${esc(JSON.stringify(x.use))}">&#9889; 使用</button>` : ''}
        ${x.probe ? `<button data-probe="${x.probe}" data-target="${esc(x.target)}">试运行</button>`
                  : '<span class="cap-kind">编排节点</span>'}
      </div>
    </div>`).join('') : '<div class="empty">没有匹配的能力。</div>';

  $$('#cat-grid button[data-probe]').forEach(b => {
    b.onclick = e => { e.stopPropagation(); doProbe(b.dataset.probe, b.dataset.target); };
  });
  $$('#cat-grid button[data-use]').forEach(b => {
    b.onclick = e => { e.stopPropagation(); useSkill(JSON.parse(b.dataset.use)); };
  });
  $$('#cat-grid .cap-card[data-use]').forEach(card => {
    card.onclick = e => {
      if (e.target.closest('button')) return;
      useSkill(JSON.parse(card.dataset.use));
    };
  });
}

$$('#cat-tabs button').forEach(b => {
  b.onclick = () => {
    state.catTab = b.dataset.cat;
    state.catFilter = '全部';
    $$('#cat-tabs button').forEach(x => x.classList.toggle('on', x === b));
    renderCatalog();
  };
});
$('#cat-q').oninput = renderCatalog;

/* ---------- 试运行弹窗 ---------- */
function showModal(id) { $(`#${id}`).style.display = 'grid'; }
function hideModal(id) { $(`#${id}`).style.display = 'none'; }
$$('[data-close]').forEach(b => { b.onclick = () => hideModal(b.dataset.close); });
$$('.modal-mask').forEach(m => {
  m.onclick = e => { if (e.target === m) m.style.display = 'none'; };
});

async function doProbe(kind, name) {
  showModal('probe-modal');
  $('#probe-title').textContent = `${kind === 'skill' ? '技能' : 'MCP 工具'} · ${name}`;
  $('#probe-body').innerHTML = '<span class="spin"></span> 真实执行中…';
  try {
    const j = await api('/api/capabilities/probe', {
      method: 'POST', body: JSON.stringify({ kind, name }),
    });
    const status = j.ok
      ? `<span class="tag ok">执行成功</span>` : `<span class="tag bad">${esc(j.error_code || '失败')}</span>`;
    $('#probe-body').innerHTML = `
      <div class="probe-line">${status}<span>耗时 ${j.ms}ms</span>
        <span>入口：${kind === 'skill' ? 'SkillLoader.run' : 'ToolRegistry.call'}</span></div>
      <div class="hint">入参（来自目录 sample_args）</div>
      <pre>${esc(JSON.stringify(j.args || {}, null, 2))}</pre>
      <h3 class="sec">${j.ok ? '真实输出' : '错误'}</h3>
      <pre>${esc(j.ok ? JSON.stringify(j.output, null, 2) : j.error)}</pre>`;
  } catch (e) {
    $('#probe-body').innerHTML = `<div class="err">${esc(e.message)}</div>`;
  }
}

/* ---------- 添加技能包 ---------- */
$('#btn-add').onclick = () => {
  const d = state.catalog;
  if (!d) return;
  const cats = Array.from(new Set(d.skills.map(s => s.category)));
  $('#sk-cat').innerHTML = cats.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  $('#sk-impl').innerHTML = (d.impl_options || []).map(n =>
    `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  $('#sk-msg').textContent = '';
  showModal('add-modal');
};

$('#btn-create').onclick = async () => {
  const msg = $('#sk-msg');
  let sample;
  try { sample = JSON.parse($('#sk-args').value || '{}'); }
  catch (e) { msg.innerHTML = `<span class="err">示例参数不是合法 JSON：${esc(e.message)}</span>`; return; }
  if (typeof sample !== 'object' || Array.isArray(sample) || sample === null) {
    msg.innerHTML = '<span class="err">示例参数必须是 JSON 对象</span>'; return;
  }
  const body = {
    name: $('#sk-name').value.trim(),
    description: $('#sk-desc').value.trim() || '运行时创建的技能包',
    category: $('#sk-cat').value,
    impl: $('#sk-impl').value,
    sample_args: sample,
  };
  msg.textContent = '创建中…';
  try {
    const j = await api('/api/capabilities/create', { method: 'POST', body: JSON.stringify(body) });
    msg.innerHTML = `<span style="color:var(--ok)">已创建并加载 ${esc(j.name)}（${esc(j.confirm_note)}）</span>`;
    await loadCatalog();
    setTimeout(() => hideModal('add-modal'), 900);
  } catch (e) {
    msg.innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
};

$('#btn-refresh').onclick = async () => {
  await api('/api/capabilities/refresh', { method: 'POST' });
  await loadCatalog();
};

/* ---------------- 「更多」视图 ---------------- */
async function loadMore() {
  const box = $('#more-stats');
  if (!box) return;
  try {
    const h = await api('/health');
    box.innerHTML = `
      <span class="tag ok">${h.agents} 个智能体</span>
      <span class="tag ok">${h.tools} 个 MCP 工具</span>
      <span class="tag ok">${h.skills} 个技能包</span>
      <span class="tag">${h.sessions} 个会话</span>
      <span class="tag">模型：${esc(h.llm?.model || '离线')}</span>`;
  } catch (e) { box.textContent = '状态读取失败：' + e.message; }
}

$$('.more-scene').forEach(b => {
  b.onclick = () => {
    setView('home');
    const box = $('#msg-home');
    if (box) { box.value = b.dataset.q; updateSendState(); }
    send();
  };
});

$$('.more-links button').forEach(b => {
  b.onclick = () => {
    const go = b.dataset.go;
    if (go === 'api') { gotoSession('chat'); state.llmOpen = true; loadModel(); return; }
    if (go === 'skills') { gotoCatalog(); return; }
    if (go === 'library') { gotoSession('library'); return; }
    if (go === 'diag') {
      gotoSession('diag');
      if (state.sid && typeof runDiagnose === 'function') runDiagnose();
      return;
    }
    if (go === 'routing') { setView('home'); openRouting(); return; }
  };
});

/* ---------------- 实时提示词优化（一边写草稿，一边出标准提示词）---------------- */
function attachPromptHelper(ta, helper, tag, body, useBtn) {
  if (!ta || !helper) return;
  let timer = null;
  ta.addEventListener('input', () => {
    const v = ta.value.trim();
    if (v.length < 4) { helper.style.display = 'none'; return; }
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const d = await api('/api/optimize', {
          method: 'POST', body: JSON.stringify({ draft: v, context: '' }),
        });
        if (d.optimized && d.optimized !== v) {
          helper.dataset.opt = d.optimized;
          body.textContent = d.optimized;
          const isModel = d.source === 'model';
          tag.textContent = isModel ? '由 DeepSeek 优化' : '离线模板';
          tag.className = 'ph-tag ' + (isModel ? 'model' : 'rule');
          helper.style.display = 'block';
        } else {
          helper.style.display = 'none';
        }
      } catch (e) { helper.style.display = 'none'; }
    }, 650);
  });
  useBtn.onclick = () => {
    const opt = helper.dataset.opt;
    if (opt) { ta.value = opt; helper.style.display = 'none'; ta.focus(); }
  };
}

/* 手机端顶栏：两个入口切换（窄屏可见，桌面端隐藏） */
$$('#m-topbar .m-nav button').forEach(b => {
  b.onclick = () => {
    if (b.dataset.mview === 'catalog') gotoCatalog();
    else { setView('home'); }
  };
});

/* ---------------- 启动 ---------------- */
const homeApiBtn = $('#btn-home-api');
if (homeApiBtn) homeApiBtn.onclick = () => {
  gotoSession('chat');          // 首页直接进入「对话 / 办事」并展开 API 配置
  state.llmOpen = true;
  loadModel();
};

(async function boot() {
  await loadHealth();
  await loadSessions();
  await Promise.allSettled([loadTools(), loadSkills(), loadLibrary(), loadCatalog(), loadModel(), loadOfficeTypes()]);
  attachPromptHelper($('#msg'), $('#prompt-helper'), $('#ph-tag'), $('#ph-body'), $('#ph-use'));
  attachPromptHelper($('#msg-home'), $('#prompt-helper-home'), $('#ph-tag-home'), $('#ph-body-home'), $('#ph-use-home'));
  updateSendState();
  setInterval(loadHealth, 15000);
  // 用户正在填写面板（尤其是 API Key）时不重绘，避免把输入清掉
  setInterval(() => {
    const key = document.getElementById('llm-key');
    const editing = (document.activeElement && document.activeElement.closest
      && document.activeElement.closest('.mb-panel')) || (key && key.value);
    if (!editing) loadModel();
  }, 30000);
})();
