// Canvas picture-motion renderer. No image uploads to an external AI provider.
let dispose = () => {};
export async function mount({api,esc,toast}) {
  dispose();
  const root=document.querySelector('#content');
  const [products,projects]=await Promise.all([api('/api/records/products'),api('/api/videos')]);
  let project=null,images=[],objectURL=null,abort=null;
  dispose=()=>{abort?.();if(objectURL)URL.revokeObjectURL(objectURL);};
  root.innerHTML=`<div class="section-heading"><div><h2>商品广告视频</h2><p class="subtitle">商品资料 → 脚本与分镜 → 图片动效广告</p></div></div><div class="notice">生成15秒无声广告，支持竖屏及横屏。DeepSeek 用于文案规划，画面来自你上传的图片，不是视频模型生成的新镜头。发布前请核对卖点、价格及图片使用权。</div><div class="grid-two"><section class="panel"><h3>1. 规划广告</h3><form id="ad-plan"><label>商品<select name="product_id" required>${products.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select></label><label>目标人群<input name="audience" maxlength="100" value="日常消费者" required></label><label>广告风格<select name="tone"><option>简洁实用</option><option>生活方式</option><option>促销介绍</option></select></label><label>画幅<select name="ratio"><option value="9:16">竖屏 9:16</option><option value="16:9">横屏 16:9</option></select></label><button class="primary" type="submit">生成脚本与分镜</button></form><p class="help">未配置密钥时使用模板，配置后商品资料和人群说明发送给 DeepSeek，图片不会发送。</p><h3>最近作品</h3><select id="ad-history"><option value="">选择已保存的项目…</option>${projects.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${p.mode==='template'?'模板':'AI脚本'}</option>`).join('')}</select></section><section class="panel"><h3>2. 编辑与生成</h3><div id="ad-editor"><p class="subtitle">先选择商品，生成你的第一份分镜。</p></div></section></div><p id="ad-error" role="alert" class="error"></p>`;
  const error=e=>{root.querySelector('#ad-error').textContent=e.message;};
  function download(blob,name) {const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}
  function readScenes() {return project.scenes.map((s,i)=>({title:root.querySelector(`#ad-title-${i}`).value,caption:root.querySelector(`#ad-caption-${i}`).value,visual:root.querySelector(`#ad-visual-${i}`).value}));}
  async function save() {project=await api('/api/videos/save',{id:project.id,version:project.version,images,scenes:readScenes()});}
  function editor() {
    images=[...project.images];
    root.querySelector('#ad-editor').innerHTML=`<div class="notice">${project.mode==='template'?'模板脚本（未调用AI）':'DeepSeek 生成草稿'} · ${project.ratio} · 15秒</div><label>上传商品图片（1–3张 JPEG/PNG）<input id="ad-images" type="file" accept="image/jpeg,image/png" multiple></label><div id="ad-thumbs" class="ad-thumbs"></div><p class="help">图片将压缩并保存在本机私有数据库。依次用于三段分镜，不足三张时循环使用。</p>${project.scenes.map((s,i)=>`<details ${i===0?'open':''}><summary>分镜 ${i+1} · ${i*5}–${(i+1)*5}秒</summary><label>标题<input id="ad-title-${i}" maxlength="30" value="${esc(s.title)}" required></label><label>字幕<textarea id="ad-caption-${i}" maxlength="70" required>${esc(s.caption)}</textarea></label><label>画面说明<textarea id="ad-visual-${i}" maxlength="200" required>${esc(s.visual)}</textarea></label></details>`).join('')}<div class="actions"><button class="secondary" id="ad-save">保存修改</button><button class="secondary" id="ad-script">导出脚本 JSON</button><button class="primary" id="ad-render">生成广告视频</button></div><p class="help">画面说明供拍摄参考；当前合成使用图片推进和字幕。无配音、音乐或自动发布。生成期间请保持此页面在前台。</p><progress id="ad-progress" max="15" value="0" hidden></progress><canvas id="ad-canvas" class="ad-canvas" hidden></canvas><video id="ad-result" class="ad-result" controls hidden></video><a id="ad-download" class="primary" hidden>下载视频</a>`;
    thumbs();
    root.querySelector('#ad-images').onchange=async e=>{
      try {
        if(!e.target.files.length)return;
        if(e.target.files.length>3)throw new Error('最多选择3张图片');
        const next=[];
        for(const file of e.target.files){
          if(!['image/jpeg','image/png'].includes(file.type)||file.size>5*1024*1024)throw new Error('仅支持5MB以内 JPEG/PNG');
          const bitmap=await createImageBitmap(file);
          try {
            if(bitmap.width*bitmap.height>16000000)throw new Error('图片像素过大，请先缩小至1600万像素以内');
            const c=document.createElement('canvas'),scale=Math.min(1,1000/Math.max(bitmap.width,bitmap.height));c.width=Math.round(bitmap.width*scale);c.height=Math.round(bitmap.height*scale);
            c.getContext('2d').drawImage(bitmap,0,0,c.width,c.height);
            let data='';for(let q=.85;q>=.14;q-=.1){data=c.toDataURL('image/jpeg',q);if(data.length<=215000)break;}
            if(data.length>215000)throw new Error('图片压缩后仍过大，请先裁剪');next.push(data);
          } finally {bitmap.close();}
        }
        images=next;thumbs();toast('图片已准备，请保存修改');
      } catch(e){error(e);}
    };
    root.querySelector('#ad-save').onclick=async e=>{e.target.disabled=true;try{await save();toast('分镜与图片已保存');}catch(e){error(e);}finally{e.target.disabled=false;}};
    root.querySelector('#ad-script').onclick=()=>download(new Blob([JSON.stringify({...project,images:undefined,scenes:readScenes()},null,2)],{type:'application/json'}),'advertisement-script.json');
    root.querySelector('#ad-render').onclick=render;
  }
  function thumbs(){root.querySelector('#ad-thumbs').replaceChildren(...images.map(source=>{const img=document.createElement('img');img.src=source;img.alt='商品素材';return img;}));}
  async function render(event) {
    const button=event.target;button.disabled=true;
    let recorder,stream,timer,visibility;
    try {
      root.querySelector('#ad-error').textContent='';
      if(!images.length)throw new Error('请先上传至少一张商品图片');
      if(!window.MediaRecorder||!HTMLCanvasElement.prototype.captureStream)throw new Error('浏览器不支持视频导出，请使用新版 Chrome 或 Edge');
      const mime=['video/webm;codecs=vp9','video/webm;codecs=vp8','video/mp4'].find(m=>MediaRecorder.isTypeSupported(m));
      if(!mime)throw new Error('此浏览器没有可用的视频编码器');
      await save();
      const pictures=await Promise.all(images.map(src=>new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error('图片无法解码'));img.src=src;})));
      const canvas=root.querySelector('#ad-canvas'),ctx=canvas.getContext('2d');[canvas.width,canvas.height]=project.ratio==='9:16'?[720,1280]:[1280,720];canvas.hidden=false;
      const w=canvas.width,h=canvas.height,progress=root.querySelector('#ad-progress');progress.hidden=false;
      function lines(value,x,y,size,maxChars,color='#fff') {ctx.fillStyle=color;ctx.font=`500 ${size}px sans-serif`;const letters=Array.from(value.replace(/\s+/g,' '));for(let j=0;j<letters.length;j+=maxChars)ctx.fillText(letters.slice(j,j+maxChars).join(''),x,y+(j/maxChars)*size*1.45);}
      function draw(seconds){
        const index=Math.min(2,Math.floor(seconds/5)),scene=project.scenes[index],img=pictures[index%pictures.length],zoom=1+(seconds%5)/5*.035;
        ctx.fillStyle='#183d32';ctx.fillRect(0,0,w,h);const area=h*.62,scale=Math.min(w*.86/img.width,area/img.height)*zoom,iw=img.width*scale,ih=img.height*scale;
        ctx.drawImage(img,(w-iw)/2,h*.08+(area-ih)/2,iw,ih);
        const chars=project.ratio==='9:16'?18:35;lines(scene.title,w*.07,h*.76,w*.043,chars);lines(scene.caption,w*.07,h*.84,w*.028,chars+6,'#dceade');
        ctx.font=`${w*.018}px sans-serif`;ctx.fillStyle='#b7d0c3';ctx.fillText(`${project.product_name} · ${index+1}/3`,w*.07,h*.96);
      }
      draw(0);stream=canvas.captureStream(24);recorder=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:2500000});const chunks=[];
      const blob=await new Promise((resolve,reject)=>{
        recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};recorder.onerror=()=>reject(new Error('视频编码失败'));recorder.onstop=()=>resolve(new Blob(chunks,{type:mime}));
        abort=()=>{reject(new Error('已取消生成，请重新打开页面后重试'));if(recorder.state!=='inactive')recorder.stop();};
        visibility=()=>{if(document.hidden)abort();};document.addEventListener('visibilitychange',visibility);
        recorder.start(250);const start=performance.now();timer=setInterval(()=>{if(!canvas.isConnected){abort();return;}const seconds=(performance.now()-start)/1000;draw(Math.min(seconds,14.999));progress.value=Math.min(seconds,15);if(seconds>=15){clearInterval(timer);recorder.stop();}},1000/24);
      });
      if(!blob.size)throw new Error('生成的视频为空，请重试');
      if(objectURL)URL.revokeObjectURL(objectURL);objectURL=URL.createObjectURL(blob);
      const player=root.querySelector('#ad-result'),link=root.querySelector('#ad-download');player.src=objectURL;player.hidden=false;link.href=objectURL;link.download=`product-ad.${mime.includes('mp4')?'mp4':'webm'}`;link.hidden=false;toast('广告视频已生成，可预览和下载');
    } catch(e){error(e);} finally {clearInterval(timer);if(visibility)document.removeEventListener('visibilitychange',visibility);if(recorder&&recorder.state!=='inactive')recorder.stop();stream?.getTracks().forEach(t=>t.stop());abort=null;button.disabled=false;}
  }
  root.querySelector('#ad-plan').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{project=await api('/api/videos',Object.fromEntries(new FormData(e.target)));const option=document.createElement('option');option.value=project.id;option.textContent=project.name+(project.mode==='template'?' · 模板':' · AI脚本');root.querySelector('#ad-history').append(option);root.querySelector('#ad-history').value=project.id;editor();}catch(e){error(e);}finally{b.disabled=false;}};
  root.querySelector('#ad-history').onchange=async e=>{if(!e.target.value)return;try{project=await api('/api/videos/'+e.target.value);editor();}catch(e){error(e);}};
}
