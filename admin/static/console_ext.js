/* ============================================================
   Agent Platform 管理后台 · 第三阶段功能扩展
   主菜单 → 子菜单 → 功能：智能分析中枢 / 升级迭代中心 /
   用户与同意管理 / 错误深度核查。
   依赖主页面已有的 api / head / tbl / tag / esc / fmtTime / openMenu。
   ============================================================ */

// ---------- 扩展菜单 ----------
MENU.push(
  {id:'analysis', icon:'🧠', t:'11. 智能分析中枢', sub:[
    ['an-live','11.1 实时分析总览'],['an-insights','11.2 洞察与告警'],
    ['an-trend','11.3 健康评分趋势'],['an-runs','11.4 分析批次记录'],
    ['an-config','11.5 分析规则与阈值']]},
  {id:'iteration', icon:'🚀', t:'12. 升级迭代中心', sub:[
    ['it-board','12.1 迭代看板'],['it-demand','12.2 需求信号池'],
    ['it-failure','12.3 失败案例库'],['it-topic','12.4 高频主题'],
    ['it-history','12.5 版本迭代记录']]},
  {id:'users', icon:'👥', t:'13. 用户与同意管理', sub:[
    ['us-search','13.1 用户检索'],['us-consent','13.2 同意授权管理'],
    ['us-chatlog','13.3 聊天记录排查'],['us-audit','13.4 查询审计留痕']]},
  {id:'deep', icon:'🔬', t:'14. 错误深度核查', sub:[
    ['de-root','14.1 根因线索'],['de-similar','14.2 相似事故聚类'],
    ['de-trend','14.3 错误趋势统计'],['de-workbench','14.4 处置工作台']]}
);

// ---------- 接管渲染分发，并给原有页面追加增强块 ----------
const _baseRender = render;
render = async function(id){
  const v = document.getElementById('view');
  if(id.startsWith('an-')) return await renderAnalysis(id,v);
  if(id.startsWith('it-')) return await renderIteration(id,v);
  if(id.startsWith('us-')) return await renderUsers(id,v);
  if(id.startsWith('de-')) return await renderDeepError(id,v);
  const r = await _baseRender(id);
  try{
    if(id==='ov-health') await enhanceOverview(v);
    if(id==='er-lookup') await enhanceErrorLookup(v);
  }catch(e){ /* 增强块失败不影响原页面 */ }
  return r;
};

const INP = 'style="border:1px solid #d7dfe9;border-radius:8px;padding:8px 10px;font-size:13px;min-width:150px;background:#fff"';
const SEL = 'style="border:1px solid #d7dfe9;border-radius:8px;padding:8px 10px;font-size:13px;background:#fff"';
function btn(label, fn, primary){
  return `<button class="btn ${primary?'primary':''}" style="margin:4px 6px 4px 0" onclick="${fn}">${esc(label)}</button>`;
}
function card(title, hint, body){
  return `<div class="card"><h2>${esc(title)}</h2><p class="hint">${esc(hint)}</p>${body}</div>`;
}
function note(msg, kind){ return `<div class="note ${kind||''}">${esc(msg)}</div>`; }
function kv(k, val){ return `<div class="kpi"><b>${esc(val==null?'—':val)}</b><span>${esc(k)}</span></div>`; }
function jsonBlock(d){ return `<pre class="mono" style="white-space:pre-wrap;font-size:12px;background:#f7f9fc;padding:10px;border-radius:8px;overflow:auto;max-height:320px">${esc(JSON.stringify(d,null,2))}</pre>`; }

// ==================== 11. 智能分析中枢 ====================
async function renderAnalysis(id,v){
  if(id==='an-live'){
    const d = await api('analysis/latest');
    if(!d.found){ v.innerHTML = card('11.1 实时分析总览','常驻引擎每轮扫描真实库表', note(d.message||'尚无批次')+
      btn('立即运行一轮分析','anRun()',true)); return; }
    const m=d.metrics||{}, r=d.run||{}, e=d.engine||{};
    const rows=(d.insights||[]).map(x=>[tag(x.severity),esc(x.category),esc(x.title),esc(x.detail||''),
      x.metric_value==null?'':esc(x.metric_value),tag(x.status)]);
    v.innerHTML = card('11.1 实时分析总览',
      `批次 #${r.id} · ${esc(fmtTime(r.created_at))} · 窗口 ${r.window_minutes} 分钟 · 方法 ${r.method||'rule-engine'}`,
      `<div class="kpis">${kv('健康评分',r.health_score)}${kv('会话数',m.sessions)}${kv('对话轮次',m.turns)}`+
      `${kv('事件数',m.events)}${kv('事故数',m.incidents)}${kv('平均耗时(ms)',m.avg_ms)}`+
      `${kv('P95(ms)',m.p95_ms)}${kv('已授权会话',m.consent_sessions)}</div>`+
      `<div style="margin:10px 0">${btn('立即运行一轮分析','anRun()',true)}${btn('生成 AI 运营解读','anBrief()')}${btn('查看健康趋势',"openMenu('analysis','an-trend')")}</div>`+
      note(r.summary||'','')+
      `<div id="an-extra"></div>`+
      `<h3 style="margin-top:16px;font-size:14px">本轮洞察 / 告警（${rows.length}）</h3>`+
      tbl(['级别','类别','标题','说明','指标','状态'],rows)+
      `<h3 style="margin-top:16px;font-size:14px">引擎说明</h3>${jsonBlock(e)}`) ;
    return;
  }
  if(id==='an-insights'){
    const sev=document.getElementById('an-sev')?.value||'', cat=document.getElementById('an-cat')?.value||'';
    const d=await api(`analysis/insights?limit=200&severity=${encodeURIComponent(sev)}&category=${encodeURIComponent(cat)}`);
    const rows=(d.items||[]).map(x=>[esc(x.id),tag(x.severity),esc(x.category),esc(x.title),
      esc(x.detail||''),esc(fmtTime(x.created_at)),tag(x.status),
      x.status==='open'?`<button class="btn" onclick="anAck(${x.id},'acknowledged')">确认</button> <button class="btn" onclick="anAck(${x.id},'dismissed')">忽略</button>`:'']);
    v.innerHTML=card('11.2 洞察与告警','所有洞察均由常驻引擎基于真实库表产生，可确认或忽略',
      `<div style="margin-bottom:10px">级别 <select id="an-sev" ${SEL}><option value="">全部</option><option value="critical">critical</option><option value="warn">warn</option><option value="info">info</option></select> `+
      `类别 <select id="an-cat" ${SEL}><option value="">全部</option><option value="error_rate">error_rate</option><option value="latency">latency</option><option value="incident">incident</option><option value="failure">failure</option><option value="iteration">iteration</option><option value="health">health</option><option value="ai_brief">ai_brief</option></select> `+
      btn('筛选','anReloadInsights()',true)+`</div>`+
      `<div id="an-extra"></div>`+
      tbl(['ID','级别','类别','标题','说明','时间','状态','操作'],rows));
    return;
  }
  if(id==='an-trend'){
    const d=await api('analysis/trend');
    const pts=(d.trend||[]).map(x=>x.health_score).filter(x=>x!=null);
    let chart='<div class="empty">暂无评分数据</div>';
    if(pts.length){
      const W=760,H=180,pad=20, max=100, step=pts.length>1?W/(pts.length-1):W;
      const coords=pts.map((p,i)=>`${pad+i*step},${H-pad-(p/max)*(H-2*pad)}`).join(' ');
      chart=`<svg viewBox="0 0 ${W+2*pad} ${H}" style="width:100%;height:200px;background:#f7f9fc;border-radius:8px">`+
        `<line x1="${pad}" y1="${H-pad}" x2="${W+pad}" y2="${H-pad}" stroke="#d7dfe9"/>`+
        `<polyline points="${coords}" fill="none" stroke="#2f6bff" stroke-width="2"/>`+
        pts.map((p,i)=>`<circle cx="${pad+i*step}" cy="${H-pad-(p/max)*(H-2*pad)}" r="3" fill="#2f6bff"/>`).join('')+
        `</svg>`;
    }
    const rows=(d.trend||[]).slice().reverse().map(x=>[esc(x.id),esc(fmtTime(x.created_at)),esc(x.health_score),tag(x.status),esc(x.summary||'')]);
    v.innerHTML=card('11.3 健康评分趋势','每轮分析的真实评分，按时间正序绘制',
      chart+tbl(['批次','时间','评分','状态','结论'],rows));
    return;
  }
  if(id==='an-runs'){
    const d=await api('analysis/runs?limit=60');
    const rows=(d.runs||[]).map(x=>[esc(x.id),esc(fmtTime(x.created_at)),esc(x.window_minutes),
      esc(x.health_score),tag(x.status),esc(x.method),esc(x.summary||'')]);
    v.innerHTML=card('11.4 分析批次记录','每次常驻引擎运行都会留下一批可追溯记录',
      `<div style="margin-bottom:10px">${btn('立即运行一轮','anRun()',true)}</div><div id="an-extra"></div>`+
      tbl(['批次','时间','窗口(min)','评分','状态','方法','结论'],rows));
    return;
  }
  if(id==='an-config'){
    const d=await api('analysis/latest');
    const cfg=(d.metrics&&d.metrics.thresholds)||{};
    v.innerHTML=card('11.5 分析规则与阈值','规则驱动的实时统计 + 异常检测；阈值可直接核查，不做黑箱',
      jsonBlock({engine:(d.engine||{}), thresholds:cfg,
        rules:['错误率 > 5% 告警 / > 15% 严重','P95 > 8000ms 告警 / > 15000ms 严重',
               '存在未处置事故扣分','存在失败/中断会话扣分','有会话但无事件流记录扣分',
               '无已授权会话时提示迭代中心无信号','LLM 解读仅按需手动触发，周期性分析不调用大模型']})+
      note('诚实边界：本引擎是规则驱动的确定性分析，不宣称大模型自主推理；AI 解读单独标注为 rule+llm。'));
    return;
  }
}
async function anRun(){
  const out=document.getElementById('an-extra'); if(out) out.innerHTML='<div class="empty">正在分析…</div>';
  try{ const d=await api('analysis/run',{method:'POST'});
    if(out) out.innerHTML=note(d.skipped?('跳过：'+d.reason):('分析完成 · 评分 '+d.health_score+' · 写入 '+d.insights_written+' 条洞察'));
    setTimeout(()=>render(location.hash?location.hash.slice(1):'an-live'),600);
  }catch(e){ if(out) out.innerHTML=note('分析失败：'+e.message,'err'); }
}
async function anBrief(){
  const out=document.getElementById('an-extra'); if(out) out.innerHTML='<div class="empty">正在调用大模型生成解读…</div>';
  try{ const d=await api('analysis/brief',{method:'POST'});
    if(out) out.innerHTML=d.ok?card('AI 运营解读',d.model+' · 按需生成',`<div style="white-space:pre-wrap;line-height:1.7">${esc(d.brief)}</div>`+note(d.note||'')):note('未能生成：'+d.reason,'err');
  }catch(e){ if(out) out.innerHTML=note('调用失败：'+e.message,'err'); }
}
async function anAck(id,status){
  try{ await api('analysis/insights/ack',{method:'POST',body:JSON.stringify({insight_id:id,status:status})});
    await anReloadInsights(); }catch(e){ alert('操作失败：'+e.message); }
}
async function anReloadInsights(){
  const v=document.getElementById('view');
  await renderAnalysis('an-insights',v);
}

// ==================== 12. 升级迭代中心 ====================
async function renderIteration(id,v){
  if(id==='it-board'){
    const s=await api('iteration/stats');
    const d=await api('iteration/items?limit=200');
    const groups={new:[],triaged:[],planned:[],done:[],rejected:[]};
    (d.items||[]).forEach(x=>{ (groups[x.status]||groups.new).push(x); });
    const cols=Object.keys(groups).map(k=>`<div style="flex:1;min-width:180px;background:#f7f9fc;border-radius:8px;padding:10px"><b>${esc(k)} (${groups[k].length})</b>`+
      groups[k].slice(0,12).map(x=>`<div style="background:#fff;border:1px solid #e3e9f1;border-radius:6px;padding:8px;margin-top:8px;font-size:12px">`+
        `<div>${tag(x.priority)} ${esc(x.title)}</div><div style="color:#7a8699;margin-top:4px">票数 ${x.votes} · ${esc(x.category)}</div></div>`).join('')+`</div>`).join('');
    v.innerHTML=card('12.1 迭代看板','反馈 → 分析 → 迭代 闭环；所有条目来自用户已授权的会话',
      `<div class="kpis">${kv('条目总数',s.total)}${kv('已授权会话',s.consent_sessions)}`+
      (s.by_status||[]).map(x=>kv('状态 '+x.status,x.n)).join('')+`</div>`+
      `<div style="margin:10px 0">${btn('重新扫描已授权会话','itScan()',true)}${btn('查看需求池',"openMenu('iteration','it-demand')")}</div>`+
      `<div id="it-extra"></div>`+
      `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px">${cols}</div>`+
      `<h3 style="margin-top:16px;font-size:14px">闭环流程</h3>${jsonBlock(s.pipeline)}`);
    return;
  }
  const catMap={'it-demand':'demand','it-failure':'failure','it-topic':'topic'};
  if(catMap[id]||id==='it-history'){
    const cat=catMap[id]||'';
    const d=await api(`iteration/items?limit=300&category=${encodeURIComponent(cat)}`);
    let items=d.items||[];
    if(id==='it-history') items=items.filter(x=>['done','rejected'].includes(x.status));
    const rows=items.map(x=>[esc(x.id),tag(x.priority),esc(x.title),esc(x.category),
      esc(x.votes),esc(x.source_session||''),`<div style="max-width:320px;white-space:pre-wrap">${esc(x.evidence||'')}</div>`,
      tag(x.status),esc(x.note||''),
      `<select ${SEL} onchange="itUpdate(${x.id},'status',this.value)">`+
      ['new','triaged','planned','done','rejected'].map(s=>`<option value="${s}" ${s===x.status?'selected':''}>${s}</option>`).join('')+`</select> `+
      `<select ${SEL} onchange="itUpdate(${x.id},'priority',this.value)">`+
      ['P0','P1','P2','P3'].map(s=>`<option value="${s}" ${s===x.priority?'selected':''}>${s}</option>`).join('')+`</select>`]);
    const titles={'it-demand':'12.2 需求信号池','it-failure':'12.3 失败案例库',
      'it-topic':'12.4 高频主题','it-history':'12.5 版本迭代记录'};
    v.innerHTML=card(titles[id],'仅来自 consent=1 的会话，证据片段已脱敏；重复信号自动累计票数',
      `<div style="margin-bottom:10px">${btn('重新扫描','itScan()',true)}</div><div id="it-extra"></div>`+
      tbl(['ID','优先级','标题','类别','票数','来源会话','证据（脱敏）','状态','备注','操作'],rows));
    return;
  }
}
async function itScan(){
  const out=document.getElementById('it-extra'); if(out) out.innerHTML='<div class="empty">正在扫描已授权会话…</div>';
  try{ const d=await api('iteration/scan',{method:'POST'});
    if(out) out.innerHTML=note(`扫描 ${d.scanned_turns} 条已授权用户消息 · 新增 ${d.created} 条 · 合并 ${d.merged} 条。${d.notice}`);
    setTimeout(()=>render('it-board'),700);
  }catch(e){ if(out) out.innerHTML=note('扫描失败：'+e.message,'err'); }
}
async function itUpdate(id,field,value){
  try{ await api('iteration/update',{method:'POST',body:JSON.stringify({item_id:id,[field]:value})});
  }catch(e){ alert('更新失败：'+e.message); }
}

// ==================== 13. 用户与同意管理 ====================
async function renderUsers(id,v){
  if(id==='us-search'){
    const d=await api('users?limit=200');
    const rows=(d.users||[]).map(x=>[esc(x.user_key),esc(x.phone_masked),esc(x.account||''),
      esc(x.display_name||''),x.consent?tag('done'):tag('open'),x.session_count,
      esc(fmtTime(x.updated_at)),btn('会话/记录',`usDetail('${esc(x.user_key)}')`)]);
    v.innerHTML=card('13.1 用户检索','按手机号 / 账号 / 关键字检索；手机号默认脱敏，查询写入审计留痕',
      `<div style="margin-bottom:10px">手机号 <input id="us-phone" ${INP} placeholder="如 138"> `+
      `账号 <input id="us-account" ${INP} placeholder="如 user01"> `+
      `关键字 <input id="us-q" ${INP} placeholder="任意匹配"> `+
      btn('检索','usSearch()',true)+`</div><div id="us-extra"></div>`+
      tbl(['用户标识','手机号(脱敏)','账号','名称','同意','会话数','更新时间','操作'],rows));
    return;
  }
  if(id==='us-consent'){
    const d=await api('users?limit=200');
    const rows=(d.users||[]).map(x=>[esc(x.user_key),esc(x.phone_masked),esc(x.account||''),
      x.consent?tag('done'):tag('open'),esc(x.consent_at||''),esc(x.consent_note||''),
      x.consent?btn('撤回同意',`usConsent('${esc(x.user_key)}',false)`):btn('登记同意',`usConsent('${esc(x.user_key)}',true)`,true)]);
    v.innerHTML=card('13.2 同意授权管理',
      '只有 consent=1 的会话才会进入「升级迭代中心」；可随时撤回，撤回后同步更新会话',
      `<div id="us-extra"></div>`+
      tbl(['用户标识','手机号(脱敏)','账号','同意状态','同意时间','备注','操作'],rows)+
      note('合规说明：未授权会话不参与聊天内容分析；按手机号/账号排查 bug 属于独立运维用途，每次查询都会留痕。'));
    return;
  }
  if(id==='us-chatlog'){
    v.innerHTML=card('13.3 聊天记录排查','按会话 ID 或用户标识读取对话，用于定位与修复 bug；默认脱敏',
      `<div style="margin-bottom:10px">会话ID <input id="cl-sid" ${INP} placeholder="session_id"> `+
      `用户标识 <input id="cl-uid" ${INP} placeholder="账号或手机号"> `+
      `用途 <input id="cl-purpose" ${INP} value="bugfix" placeholder="如 bugfix"> `+
      `<label style="font-size:13px;margin-left:8px"><input type="checkbox" id="cl-reveal"> 显示原文（超管，留痕）</label> `+
      btn('读取聊天记录','usChatlog()',true)+`</div><div id="cl-out"></div>`);
    return;
  }
  if(id==='us-audit'){
    const d=await api('query-audit?limit=200');
    const rows=(d.items||[]).map(x=>[esc(x.id),esc(fmtTime(x.created_at)),esc(x.actor),esc(x.action),
      esc(x.user_key||''),esc(x.phone_mask||''),esc(x.account||''),esc(x.session_id||''),
      x.revealed?tag('done'):tag('open'),esc(x.purpose||'')]);
    v.innerHTML=card('13.4 查询审计留痕','用户检索、聊天记录读取、同意变更全部 append-only 留痕',
      tbl(['ID','时间','操作者','动作','用户','手机号(脱敏)','账号','会话','明文','用途'],rows));
    return;
  }
}
async function usSearch(){
  const p=document.getElementById('us-phone').value.trim(),
        a=document.getElementById('us-account').value.trim(),
        q=document.getElementById('us-q').value.trim();
  const d=await api(`users?limit=200&phone=${encodeURIComponent(p)}&account=${encodeURIComponent(a)}&q=${encodeURIComponent(q)}`);
  const rows=(d.users||[]).map(x=>[esc(x.user_key),esc(x.phone_masked),esc(x.account||''),
    x.consent?tag('done'):tag('open'),x.session_count,esc(fmtTime(x.updated_at))]);
  document.getElementById('us-extra').innerHTML=
    note(`命中 ${rows.length} 个用户（查询已留痕）`)+tbl(['用户标识','手机号(脱敏)','账号','同意','会话数','更新时间'],rows);
}
async function usDetail(key){
  const d=await api('users/'+encodeURIComponent(key));
  if(!d.found){ alert('未找到用户'); return; }
  const rows=(d.sessions||[]).map(s=>[esc(s.session_id),esc(s.channel),tag(s.status),
    s.consent?tag('done'):tag('open'),esc(fmtTime(s.created_at))]);
  document.getElementById('us-extra').innerHTML=
    card('用户详情 · '+key,'身份信息与关联会话',
      jsonBlock(d.user)+tbl(['会话','通道','状态','同意','创建时间'],rows)+
      btn('读取该用户聊天记录',`usFillChatlog('${esc(key)}')`,true));
}
function usFillChatlog(key){ openMenu('users','us-chatlog');
  setTimeout(()=>{ const el=document.getElementById('cl-uid'); if(el) el.value=key; },150); }
async function usConsent(key,val){
  try{ await api('users/consent',{method:'POST',body:JSON.stringify({user_key:key,consent:val,
        note:val?'管理后台登记授权':'管理后台撤回授权'})});
    await renderUsers('us-consent',document.getElementById('view'));
  }catch(e){ alert('操作失败：'+e.message); }
}
async function usChatlog(){
  const sid=document.getElementById('cl-sid').value.trim(),
        uid=document.getElementById('cl-uid').value.trim(),
        purpose=document.getElementById('cl-purpose').value.trim()||'bugfix',
        reveal=document.getElementById('cl-reveal').checked?1:0;
  const out=document.getElementById('cl-out'); out.innerHTML='<div class="empty">读取中…</div>';
  try{
    const d=await api(`chatlog?limit=300&session_id=${encodeURIComponent(sid)}&user_key=${encodeURIComponent(uid)}&purpose=${encodeURIComponent(purpose)}&reveal=${reveal}`);
    if(!d.found){ out.innerHTML=note(d.message||'未找到','err'); return; }
    const rows=(d.turns||[]).map(t=>[esc(t.session_id),esc(t.turn_index),tag(t.role==='user'?'warn':'ok'),
      esc(t.role),`<div style="max-width:640px;white-space:pre-wrap">${esc(t.content)}</div>`,esc(t.mode||''),esc(fmtTime(t.created_at))]);
    out.innerHTML=note(`${d.count} 条记录 · ${d.notice}`)+tbl(['会话','轮次','角色标记','角色','内容','模式','时间'],rows);
  }catch(e){ out.innerHTML=note('读取失败：'+e.message,'err'); }
}

// ==================== 14. 错误深度核查 ====================
function deInput(){
  return `<div style="margin-bottom:10px">错误代码 <input id="de-code" ${INP} placeholder="AP-YYYYMMDD-XXXXXX" style="min-width:260px"> `+
    btn('核查根因',"deGo('rootcause')",true)+btn('相似事故',"deGo('similar')")+
    btn('事件时间线',"deGo('timeline')")+btn('去处置工作台',"openMenu('errors','er-resolve')")+`</div><div id="de-extra"></div><div id="de-out"></div>`;
}
async function renderDeepError(id,v){
  const titles={'de-root':'14.1 根因线索','de-similar':'14.2 相似事故聚类',
    'de-trend':'14.3 错误趋势统计','de-workbench':'14.4 处置工作台'};
  v.innerHTML=card(titles[id],'凭错误代码把事故、日志、快照、事件串成完整证据链',deInput());
  if(id==='de-trend'){
    const d=await api('errors/trends?hours=24');
    const rowsH=(d.hourly||[]).map(x=>[esc(x.hour),esc(x.n)]);
    const rowsC=(d.by_class||[]).map(x=>[esc(x.error_class),esc(x.n)]);
    document.getElementById('de-out').innerHTML=
      card('近 24 小时错误趋势',`共 ${d.total} 起事故`,
        tbl(['小时','事故数'],rowsH)+`<h3 style="margin-top:14px;font-size:14px">错误分类 TOP</h3>`+tbl(['错误类别','次数'],rowsC)+
        `<h3 style="margin-top:14px;font-size:14px">处置状态分布</h3>`+jsonBlock(d.by_status));
    return;
  }
  if(id==='de-workbench'){
    const d=await api('incidents?limit=100&status=open');
    const rows=(d.items||d.incidents||[]).map(x=>[esc(x.code),esc(x.error_class),esc(x.message||''),
      esc(x.session_id||''),esc(fmtTime(x.created_at)),btn('载入',`deFill('${esc(x.code)}')`)]);
    document.getElementById('de-out').innerHTML=
      card('待处置事故工作台','从事故列表直接载入错误代码，串联根因与时间线',
        tbl(['错误代码','类别','信息','会话','时间','操作'],rows));
    return;
  }
  const kind={'de-root':'rootcause','de-similar':'similar'}[id]||'timeline';
  const code=document.getElementById('de-code').value.trim();
  if(code) await deGo(kind);
}
function deFill(code){ const el=document.getElementById('de-code'); if(el) el.value=code; }
async function deGo(kind){
  const code=document.getElementById('de-code').value.trim();
  const out=document.getElementById('de-out');
  if(!code){ out.innerHTML=note('请输入错误代码','err'); return; }
  out.innerHTML='<div class="empty">核查中…</div>';
  try{
    const d=await api(`errors/${kind}?code=${encodeURIComponent(code)}`);
    if(d.found===false){ out.innerHTML=note(d.message||'未查到该错误代码','err'); return; }
    if(kind==='rootcause'){
      const rows=(d.suspects||[]).map(s=>[esc(s.kind),esc(s.value),esc(s.where||''),esc(s.hits),esc(s.detail||'')]);
      out.innerHTML=card('根因线索 · '+code, d.conclusion||'',
        tbl(['线索类型','线索值','位置','命中','详情'],rows)+jsonBlock({error_class:d.error_class,session_id:d.session_id}));
    }else if(kind==='similar'){
      const rows=(d.similar||[]).map(s=>[esc(s.code),esc(s.error_class),esc(s.message||''),
        esc(s.session_id||''),tag(s.status),esc(fmtTime(s.created_at))]);
      out.innerHTML=card('相似事故聚类 · '+code,`命中 ${d.count} 起同类/同会话事故`,
        tbl(['错误代码','类别','信息','会话','状态','时间'],rows)+jsonBlock(d.clusters||{}));
    }else{
      const rows=(d.timeline||[]).map(t=>[esc(fmtTime(t.ts)),esc(t.type),esc(t.title),
        `<div style="max-width:520px;white-space:pre-wrap">${esc(t.detail||'')}</div>`]);
      out.innerHTML=card('事件时间线 · '+code,`${d.count} 条证据，按时间排序`,
        tbl(['时间','类型','标题','详情'],rows)+jsonBlock(d.incident||{}));
    }
  }catch(e){ out.innerHTML=note('核查失败：'+e.message,'err'); }
}

// ==================== 原有页面增强 ====================
async function enhanceOverview(v){
  const d=await api('analysis/latest');
  if(!d.found) return;
  const r=d.run||{}, m=d.metrics||{};
  const top=(d.insights||[]).slice(0,3).map(x=>`<div style="font-size:13px;margin:4px 0">${tag(x.severity)} ${esc(x.title)} — ${esc(x.detail||'')}</div>`).join('');
  v.innerHTML += card('智能分析快照（常驻引擎）',
    `批次 #${r.id} · ${esc(fmtTime(r.created_at))} · 方法 ${r.method||'rule-engine'}`,
    `<div class="kpis">${kv('健康评分',r.health_score)}${kv('窗口会话',m.sessions)}${kv('待处置事故',m.open_incidents)}${kv('P95(ms)',m.p95_ms)}</div>`+
    top+`<div style="margin-top:8px">${btn('进入智能分析中枢',"openMenu('analysis','an-live')",true)}${btn('生成 AI 解读','anBrief()')}</div><div id="an-extra"></div>`);
}
async function enhanceErrorLookup(v){
  const d=await api('errors/trends?hours=24');
  const rows=(d.by_class||[]).map(x=>[esc(x.error_class),esc(x.n)]);
  v.innerHTML += card('错误趋势与深度核查（增强）',`近 24 小时共 ${d.total} 起事故`,
    tbl(['错误类别','次数'],rows)+`<div style="margin-top:8px">${btn('根因线索',"openMenu('deep','de-root')",true)}${btn('相似事故',"openMenu('deep','de-similar')")}${btn('趋势统计',"openMenu('deep','de-trend')")}</div>`);
}
