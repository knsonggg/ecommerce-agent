const messages = document.querySelector('#messages');
const input = document.querySelector('#input');
const send = document.querySelector('#send');
const error = document.querySelector('#error');
let history = [];
let busy = false;
const names = {search_products:'商品检索',get_order:'订单查询',analyze_selection:'选品指标计算',lookup_policy:'店铺政策检索'};
function addMessage(role, content, trace = []) {
  const item = document.createElement('article'); item.className = `message ${role}`;
  const label = document.createElement('div'); label.className = 'role'; label.textContent = role === 'user' ? '你 · 店铺客服' : '◈ Commerce Copilot';
  const bubble = document.createElement('div'); bubble.className = 'bubble'; bubble.textContent = content;
  item.append(label, bubble);
  if (trace.length) {
    const details = document.createElement('details'); details.className = 'trace';
    const summary = document.createElement('summary'); summary.textContent = `已查询 ${trace.length} 次工具 · 查看数据依据`;
    details.append(summary);
    for (const tool of trace) {
      const pre = document.createElement('pre'); pre.textContent = `${names[tool.name] || tool.name}\n${JSON.stringify(tool, null, 2)}`; details.append(pre);
    }
    item.append(details);
  }
  messages.append(item); item.scrollIntoView({block:'end',behavior:'smooth'});
  return item;
}
async function submit(text) {
  if (busy || !text.trim()) return;
  busy = true; send.disabled = true; input.disabled = true; error.textContent = '';
  document.querySelector('#welcome').hidden = true;
  addMessage('user', text);
  const waiting = addMessage('assistant', '正在检索资料并整理回复…');
  try {
    const response = await fetch('/api/chat', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,history:history.slice(-20)}),signal:AbortSignal.timeout(290000)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '请求失败');
    waiting.remove(); addMessage('assistant', data.reply, data.trace);
    history.push({role:'user',content:text},{role:'assistant',content:data.reply.slice(0,8000)});
    input.value = '';
  } catch (err) { waiting.remove(); error.textContent = `${err.message} 请重试。`; }
  finally { busy = false; send.disabled = false; input.disabled = false; input.focus(); }
}
document.querySelector('#composer').addEventListener('submit', e => {e.preventDefault(); submit(input.value);});
input.addEventListener('keydown', e => {if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {e.preventDefault(); submit(input.value);}});
document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => submit(button.dataset.prompt)));
document.querySelector('#new-chat').addEventListener('click', () => {if (busy) return; history=[]; messages.replaceChildren(); error.textContent=''; document.querySelector('#welcome').hidden=false; input.value='';});
fetch('/api/status').then(r=>r.json()).then(d=>{document.querySelector('#mode').textContent=d.mode==='demo'?'● 演示模式 · 无需密钥':'● DeepSeek 已配置';}).catch(()=>{error.textContent='服务连接失败，请刷新页面。';});
