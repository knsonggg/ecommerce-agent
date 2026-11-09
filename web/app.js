const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp = value => new Date(value * 1000).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
const money = value => '¥' + Number(value).toFixed(2);
const labels = {videos:'商品广告视频',aftersales:'退单与退款',overview:'工作概览',chat:'智能接待',products:'商品管理',orders:'订单查询',policies:'知识库',analysis:'选品分析',tickets:'人工工单',settings:'系统与成员'};
const toolNames = {check_aftersale:'售后条件检查',request_aftersale:'提交模拟售后',search_products:'商品资料检索',get_order:'订单状态查询',analyze_selection:'经营指标计算',lookup_policy:'已发布知识检索'};
const statusNames = {open:'待处理',in_progress:'处理中',resolved:'已完成'};
const state = {user:null,view:'overview',overview:null,cid:null,busy:false,rows:[],conversations:[],setup:false,ticketFilter:'all',request:null};
let editor = null;
let toastTimer;
function toast(message) { $('#toast').textContent=message; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,3500); }
function fail(error) { $('#global-error').textContent=error.message; }
async function api(path, body) {
  const response = await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':state.user?.csrf || ''},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(150000)});
  const data = await response.json();
  if (!response.ok) {
    if (response.status===401 && path!='/api/login') {state.user=null; await boot();}
    throw new Error(data.error || '服务请求失败');
  }
  return data;
}
function heading(title, subtitle, buttons='') {return `<div class="section-heading"><div><h2>${title}</h2><p class="subtitle">${subtitle}</p></div><div class="actions">${buttons}</div></div>`;}
function button(label, action, css='secondary', more='') {return `<button class="${css}" data-action="${action}" ${more}>${label}</button>`;}
function badge(label, css='') {return `<span class="badge ${css}">${esc(label)}</span>`;}
function empty(message) {return `<div class="empty">${esc(message)}</div>`;}
function table(columns, rows) {return `<div class="table-wrap"><table><thead><tr>${columns.map(c=>`<th>${c}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>${rows.length?'':empty('暂无数据')}</div>`;}
async function boot() {
  const [status, me] = await Promise.all([api('/api/status'),api('/api/me')]);
  state.setup=status.setup_required;
  state.user=me.user;
  $('#auth').hidden=!!me.user; $('#app').hidden=!me.user;
  if (!me.user) {
    $('#auth-title').textContent=state.setup?'创建你的工作空间':'欢迎回来';
    $('#auth-description').textContent=state.setup?(status.local_setup?'首次使用，请创建管理员账号。密码至少 12 位。':'请先在服务器运行 manage.py create-admin 创建管理员。'):'登录商家工作台，继续处理客户咨询。';
    $('#login-submit').textContent=state.setup?'创建管理员并进入 →':'登录工作台 →';
    $('#login-submit').disabled=state.setup&&!status.local_setup;
    $('#login-form [name=password]').autocomplete=state.setup?'new-password':'current-password';
    return;
  }
  $('#username').textContent=me.user.username; $('#user-role').textContent=me.user.role==='admin'?'店铺管理员':'客服成员';
  $('#user-avatar').textContent=me.user.username[0].toUpperCase();
  $('#today').textContent=new Date().toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric'});
  await refreshOverview();
  await view(state.view);
}
async function refreshOverview() {
  state.overview=await api('/api/overview');
  $('#mode').textContent=state.overview.mode==='demo'?'● 规则演示模式':'● DeepSeek 已配置';
  $('#mode').className='badge'+(state.overview.mode==='demo'?' warn':'');
  $('#ticket-count').textContent=state.overview.open_tickets;
}
async function view(name) {
  if (state.busy) {toast('正在生成回复，请稍候'); return;}
  state.view=name; $('#global-error').textContent=''; $('#page-title').textContent=labels[name];
  document.querySelectorAll('nav [data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===name));
  $('#content').innerHTML=empty('正在加载…');
  try {
    if(name==='videos') {const videoModule=await import('/video.js'); await videoModule.mount({api,esc,toast});}
    else if(name==='overview') await overviewView();
    else if(name==='chat') await chatView();
    else if(['products','orders','policies'].includes(name)) await recordsView(name);
    else if(name==='tickets') await ticketsView();
    else if(name==='aftersales') await aftersalesView();
    else if(name==='analysis') await analysisView();
    else await settingsView();
  } catch(error) {fail(error); $('#content').innerHTML=empty('加载失败，请点击左侧菜单重试。');}
}
async function overviewView() {
  await refreshOverview();
  const o=state.overview;
  const stats=[['累计咨询',o.conversations,'已保存至本地数据库'],['已生成回复',Math.floor(o.messages/2),'可查看历史与工具依据'],['待处理工单',o.open_tickets,'等待人工核实与处理'],['可用商品资料',o.product_count,'维护商品后供助手检索']];
  $('#content').innerHTML=heading(`你好，${esc(state.user.username)}。`,'今天，也让客户服务井井有条。',button('开始处理咨询 →','go-chat','primary'))+
    `<div class="stats">${stats.map(([label,value,foot])=>`<div class="stat"><div class="stat-label">${label}</div><div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`).join('')}</div>`+
    `<div class="grid-two"><section class="start-card"><div class="eyebrow">YOUR SERVICE, CONNECTED</div><h2>重复的问题交给助手，<br>重要的服务留给你。</h2><p>检索店铺资料，生成回复，记录每一次处理。</p>${button('进入智能接待 →','go-chat','primary')}</section><section class="panel"><div class="panel-title"><h3>服务工作流</h3>${badge('人工可控')}</div><div class="flow-list"><div class="flow-item"><span class="step">01</span><div><b>录入客户问题</b><p>从平台复制咨询，创建独立会话。</p></div></div><div class="flow-item"><span class="step">02</span><div><b>智能查询并生成回复</b><p>使用已上架商品、订单和已发布知识。</p></div></div><div class="flow-item"><span class="step">03</span><div><b>核对后发送，异常转工单</b><p>复制回复至平台，复杂问题交由人工。</p></div></div></div></section></div>`+
    `<div class="grid-two bottom-grid"><section class="panel"><div class="panel-title"><h3>店铺资源</h3><small>让答案来自你的店铺</small></div><div class="resource-grid"><button class="resource" data-view="products"><i>▧</i><b>商品资料</b><p>${o.product_count} 款商品 →</p></button><button class="resource" data-view="orders"><i>▱</i><b>订单数据</b><p>${o.order_count} 笔订单 →</p></button><button class="resource" data-view="policies"><i>▥</i><b>服务知识</b><p>${o.knowledge_count} 条知识 →</p></button></div></section><section class="panel"><div class="panel-title"><h3>运行状态</h3>${badge('本地工作空间')}</div><div class="activity"><span>回复引擎</span><b>${o.mode==='demo'?'规则与模板':'DeepSeek'}</b></div><div class="activity"><span>接待渠道</span><span>人工录入 / 复制回复</span></div><div class="activity"><span>数据存储</span><span>SQLite · 已开启持久化</span></div><div class="activity"><span>人工工单</span><span>${o.open_tickets} 项待处理</span></div></section></div>`+
    `<p class="help">初始资料为模拟数据。未接入平台自动收发消息；当前用于商家内部处理，回复发送前请核对。</p>`;
}
async function loadConversations() {
  state.conversations=await api('/api/conversations');
  const list=$('#conversation-list'); if(!list)return;
  list.innerHTML=state.conversations.map(c=>`<button class="conversation-item ${state.cid===c.id?'selected':''}" data-action="select-conversation" data-id="${esc(c.id)}"><b>${esc(c.title)}</b><small>${stamp(c.updated)}</small></button>`).join('') || '<p class="help">还没有咨询记录</p>';
}
async function chatView() {
  $('#content').innerHTML=`<div class="chat-layout"><section class="conversation-panel">${button('＋ 新建咨询','new-conversation','primary')}<h3>最近咨询 · 仅自己可见</h3><div id="conversation-list"></div></section><section class="chat-main"><div class="chat-heading"><div><b>◈ 智能接待</b><small>所有回复均可追溯到查询依据</small></div>${button('创建人工工单','handoff')}</div><div id="chat-scroll"><div id="welcome"><span class="copilot-icon">◈</span><h2>你好，有什么需要我处理？</h2><p>粘贴客户咨询，或从下面的场景开始。<br>我会查询店铺资料，为你整理回复。</p><div class="prompt-grid"><button data-prompt="店铺支持什么退货政策？"><b>↔ 售后咨询</b>查询店铺退换货规则</button><button data-prompt="推荐300元以内适合通勤的商品"><b>✧ 商品推荐</b>按需求与预算筛选商品</button><button data-prompt="查询订单 DEMO1001"><b>▱ 订单进度</b>核对物流与履约状态</button><button data-prompt="为云感降噪耳机生成商品文案"><b>✎ 商品文案</b>从已有卖点生成文案草稿</button></div></div><section id="messages" aria-live="polite"></section></div><form id="composer"><label class="sr-only" for="input">客户问题</label><textarea id="input" maxlength="2000" placeholder="粘贴客户问题，或描述需要处理的事项…" rows="2"></textarea><div id="composer-bottom"><span>Enter 发送 · Shift + Enter 换行 · 历史自动保存</span><button class="primary" id="send" type="submit">生成回复 ↑</button></div></form><div class="compose-note">${state.overview.mode==='demo'?'当前为规则演示；配置 DeepSeek 后可生成模型回复。':'消息与相关查询结果会发送至 DeepSeek。'} 请勿输入密码或支付凭据。</div></section></div>`;
  await loadConversations();
  if(state.cid) await selectConversation(state.cid);
}
function addMessage(role, content, trace=[], handoff=false) {
  const item=document.createElement('article'); item.className=`message ${role}`;
  const label=document.createElement('div'); label.className='message-label'; label.textContent=role==='user'?'客户问题 · 由客服录入':'◈ Commerce Copilot · 回复草稿';
  const bubble=document.createElement('div');bubble.className='bubble';bubble.textContent=content;item.append(label,bubble);
  if(role==='assistant') {
    const actions=document.createElement('div'); actions.className='message-actions';
    const copy=document.createElement('button');copy.className='link-button';copy.textContent='复制回复';copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(content);toast('回复已复制，请核对后发送');}catch{toast('无法自动复制，请选中文字复制');}}); actions.append(copy);item.append(actions);
  }
  if(trace.length) {
    const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent=`已查询 ${trace.length} 次资料 · 查看依据`;details.append(summary);
    trace.forEach(tool=>{const pre=document.createElement('pre');pre.textContent=`${toolNames[tool.name]||tool.name}\n${JSON.stringify(tool.result,null,2)}`;details.append(pre);});item.append(details);
  }
  if(handoff){const note=document.createElement('div');note.className='handoff-hint';note.textContent='资料不足或问题需人工判断，建议创建人工工单。';item.append(note);}
  $('#messages').append(item);$('#chat-scroll').scrollTop=$('#chat-scroll').scrollHeight;return item;
}
async function selectConversation(cid) {
  const rows=await api('/api/conversations/'+cid);state.cid=cid;state.request=null;
  $('#messages').replaceChildren();$('#welcome').hidden=rows.length>0;
  rows.forEach(m=>addMessage(m.role,m.content,m.trace));
  document.querySelectorAll('.conversation-item').forEach(b=>b.classList.toggle('selected',b.dataset.id===cid));
}
async function sendMessage(message) {
  if(state.busy || !message.trim())return;
  message=message.trim();
  if(message.length>2000){toast('消息最多 2000 字');return;}
  state.busy=true;$('#send').disabled=true;$('#input').disabled=true;$('#global-error').textContent='';
  let waiting;
  try {
    if(!state.cid){const c=await api('/api/conversations',{});state.cid=c.id;}
    const prior=await api('/api/conversations/'+state.cid);
    $('#messages').replaceChildren();prior.forEach(m=>addMessage(m.role,m.content,m.trace));$('#welcome').hidden=true;
    addMessage('user',message);waiting=addMessage('assistant','正在查询资料并整理回复…');
    if(!state.request || state.request.message!==message || state.request.cid!==state.cid)state.request={id:crypto.randomUUID(),message,cid:state.cid};
    const result=await api('/api/chat',{message,conversation_id:state.cid,request_id:state.request.id});
    waiting.remove();addMessage('assistant',result.reply,result.trace,result.handoff_recommended);$('#input').value='';state.request=null;
    await loadConversations();await refreshOverview();
  } catch(error){waiting?.remove();fail(error);$('#input').value=message;}
  finally {state.busy=false;$('#send').disabled=false;$('#input').disabled=false;$('#input').focus();}
}
const configs={
  products:{name:'商品',sub:'维护价格、库存与卖点。只有已上架且有库存的商品参与推荐。',fields:[['id','商品编号','text'],['name','商品名称','text'],['category','分类','text'],['price','售价（元）','number'],['cost','成本（元）','number'],['stock','库存','number'],['sales_30d','近30天销量','number'],['returns_30d','近30天退货数','number'],['tags','标签（逗号分隔）','list'],['features','商品卖点（每行一条）','lines'],['active','上架并供助手查询','checkbox']]},
  orders:{name:'订单',sub:'查询履约与物流。此处维护本地记录，不会更改电商平台订单。',fields:[['id','订单编号','text'],['status','订单状态','order-status'],['amount','订单金额（元）','number'],['items','商品名称（每行一件）','lines'],['carrier','承运方（可留空）','optional'],['tracking','物流编号（可留空）','optional'],['timeline','物流节点（每行一条）','lines']]},
  policies:{name:'知识',sub:'发布后的知识才用于回答。未知政策交由人工确认，避免错误承诺。',fields:[['id','知识编号','text'],['topic','知识标题','text'],['keywords','检索关键词（逗号分隔）','list'],['answer','回复依据','long'],['published','发布并供助手检索','checkbox']]}
};
async function recordsView(kind) {
  state.rows=await api('/api/records/'+kind);const c=configs[kind];
  $('#content').innerHTML=heading(c.name+(kind==='policies'?'库':kind==='orders'?'查询':'管理'),c.sub,state.user.role==='admin'?button('导入 JSON','import')+button('＋ 新增'+c.name,'add-record','primary'):badge('只读权限'))+
    `<div class="toolbar"><label class="sr-only" for="search">搜索</label><input id="search" type="search" placeholder="搜索编号、名称或关键词…"><span class="help">共 ${state.rows.length} 条</span>${state.user.role==='admin'?button('下载导入示例','template','link-button'):''}<input id="import-file" class="sr-only" type="file" accept="application/json,.json"></div><div id="record-list"></div>`;
  renderRecords(kind,'');
}
function renderRecords(kind,query) {
  const rows=state.rows.filter(r=>JSON.stringify(r).toLowerCase().includes(query.toLowerCase()));
  const edit = r => state.user.role==='admin'?button('编辑','edit-record','secondary',`data-id="${esc(r.id)}"`):'<small>只读</small>';
  if(kind==='products')$('#record-list').innerHTML=table(['商品资料','分类','售价 / 成本','库存','近30天销量','状态','操作'],rows.map(r=>`<tr><td><strong>${esc(r.name)}</strong><small>${esc(r.id)}</small></td><td>${esc(r.category)}</td><td>${money(r.price)}<small>成本 ${money(r.cost)}</small></td><td>${r.stock}</td><td>${r.sales_30d}</td><td>${badge(r.active?'已上架':'已下架',r.active?'':'neutral')}</td><td>${edit(r)}</td></tr>`));
  if(kind==='orders')$('#record-list').innerHTML=table(['订单编号','商品','金额','履约状态','物流','操作'],rows.map(r=>`<tr><td><strong>${esc(r.id)}</strong></td><td>${esc(r.items.join('、'))}</td><td>${money(r.amount)}</td><td>${badge(r.status)}</td><td>${esc(r.carrier||'尚未发货')}<small>${esc(r.tracking)}</small></td><td>${edit(r)}</td></tr>`));
  if(kind==='policies')$('#record-list').innerHTML=rows.length?`<div class="knowledge-grid">${rows.map(r=>`<section class="panel knowledge-card">${badge(r.published?'已发布':'草稿',r.published?'':'neutral')}<h3>${esc(r.topic)}</h3><p>${esc(r.answer)}</p><div>${r.keywords.map(k=>`<span class="keyword">${esc(k)}</span>`).join('')}</div><div class="knowledge-footer"><small>${esc(r.id)} · v${r._version}</small>${edit(r)}</div></section>`).join('')}</div>`:empty('没有匹配的知识');
}
function showEditor(title, fields, save, saveText='保存') {
  editor={save};$('#editor-title').textContent=title;$('#editor-fields').innerHTML=fields;$('#editor-error').textContent='';$('#save-editor').textContent=saveText;$('#save-editor').hidden=!save;$('#editor').showModal();
}
function field(key,label,type,value,editing=false) {
  let control;
  if(type==='checkbox')return `<label class="wide"><input type="checkbox" name="${key}" ${value?'checked':''}>${label}</label>`;
  if(type==='order-status')control=`<select name="${key}">${['待付款','待发货','运输中','已完成','已取消','售后中','已退款'].map(s=>`<option ${value===s?'selected':''}>${s}</option>`).join('')}</select>`;
  else if(['long','lines'].includes(type))control=`<textarea name="${key}" maxlength="${type==='long'?5000:10000}" ${key==='items'?'required':''}>${esc(Array.isArray(value)?value.join('\n'):value||'')}</textarea>`;
  else control=`<input name="${key}" type="${type==='number'?'number':'text'}" value="${esc(Array.isArray(value)?value.join('，'):value??'')}" ${type==='number'?'min="0" step="0.01"':''} ${type==='optional'?'':'required'} ${editing&&key==='id'?'readonly':''} maxlength="300">`;
  return `<label class="${['long','lines','list'].includes(type)?'wide':''}">${label}${control}</label>`;
}
function editRecord(row) {
  const kind=state.view,c=configs[kind];
  const defaults={active:true,published:false,stock:0,sales_30d:0,returns_30d:0,cost:0,price:1,amount:0};
  const value={...defaults,...row};
  showEditor((row?'编辑':'新增')+c.name,`<div class="form-grid">${c.fields.map(([k,l,t])=>field(k,l,t,value[k],!!row)).join('')}</div><p class="help">保存到本地店铺资料。更新将立即供下一次查询使用。</p>`,async form=>{
    const body={};
    c.fields.forEach(([key,_,type])=>{const input=form.elements.namedItem(key);body[key]=type==='checkbox'?input.checked:type==='number'?Number(input.value):['lines','list'].includes(type)?input.value.split(type==='lines'?/\n/:/[,，]/).map(v=>v.trim()).filter(Boolean):input.value;});
    if(row)body._version=row._version;
    await api('/api/records/'+kind,body);toast('资料已保存');await recordsView(kind);
  });
}
async function ticketsView() {
  state.rows=await api('/api/tickets');
  $('#content').innerHTML=heading('人工工单','从需要核实的问题开始，保留上下文，让服务有人跟进。',button('刷新列表','refresh'))+`<div class="ticket-filters">${['all','open','in_progress','resolved'].map(s=>`<button class="filter ${state.ticketFilter===s?'active':''}" data-action="ticket-filter" data-status="${s}">${s==='all'?'全部工单':statusNames[s]}</button>`).join('')}</div><div id="ticket-list"></div>`;
  const rows=state.rows.filter(r=>state.ticketFilter==='all'||r.status===state.ticketFilter);
  $('#ticket-list').innerHTML=table(['工单 / 主题','发起人','负责人','状态','更新时间','操作'],rows.map(r=>`<tr><td><strong>${esc(r.subject)}</strong><small>${esc(r.id)}</small></td><td>${esc(r.creator)}</td><td>${esc(r.assigned_name||'待领取')}</td><td>${badge(statusNames[r.status],r.status==='open'?'warn':'')}</td><td>${stamp(r.updated)}</td><td>${button('查看','ticket-detail','secondary',`data-id="${esc(r.id)}"`)}</td></tr>`));
}
async function analysisView() {
  const data=await api('/api/analysis');
  const top=data.ranking[0];
  $('#content').innerHTML=heading('选品分析','用可解释的指标辅助选品判断。',button('刷新数据','refresh'))+`<div class="notice">${esc(data.basis)} 数据来自本地录入，首次为模拟资料。</div><div class="grid-two"><section class="panel"><div class="panel-title"><h3>商品得分对比</h3><small>销量 × 毛利率 × (1 − 退货率)</small></div>${data.ranking.map(r=>`<div class="progress-row"><div class="progress-head"><span>${esc(r.name)}</span><b>${r.score}</b></div><progress max="${Math.max(top?.score||1,1)}" value="${r.score}"></progress></div>`).join('')||empty('暂无可分析商品')}</section><section class="panel"><div class="panel-title"><h3>选品参考</h3>${badge('辅助决策')}</div>${top?`<h2>${esc(top.name)}</h2><p class="subtitle">当前排序第一。请结合广告费用、履约成本与实际需求做小批量测试。</p><div class="activity"><span>近30天销量</span><b>${top.sales_30d}</b></div><div class="activity"><span>商品毛利率</span><b>${top.gross_margin_pct}%</b></div><div class="activity"><span>退货率</span><b>${top.return_rate_pct}%</b></div><div class="activity"><span>可用库存</span><b>${top.stock}</b></div>`:empty('先录入商品资料')}</section></div><div class="bottom-grid">${table(['排名','商品','30天销量','毛利率','退货率','库存','得分'],data.ranking.map((r,i)=>`<tr><td>${i+1}</td><td><strong>${esc(r.name)}</strong></td><td>${r.sales_30d}</td><td>${r.gross_margin_pct}%</td><td>${r.return_rate_pct}%</td><td>${r.stock}</td><td>${r.score}</td></tr>`))}</div>`;
}
async function settingsView() {
  await refreshOverview();const o=state.overview;
  const admin=state.user.role==='admin';
  const [users,audits]=admin?await Promise.all([api('/api/users'),api('/api/audits')]):[[],[]];
  $('#content').innerHTML=heading('系统与成员','管理工作空间成员，核对系统配置与操作记录。')+`<div class="settings-grid"><section class="panel"><div class="panel-title"><h3>模型与接待</h3>${badge(o.mode==='demo'?'演示模式':'DeepSeek 已配置',o.mode==='demo'?'warn':'')}</div><div class="setting-row"><span>模型</span><b>${esc(o.model)}</b></div><div class="setting-row"><span>接待渠道</span><b>商家内部 · 人工录入</b></div><div class="setting-row"><span>平台自动收发</span><b>尚未对接</b></div><div class="setting-row"><span>数据范围</span><b>单店工作空间</b></div><p class="help">模型密钥仅在服务器 .env 中配置，不提供网页回显。配置后重启服务生效。DeepSeek 会接收消息与相关资料，请先确认数据可发送。</p></section><section class="panel"><div class="panel-title"><h3>工作空间保护</h3>${badge('已启用')}</div><div class="setting-row"><span>登录凭据</span><b>加盐 PBKDF2 哈希</b></div><div class="setting-row"><span>会话有效期</span><b>8 小时</b></div><div class="setting-row"><span>数据编辑</span><b>管理员权限 + 版本校验</b></div><div class="setting-row"><span>模型操作</span><b>资料查询 + 本地模拟售后</b></div><p class="help">本地数据库不纳入 Git。备份命令与 HTTPS 部署要求见项目 README；建议使用系统账户权限保护本机数据。</p></section><section class="panel wide"><div class="panel-title"><h3>工作空间成员</h3>${admin?button('＋ 添加成员','add-user','primary'):badge('仅管理员可管理')}</div>${admin?table(['账号','角色','创建时间','状态','操作'],users.map(u=>`<tr><td><strong>${esc(u.username)}</strong></td><td>${u.role==='admin'?'管理员':'客服成员'}</td><td>${stamp(u.created)}</td><td>${badge(u.active?'启用':'停用',u.active?'':'neutral')}</td><td>${u.id===state.user.id?'<small>当前账号</small>':button(u.active?'停用':'启用','toggle-user','secondary',`data-id="${esc(u.id)}" data-active="${u.active?'false':'true'}"`)}</td></tr>`)):empty('请联系管理员添加或维护客服账号。')}</section>${admin?`<section class="panel wide"><div class="panel-title"><h3>最近操作记录</h3><small>不记录密码、密钥或消息正文</small></div>${table(['时间','操作人','事件','对象'],audits.map(a=>`<tr><td>${stamp(a.created)}</td><td>${esc(a.actor)}</td><td>${esc(a.action)}</td><td>${esc(a.target)}</td></tr>`))}</section>`:''}</div>`;
}
async function action(name,target) {
  if(name==='new-aftersale') {
    const rid=crypto.randomUUID();
    return showEditor('申请退单 / 退款（本地模拟）',`<div class="notice warning">不处理真实资金。未发货全额退款≤100元自动模拟完成；其他退款需审批，已发货还需退货验收。</div>${field('order_id','订单编号','text','')}<label>售后类型<select name="kind"><option value="cancel">待付款取消</option><option value="refund_only">未发货全额退款</option><option value="return_refund">已发货退货退款</option></select></label>${field('reason','申请原因','long','')}`,async form=>{await api('/api/aftersales',{order_id:form.elements.order_id.value,kind:form.elements.kind.value,reason:form.elements.reason.value,request_id:rid});toast('模拟售后申请已保存');await aftersalesView();},'提交模拟申请');
  }
  if(name==='decide-aftersale') {
    const decision=target.dataset.decision,id=target.dataset.id;
    const names={approve:'批准模拟售后',reject:'驳回申请',received:'确认退货验收并模拟退款'};
    return showEditor(names[decision],`<div class="notice warning">仅修改本地模拟记录，不调用支付或平台接口。批准已发货退款后仍需登记验收。</div>${field('note',decision==='received'?'退货验收说明':'审核说明','long','')}`,async form=>{await api('/api/aftersales/decision',{id,decision,note:form.elements.note.value});toast('售后记录已更新');await aftersalesView();},names[decision]);
  }
  if(name==='toggle-user'){await api('/api/users/status',{id:target.dataset.id,active:target.dataset.active==='true'});toast('成员状态已更新，原有登录会话已失效');return settingsView();}
  if(name==='go-chat')return view('chat');
  if(name==='refresh')return view(state.view);
  if(name==='new-conversation'){state.cid=null;state.request=null;return chatView();}
  if(name==='select-conversation')return selectConversation(target.dataset.id);
  if(name==='add-record')return editRecord(null);
  if(name==='edit-record')return editRecord(state.rows.find(r=>r.id===target.dataset.id));
  if(name==='import')return $('#import-file').click();
  if(name==='template'){
    const clean={id:'NEW_'+Date.now()};
    // Template deliberately contains only a fresh empty skeleton, never business values.
    const samples={products:{id:clean.id,name:'示例商品',category:'示例分类',price:100,cost:50,stock:10,sales_30d:0,returns_30d:0,tags:['示例'],features:['请填写实际卖点'],active:false},orders:{id:clean.id,status:'待发货',amount:100,items:['示例商品'],carrier:'',tracking:'',timeline:[]},policies:{id:clean.id,topic:'示例政策',keywords:['关键词'],answer:'请填写经过确认的店铺政策',published:false}};
    const blob=new Blob([JSON.stringify([samples[state.view]],null,2)],{type:'application/json'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=state.view+'-template.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);return;
  }
  if(name==='handoff'){
    if(!state.cid){toast('请先创建咨询并描述需要处理的问题');return;}
    return showEditor('创建人工工单',`<p class="help">关联当前咨询记录。创建后进入团队工单队列，不会自动联系外部客服。</p>${field('subject','工单主题','text','')}${field('note','需要人工核实的事项','long','')}`,async form=>{await api('/api/tickets',{conversation_id:state.cid,subject:form.elements.subject.value,note:form.elements.note.value});toast('工单已创建，团队可在人工工单中领取');await refreshOverview();},'创建工单');
  }
  if(name==='ticket-filter'){state.ticketFilter=target.dataset.status;return ticketsView();}
  if(name==='ticket-detail'){
    const data=await api('/api/tickets/'+target.dataset.id),t=data.ticket;
    const transition=t.status==='open'?'in_progress':t.status==='in_progress'&&t.assignee===state.user.id?'resolved':null;
    showEditor('工单 · '+t.subject,`<div class="notice">${esc(t.note||'未填写说明')}</div><p class="help">${esc(t.id)} · ${statusNames[t.status]} · ${stamp(t.created)}</p>${t.resolution?`<p class="subtitle">处理结果：${esc(t.resolution)}</p>`:''}<div class="dialog-messages">${data.messages.map(m=>`<article class="message ${m.role}"><div class="message-label">${m.role==='user'?'客户问题':'助手草稿'}</div><div class="bubble">${esc(m.content)}</div></article>`).join('')||empty('暂无关联对话')}</div>${transition==='resolved'?field('resolution','处理结果（必填）','long',''):''}`,transition?async form=>{await api('/api/tickets/update',{id:t.id,status:transition,resolution:form.elements.resolution?.value});toast(transition==='in_progress'?'已领取工单':'工单已完成');await ticketsView();await refreshOverview();}:null,transition==='in_progress'?'领取工单':'标记完成');return;
  }
  if(name==='add-user')return showEditor('添加工作空间成员',`${field('username','账号（3–40 位字母数字）','text','')}<label>初始密码（至少12位）<input name="password" type="password" minlength="12" maxlength="128" autocomplete="new-password" required></label><label>角色<select name="role"><option value="support">客服成员 · 接待与工单</option><option value="admin">管理员 · 可维护资料与账号</option></select></label>`,async form=>{await api('/api/users',{username:form.elements.username.value,password:form.elements.password.value,role:form.elements.role.value});toast('成员已添加');await settingsView();});
}
const aftersaleStatuses={pending_review:'待管理员审批',awaiting_return:'待退货验收',simulated_completed:'模拟完成 · 无真实退款',rejected:'已驳回'};
const aftersaleKinds={cancel:'取消待付款订单',refund_only:'未发货全额退款',return_refund:'退货退款'};
async function aftersalesView() {
  state.rows=await api('/api/aftersales');
  const admin=state.user.role==='admin';
  $('#content').innerHTML=heading('退单与退款','统一办理取消、退款与退货，保留规则判断及审核记录。',button('＋ 新增售后申请','new-aftersale','primary'))+`<div class="notice warning">当前是本地模拟执行，不会退回真实资金。待付款取消、未发货全额退款≤100元可自动模拟完成；较高金额由管理员审批，退货退款需确认验收。暂不支持部分退款。</div>`+table(['售后 / 订单','类型 / 原因','退款金额','进度','申请人','操作 / 审核结果'],state.rows.map(r=>{
    const actions=[];
    if(admin&&r.status==='pending_review')actions.push(button('批准','decide-aftersale','secondary',`data-id="${esc(r.id)}" data-decision="approve"`));
    if(admin&&r.status==='awaiting_return')actions.push(button('确认验收','decide-aftersale','secondary',`data-id="${esc(r.id)}" data-decision="received"`));
    if(admin&&['pending_review','awaiting_return'].includes(r.status))actions.push(button('驳回','decide-aftersale','secondary',`data-id="${esc(r.id)}" data-decision="reject"`));
    return `<tr><td><strong>${esc(r.id)}</strong><small>${esc(r.order_id)} · ${stamp(r.created)}</small></td><td>${aftersaleKinds[r.kind]}<small>${esc(r.reason)}</small></td><td>${money(r.amount_cents/100)}</td><td>${badge(aftersaleStatuses[r.status],r.status==='pending_review'?'warn':'')}</td><td>${esc(r.creator)}</td><td>${actions.join(' ')}<small>${esc(r.decision_note||'—')}</small></td></tr>`;
  }));
}
document.addEventListener('click',event=>{
  const target=event.target.closest('[data-view],[data-action],[data-prompt]');if(!target)return;
  if(state.busy){toast('正在生成回复，请稍候');return;}
  const task=target.dataset.view?view(target.dataset.view):target.dataset.prompt?sendMessage(target.dataset.prompt):action(target.dataset.action,target);
  Promise.resolve(task).catch(fail);
});
document.addEventListener('submit',event=>{if(event.target.id==='composer'){event.preventDefault();sendMessage($('#input').value).catch(fail);}});
document.addEventListener('keydown',event=>{if(event.target.id==='input'&&event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();sendMessage(event.target.value).catch(fail);}});
document.addEventListener('input',event=>{if(event.target.id==='search')renderRecords(state.view,event.target.value);});
document.addEventListener('change',async event=>{
  if(event.target.id!=='import-file')return;
  const file=event.target.files[0];if(!file)return;
  try{if(file.size>1000000)throw new Error('导入文件须小于 1 MB');const records=JSON.parse(await file.text());const result=await api('/api/records/'+state.view+'/import',{records});toast(`成功新增 ${result.imported} 条记录`);await recordsView(state.view);}catch(error){fail(error);}finally{event.target.value='';}
});
$('#login-form').addEventListener('submit',async event=>{
  event.preventDefault();$('#auth-error').textContent='';$('#login-submit').disabled=true;
  try{const form=event.target;await api(state.setup?'/api/setup':'/api/login',{username:form.elements.username.value,password:form.elements.password.value});form.elements.password.value='';await boot();}catch(error){$('#auth-error').textContent=error.message;}finally{$('#login-submit').disabled=false;}
});
$('#logout').addEventListener('click',async()=>{try{await api('/api/logout',{});state.cid=null;state.view='overview';await boot();}catch(error){fail(error);}});
$('#close-editor').addEventListener('click',()=>$('#editor').close());$('#cancel-editor').addEventListener('click',()=>$('#editor').close());
$('#editor-form').addEventListener('submit',async event=>{event.preventDefault();if(!editor?.save)return;$('#save-editor').disabled=true;$('#editor-error').textContent='';try{await editor.save(event.target);$('#editor').close();}catch(error){$('#editor-error').textContent=error.message;}finally{$('#save-editor').disabled=false;}});
boot().catch(error=>{document.body.textContent='工作台加载失败：'+error.message;});
