const $ = id => document.getElementById(id);
let token, jobId, job, selected = [], polling, creating = false, syncTimer;
let savedClientId = '', spotifyConnected = false, spotifyCanWrite = false, spotifyCanModify = false, loginBusy = false;
let youtubeConnected = false, youtubeAccount = '';

// --- Language ---------------------------------------------------------------
// Traditional Chinese strings are the source; i18n/en.json maps them to English.
// Server messages arrive in Chinese and are translated here too, so the API never changes.
let lang = 'zh', dictionary = {strings: {}, patterns: []};
function t(text, params) {
  let out = text;
  if (lang === 'en' && typeof text === 'string') {
    if (dictionary.strings[text] !== undefined) out = dictionary.strings[text];
    else for (const [pattern, replacement] of dictionary.patterns) { const m = text.match(pattern); if (m) { out = replacement.replace(/\$(\d)/g, (_, i) => m[+i]); break; } }
  }
  if (params) for (const [k, v] of Object.entries(params)) out = out.split('{' + k + '}').join(String(v));
  return out;
}
function applyLanguage() {
  document.documentElement.lang = lang === 'en' ? 'en' : 'zh-Hant';
  for (const el of document.querySelectorAll('[data-i18n]')) {
    if (el.dataset.zh === undefined) el.dataset.zh = el.textContent.trim();
    if (el.dataset.zh) el.textContent = t(el.dataset.zh);
  }
  for (const el of document.querySelectorAll('[data-i18n-attr]')) {
    for (const attr of el.dataset.i18nAttr.split(' ')) {
      const key = 'zh_' + attr.replace(/-/g, '_');
      if (el.dataset[key] === undefined) el.dataset[key] = el.getAttribute(attr) || '';
      el.setAttribute(attr, t(el.dataset[key]));
    }
  }
  $('lang-toggle').textContent = lang === 'en' ? '中文' : 'EN';
  $('lang-toggle').setAttribute('aria-label', lang === 'en' ? '切換成中文' : 'Switch to English');
  $('history-toggle').setAttribute('aria-label', t(document.body.classList.contains('sidebar-open') ? '收起比對紀錄' : '開啟比對紀錄'));
  $('history-toggle').title = t('比對紀錄');
}
async function setLanguage(next) {
  if (next === 'en' && !Object.keys(dictionary.strings).length) {
    try { const data = await (await fetch('/i18n/en.json')).json(); dictionary = {strings: data.strings || {}, patterns: (data.patterns || []).map(([p, r]) => [new RegExp(p), r])}; }
    catch (_) { return; }
  }
  lang = next;
  try { localStorage.setItem('songferry.lang', lang); } catch (_) {}
  applyLanguage();
  configureDirection();
  if (token) { spotifyStatus(); youtubeStatus(); if (browsersLoaded) renderImportButtons(); renderLinksSummary(); renderPlaylistOptions(); }
  if (job) { applyJobLabels(); renderRows(); renderProgress(); if (job.result) showResult(job.result); }
  if (lastNotice && !$('notice').hidden) notice(lastNotice.text, lastNotice.error, lastNotice.params);
  if (token) loadHistory();
}
function initialLanguage() {
  let saved = null;
  try { saved = localStorage.getItem('songferry.lang'); } catch (_) {}
  if (saved === 'en' || saved === 'zh') return saved;
  return (navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en';
}
function locale() { return lang === 'en' ? 'en-US' : 'zh-TW'; }
function joinArtists(list) { return (list || []).join(lang === 'en' ? ', ' : '、'); }

// --- Helpers ----------------------------------------------------------------
async function api(path, data, method) {
  const options = data === undefined ? {} : {method:method||'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':token}, body:JSON.stringify(data)};
  const response = await fetch('/api/' + path, options);
  const value = await response.json();
  if (!response.ok) throw Error(value.error || '請求失敗');
  return value;
}
let lastNotice=null;
function notice(text, error=false, params) { lastNotice={text,error,params}; $('notice').hidden=false; $('notice').textContent=t(text,params); $('notice').className=error?'error':''; }
function node(tag, text, className) { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(className)el.className=className; return el; }
function duration(seconds) { const total=Math.round(seconds||0); return total ? `${Math.floor(total/60)}:${String(total%60).padStart(2,'0')}` : t('長度未知'); }
function candidateId(candidate){return candidate.id||candidate.videoId;}
function reverseDirection(){return $('direction').value==='youtube-to-spotify';}
function destinationName(){return reverseDirection()?'Spotify':'YouTube Music';}
function isLinksJob(){return job?.kind==='links';}
function existingPlaylist(){return isLinksJob()&&job.playlist?.mode==='existing'?job.playlist:null;}
function directionLabel(entry){
  const to=entry.direction==='youtube-to-spotify'?'SP':'YT';
  if(entry.kind==='links')return t('連結 → {to}',{to});
  return (to==='SP'?'YT':'SP')+' → '+to;
}

// --- Pages ------------------------------------------------------------------
const VIEWS=['accounts','transfer','links'];
let currentView='accounts';
function jobView(j){return j?.kind==='links'?'links':'transfer';}
function routeFromPath(){const p=location.pathname.replace(/^\/+|\/+$/g,'');return VIEWS.includes(p)?p:null;}
function showView(name, push){
  if(!VIEWS.includes(name))name='accounts';
  currentView=name;
  for(const section of document.querySelectorAll('.view'))section.hidden=section.dataset.view!==name;
  for(const link of document.querySelectorAll('.tabs a'))if(link.dataset.route===name)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
  const slot=document.querySelector(`.view[data-view="${name}"] .review-slot`);
  if(slot){slot.append($('notice'),$('review'));}
  try{localStorage.setItem('songferry.view',name);}catch(_){}
  if(push){const url='/'+name+(jobId&&jobView(job)===name?'?'+new URLSearchParams({job:jobId}):'');history.pushState(null,'',url);}
  if(name==='links'&&token)loadPlaylists();
  window.scrollTo({top:0});
}
function jobUrl(){return '/'+jobView(job)+(jobId?'?'+new URLSearchParams({job:jobId}):'');}
document.addEventListener('click',e=>{
  const link=e.target.closest('a[data-route]');
  if(!link||e.metaKey||e.ctrlKey||e.shiftKey||e.button!==0)return;
  e.preventDefault();showView(link.dataset.route,true);
});
window.addEventListener('popstate',()=>{const route=routeFromPath();if(route)showView(route,false);});
function renderAccountStrips(){
  for(const strip of document.querySelectorAll('[data-strip]')){
    strip.replaceChildren();
    const sp=node('span');sp.append(node('b','Spotify '),node('span',t(spotifyConnected?'已連接':'尚未連接'),spotifyConnected?'ok':'warn'));
    const yt=node('span');yt.append(node('b','YouTube Music '),node('span',youtubeConnected?(youtubeAccount?t('已連接 · {account}',{account:youtubeAccount}):t('已連接')):t('尚未設定'),youtubeConnected?'ok':'warn'));
    const link=node('a',t('管理帳號 →'));link.href='/accounts';link.dataset.route='accounts';
    strip.append(sp,yt,link);
  }
}

// --- Review (shared by transfer and links jobs) ------------------------------
function saveSelections(){
  if(!jobId)return;
  // Choices live on the server with the job, so a restart or another tab shows the same picks.
  clearTimeout(syncTimer);
  const id=jobId, payload={selections:selected.slice(), title:$('target-title').value};
  syncTimer=setTimeout(()=>api('jobs/'+encodeURIComponent(id)+'/selections',payload).catch(()=>{}),400);
}
function configureDirection(){
  const reverse=reverseDirection();
  $('source-label').textContent=t('{service} 歌單連結',{service:reverse?'YouTube Music':'Spotify'});
  $('playlist-url').placeholder=reverse?'https://music.youtube.com/playlist?list=…':'https://open.spotify.com/playlist/…';
  $('direction-hint').textContent=t(reverse?'將 YouTube Music 歌單轉入 Spotify。建立前需連接 Spotify 並授權建立私人歌單。':'將 Spotify 歌單轉入 YouTube Music。');
  sessionStorage.setItem('songferryDirection',$('direction').value);
  applyJobLabels();updateLoginButtons();updateCount();
}
function applyJobLabels(){
  const reverse=reverseDirection(), existing=existingPlaylist();
  $('source-column').textContent=isLinksJob()?t('貼上的連結'):t('{service} 原始歌曲',{service:reverse?'YOUTUBE MUSIC':'SPOTIFY'});
  $('destination-column').textContent=t('{service} 對應版本',{service:reverse?'SPOTIFY':'YOUTUBE MUSIC'});
  $('review-help').textContent=isLinksJob()?t('已在目標平台的連結會直接加入；其他平台的連結會比對候選版本，預設選取第一個。略過的連結不會加入。'):t('每首預設選取第一個候選版本；沒有搜尋結果的歌曲才會略過。版本警示與分數保留供你確認。');
  $('finish-new').hidden=!!existing;$('finish-existing').hidden=!existing;
  if(existing)$('finish-existing-name').textContent=existing.name||existing.id;
  if(!creating)$('create').textContent=existing?t('確認並加入 {destination} 歌單 ↗',{destination:destinationName()}):t('確認並建立 {destination} 私人歌單 ↗',{destination:destinationName()});
}
function updateCount() {
  const text=t('已選擇 {selected} / {total} 首；略過的歌曲不會加入。保留原始順序與重複歌曲。',{selected:selected.filter(Boolean).length,total:selected.length});
  $('selected-count').textContent=text;$('selected-count-existing').textContent=text;
  $('create').disabled=creating || job?.status!=='ready' || !selected.some(Boolean) || job?.attempted || (reverseDirection()&&!spotifyCanWrite);
  $('direction').disabled=creating||job?.status==='matching';
}
function renderProgress(){ if(!job)return; $('progress').textContent=t('{done} / {total} 首',{done:job.rows.length,total:job.total||'…'}); }
function renderRows() {
  $('tracks').replaceChildren();
  job.rows.forEach((row,index)=>{
    const container=node('div',undefined,'track'), source=node('div',undefined,'source'), info=node('div');
    source.append(node('span',String(index+1).padStart(2,'0'),'number'));
    info.append(node('div',row.track.title,'song-title'),node('div',`${joinArtists(row.track.artists)} · ${duration(row.track.duration)}`,'artist'));
    if(row.track.url){const link=node('a',t('原始連結 ↗'),'source-link');link.href=row.track.url;link.target='_blank';link.rel='noreferrer';info.append(link);}
    source.append(info);
    const target=node('div'), select=node('select'); select.setAttribute('aria-label',t('第 {n} 首歌曲的對應版本',{n:index+1}));
    const skip=node('option',t('略過這首歌曲'));skip.value='';select.append(skip);
    row.candidates.forEach(candidate=>{const option=node('option',`${candidate.title} — ${joinArtists(candidate.artists)} · ${duration(candidate.duration)} · ${t('{score} 分',{score:candidate.score})}`);option.value=candidateId(candidate);select.append(option);});
    select.value=selected[index]||'';
    const warning=node('div',undefined,'warning'),tools=node('div',undefined,'song-tools'),preview=node('a',t('試聽 ↗')),searchButton=node('button',t('手動搜尋'),'search-button');
    preview.target='_blank';preview.rel='noreferrer';
    const direct=isLinksJob()&&row.track.source===job.target;
    function updateDetails(){const c=row.candidates.find(c=>candidateId(c)===selected[index]);warning.textContent=c?(c.warnings.map(w=>t(w)).join(' · ')||(direct?t('連結本身就在目標平台，會直接加入。'):t('歌名、歌手與長度比對相近；仍建議試聽確認。'))):(row.error?t(row.error):t('尚未選擇版本，請確認候選歌曲或手動搜尋。'));preview.hidden=!c;if(c)preview.href=c.url||('https://music.youtube.com/watch?v='+encodeURIComponent(c.videoId));}
    select.onchange=()=>{selected[index]=select.value||null;saveSelections();updateDetails();updateCount();};
    tools.append(preview,searchButton);target.append(select,warning,tools);updateDetails();
    searchButton.onclick=()=>{
      if(target.querySelector('.manual'))return;
      const form=node('form',undefined,'manual'),input=node('input'),button=node('button',t('搜尋'),'secondary');input.value=row.track.title+' '+(row.track.artists||[]).join(' ');input.setAttribute('aria-label',t('手動搜尋歌名與歌手'));button.type='submit';form.append(input,button);target.append(form);input.focus();
      form.onsubmit=async e=>{e.preventDefault();button.disabled=true;try{const result=await api('search',{job:jobId,index,query:input.value});for(const c of result.candidates)if(!row.candidates.some(x=>candidateId(x)===candidateId(c)))row.candidates.push(c);if(!selected[index]&&result.candidates.length)selected[index]=candidateId(result.candidates[0]);saveSelections();renderRows();notice(result.candidates.length?'候選版本已更新，請選擇對應歌曲。':'沒有找到候選歌曲，試試其他歌名或歌手。');}catch(error){notice(error.message,true);button.disabled=false;}};
    };
    container.append(source,target);$('tracks').append(container);
  });
  updateCount();
}
function showResult(result){
  const box=$('result');box.hidden=false;box.replaceChildren();
  const appended=result.added!==undefined;
  const headline=appended?(result.verified?t('歌曲已加入歌單，內容已核對。'):t('歌曲已加入歌單，請在 {destination} 確認內容。',{destination:destinationName()})):result.complete===false?t('歌單已建立，但部分歌曲尚未確認加入。'):result.verified?t('歌單已建立，歌曲順序已核對。'):t('歌單已建立，請在 {destination} 確認歌曲內容。',{destination:destinationName()});
  let counts;
  if(appended)counts=t('加入 {added} 首，{skipped} 首原本就在歌單裡，歌單現有 {actual} 首。',{added:result.added,skipped:result.skipped_existing||0,actual:result.actual??'?'});
  else counts=result.actual!==null&&result.actual!==undefined?t('送出 {requested} 首，讀回 {actual} 首。',{requested:result.requested,actual:result.actual}):t('送出 {requested} 首。',{requested:result.requested});
  box.append(node('strong',headline),node('p',counts));
  if(result.warning)box.append(node('p',t(result.warning)));
  const link=node('a',t('打開 {destination} 歌單 ↗',{destination:destinationName()}));link.href=result.url;link.target='_blank';link.rel='noreferrer';box.append(link);
}
async function poll() {
  try {
    const latest=await api('jobs/'+encodeURIComponent(jobId));
    const first=!job, changed=first||latest.rows.length!==job.rows.length||latest.status!==job.status;
    const historyChanged=!first&&(latest.status!==job.status||(latest.title&&!job.title));
    job=latest;
    if(latest.direction&&$('direction').value!==latest.direction){$('direction').value=latest.direction;configureDirection();}
    if(first){
      if(currentView!==jobView(job))showView(jobView(job),false);
      history.replaceState(null,'',jobUrl());
      applyJobLabels();
      selected=job.rows.map(r=>r.selected??null);
      if(job.source_url&&!$('playlist-url').value)$('playlist-url').value=job.source_url;
      if(job.target_title)$('target-title').value=job.target_title;
      if(job.result)showResult(job.result);
    } else if(changed) {
      selected=job.rows.map((r,i)=>i<selected.length?selected[i]:(r.selected??null));
    }
    renderProgress();
    $('progress-bar').style.width=(job.total?job.rows.length/job.total*100:0)+'%';
    const heading=job.title||(isLinksJob()?t('{n} 個連結',{n:job.total||job.rows.length}):'');
    if(heading){$('playlist-title').textContent=heading;if(!$('target-title').value&&job.title)$('target-title').value=job.title;}
    if(changed)renderRows();
    if(historyChanged)loadHistory();
    if(job.status==='matching'){polling=setTimeout(poll,1200);return;}
    $('import-button').disabled=false;$('links-start').disabled=false;
    if(job.status==='error')notice(job.error,true);
    else if(job.result)notice(job.result.complete===false?job.result.warning:job.result.verified?(job.result.added!==undefined?'歌曲已加入歌單。':'這份歌單已轉移完成。'):'歌單已建立，但未能完整核對內容，請開啟歌單檢查。',job.result.complete===false);
    else if(job.attempted)notice('此工作已送出建立要求但結果未確認，請到 {destination} 檢查是否已有歌單。',true,{destination:destinationName()});
    else notice(reverseDirection()&&!spotifyCanWrite?'比對完成。已選 {selected} 首，略過 {skipped} 首。 請按「授權 Spotify 建立歌單」，完成後即可建立。':'比對完成。已選 {selected} 首，略過 {skipped} 首。',false,{selected:selected.filter(Boolean).length,skipped:job.rows.length-selected.filter(Boolean).length});
    updateCount();
  } catch(error) {sessionStorage.removeItem('songferryJob');notice('{error} 請貼上來源連結重新讀取歌單。',true,{error:t(error.message)});$('import-button').disabled=false;$('links-start').disabled=false;$('direction').disabled=false;}
}
function resetWorkspace(){
  clearTimeout(polling);clearTimeout(syncTimer);job=null;jobId=null;selected=[];creating=false;
  sessionStorage.removeItem('songferryJob');
  $('review').hidden=true;$('result').hidden=true;$('notice').hidden=true;$('playlist-url').value='';$('target-title').value='';$('import-button').disabled=false;$('links-start').disabled=false;
  history.replaceState(null,'','/'+currentView);
  configureDirection();highlightHistory();
}
function startJob(id){
  clearTimeout(polling);clearTimeout(syncTimer);
  jobId=id;job=null;selected=[];
  sessionStorage.setItem('songferryJob',jobId);
  $('target-title').value='';$('result').hidden=true;$('review').hidden=false;$('tracks').replaceChildren();$('progress').textContent='';$('progress-bar').style.width='0';
}
function openJob(jid){
  if(creating)return;
  startJob(jid);
  $('playlist-url').value='';$('notice').hidden=true;$('import-button').disabled=true;$('links-start').disabled=true;
  $('playlist-title').textContent=t('讀取紀錄中…');
  if(isNarrow())setHistoryOpen(false);
  highlightHistory();
  poll().then(()=>$('review').scrollIntoView({behavior:'smooth',block:'start'}));
}

// --- History sidebar ----------------------------------------------------------
function highlightHistory(){
  for(const item of $('history-list').children)item.classList.toggle('active',item.dataset.id===jobId);
}
function statusLabel(entry){
  if(entry.status==='matching')return ['比對中','live'];
  if(entry.status==='error')return ['已中斷','err'];
  if(entry.url&&entry.attempted)return [entry.kind==='links'?'已加入':'已建立','ok'];
  if(entry.attempted)return ['建立結果未確認','err'];
  return ['待確認',''];
}
function formatTime(seconds){
  if(!seconds)return '';
  return new Date(seconds*1000).toLocaleString(locale(),{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});
}
async function loadHistory(){
  let data;
  try{data=await api('jobs');}catch(_){return;}
  const list=$('history-list');list.replaceChildren();
  $('history-empty').hidden=data.jobs.length>0;
  for(const entry of data.jobs){
    const item=node('li',undefined,'history-item');item.dataset.id=entry.id;
    const open=node('button',undefined,'history-open');open.type='button';
    const title=entry.title||(entry.kind==='links'?t('{n} 個連結',{n:entry.total||entry.rows}):t(entry.status==='matching'?'讀取中…':'未命名歌單'));
    open.append(node('span',title,'history-title'));
    const meta=node('span',undefined,'history-meta');
    const [label,tone]=statusLabel(entry);
    const count=t('{count} 首',{count:entry.rows+(entry.total&&entry.total!==entry.rows?' / '+entry.total:'')});
    meta.append(node('b',directionLabel(entry)),document.createTextNode(` · ${count} · ${formatTime(entry.created_at)} · `),node('span',t(label),tone));
    open.append(meta);open.onclick=()=>openJob(entry.id);
    const remove=node('button','×','history-delete');remove.type='button';remove.setAttribute('aria-label',t('刪除這筆比對紀錄'));remove.title=t('刪除紀錄（不會刪除已建立的歌單）');
    remove.onclick=async()=>{
      if(!remove.classList.contains('confirm')){remove.classList.add('confirm');remove.textContent=t('確定刪除？');setTimeout(()=>{remove.classList.remove('confirm');remove.textContent='×';},4000);return;}
      remove.disabled=true;
      try{await api('jobs/'+encodeURIComponent(entry.id),{}, 'DELETE');if(entry.id===jobId)resetWorkspace();await loadHistory();}
      catch(error){notice(error.message,true);remove.disabled=false;}
    };
    item.append(open,remove);list.append(item);
  }
  highlightHistory();
}

// --- Spotify ------------------------------------------------------------------
function spotifyMessage(text, error=false) {
  for (const id of ['spotify-message','spotify-setup-message']) {
    $(id).hidden=false;
    $(id).textContent=t(text);
    $(id).className=error?'account-message error':'account-message';
  }
}
function updateLoginButtons() {
  $('login').disabled=!token || loginBusy;
  $('save-config').disabled=!token || loginBusy;
  $('login').textContent=t(loginBusy?'正在前往 Spotify…':reverseDirection()&&!spotifyCanWrite?'授權 Spotify 建立歌單 ↗':spotifyConnected?'重新連接 Spotify ↗':savedClientId?'連接 Spotify ↗':'設定並連接 Spotify ↗');
  $('logout').hidden=!spotifyConnected;
}
function showSpotifySetup(message, error=false) {
  showView('accounts',true);
  $('setup').open=true;
  spotifyMessage(message, error);
  $('client-id').setAttribute('aria-invalid',error?'true':'false');
  $('client-id').focus({preventScroll:true});
  $('spotify-config-form').scrollIntoView({block:'center',behavior:'instant'});
}
function spotifyStatus(){
  $('sp-status').textContent=t(spotifyConnected?'已連接':savedClientId?'已設定 · 尚未連接':'待完成首次設定');
  $('sp-status').className='status'+(spotifyConnected?' connected':'');
  renderAccountStrips();
}
async function connectSpotify(event, direction) {
  event?.preventDefault();
  if(loginBusy || !token)return;
  const id=$('client-id').value.trim();
  if(!id) {
    showSpotifySetup('請先在 Spotify Developer Dashboard 建立 App，將上方 Redirect URI 加到 App，然後貼上 Client ID。');
    return;
  }
  if(!/^[a-fA-F0-9]{32}$/.test(id)) {
    showSpotifySetup('Client ID 應為 32 位十六進位字元，請從 Spotify App 設定複製完整的 Client ID。',true);
    return;
  }
  $('client-id').setAttribute('aria-invalid','false');
  loginBusy=true;updateLoginButtons();
  try {
    if(id!==savedClientId) {
      await api('config',{client_id:id});savedClientId=id;spotifyConnected=false;spotifyStatus();
    }
    const wantWrite=direction==='youtube-to-spotify'||reverseDirection();
    const data=await api('login',{direction:wantWrite?'youtube-to-spotify':'spotify-to-youtube'});
    spotifyMessage(wantWrite?'正在前往 Spotify，授權讀取與建立私人歌單…':'正在前往 Spotify 登入並授權讀取歌單…');
    window.location.assign(data.url);
  } catch(error) {
    showSpotifySetup(error.message,true);
  } finally {
    loginBusy=false;updateLoginButtons();
  }
}
$('spotify-config-form').onsubmit=connectSpotify;
$('login').onclick=connectSpotify;
$('logout').onclick=async()=>{
  $('logout').disabled=true;
  try{await api('logout',{});spotifyConnected=false;spotifyCanWrite=false;spotifyCanModify=false;spotifyStatus();spotifyMessage('已登出 Spotify，本機保存的登入資訊已刪除。');updateLoginButtons();updateCount();}
  catch(error){spotifyMessage(error.message,true);}
  finally{$('logout').disabled=false;}
};

// --- YouTube Music ------------------------------------------------------------
function youtubeStatus(){
  $('yt-status').textContent=youtubeConnected?(youtubeAccount?t('已連接 · {account}',{account:youtubeAccount}):t('已連接')):t('尚未設定');
  $('yt-status').className='status'+(youtubeConnected?' connected':'');
  $('yt-logout').hidden=!youtubeConnected;
  renderAccountStrips();
}
function youtubeMessage(id, text, error=false, params){ $(id).hidden=false; $(id).textContent=t(text,params); $(id).className=error?'account-message error':'account-message'; }
let browsersLoaded=false, availableBrowsers=[], currentBrowser=null;
async function detectBrowser(){
  // Which browser is this page open in? Brave hides itself from brands but exposes navigator.brave.
  try{if(navigator.brave&&await navigator.brave.isBrave())return 'brave';}catch(_){}
  const brands=(navigator.userAgentData?.brands||[]).map(b=>b.brand);
  if(brands.includes('Microsoft Edge'))return 'edge';
  if(brands.includes('Google Chrome'))return 'chrome';
  if(brands.includes('Chromium'))return 'chromium';
  const ua=navigator.userAgent;
  if(/Firefox\//.test(ua))return 'firefox';
  if(/Edg\//.test(ua))return 'edge';
  if(/Chrome\//.test(ua)&&!/OPR\//.test(ua))return 'chrome';
  return null;
}
function importButton(browser, label, className){
  const button=node('button',label,className);button.type='button';button.dataset.browser=browser.id;button.dataset.name=browser.name;
  button.onclick=()=>importFromBrowser(browser.id,browser.name);
  return button;
}
function renderImportButtons(){
  const main=$('yt-import-buttons'), others=$('yt-import-others'), detected=$('yt-import-detected');
  main.replaceChildren();others.replaceChildren();others.hidden=true;detected.replaceChildren();
  const current=availableBrowsers.find(b=>b.id===currentBrowser);
  const rest=availableBrowsers.filter(b=>b.id!==currentBrowser);
  if(current){
    main.append(importButton(current,t('從瀏覽器匯入登入'),'secondary'));
    detected.append(document.createTextNode(t('偵測到你正在使用 {browser}。',{browser:current.name})));
    if(rest.length){const link=node('button',t('不是這個瀏覽器？'),'link-button');link.type='button';link.onclick=()=>{others.hidden=!others.hidden;};detected.append(document.createTextNode(' '),link);}
  } else {
    detected.append(document.createTextNode(t(availableBrowsers.length?'無法判斷目前的瀏覽器，請選擇已登入 YouTube Music 的那一個：':'')));
    others.hidden=false;
  }
  for(const browser of rest)others.append(importButton(browser,t('從 {browser} 匯入登入',{browser:browser.name}),'secondary'));
  $('yt-import').hidden=!availableBrowsers.length;
}
async function loadBrowsers(){
  if(browsersLoaded){renderImportButtons();return;}
  let data;
  try{data=await api('youtube/browsers');}catch(_){return;}
  browsersLoaded=true;availableBrowsers=data.browsers;currentBrowser=await detectBrowser();
  renderImportButtons();
}
async function importFromBrowser(id,name){
  for(const b of [...$('yt-import-buttons').children,...$('yt-import-others').children])b.disabled=true;
  youtubeMessage('yt-import-message','正在讀取 {browser} 的登入資料…如果跳出鑰匙圈視窗，請按「允許」。',false,{browser:name});
  try{const data=await api('youtube/import',{browser:id});youtubeConnected=true;youtubeAccount=data.account||'';youtubeStatus();$('yt-connect').hidden=true;$('yt-import-message').hidden=true;youtubeMessage('yt-message','已連接 YouTube Music：{account}。登入已保存在本機。',false,{account:youtubeAccount});}
  catch(error){youtubeMessage('yt-import-message',error.message,true);}
  finally{for(const b of [...$('yt-import-buttons').children,...$('yt-import-others').children])b.disabled=false;}
}
$('yt-connect-button').onclick=()=>{$('yt-connect').hidden=false;$('yt-panel-message').hidden=true;$('yt-import-message').hidden=true;loadBrowsers();$('yt-connect').scrollIntoView({behavior:'smooth',block:'center'});};
$('yt-cancel').onclick=()=>{$('yt-connect').hidden=true;$('yt-headers').value='';};
$('yt-save').onclick=async()=>{
  const text=$('yt-headers').value;
  if(!text.trim()){youtubeMessage('yt-panel-message','請先貼上從瀏覽器複製的請求。',true);$('yt-headers').focus();return;}
  $('yt-save').disabled=true;youtubeMessage('yt-panel-message','正在驗證 YouTube Music 登入…');
  try{const data=await api('youtube/connect',{text});youtubeConnected=true;youtubeAccount=data.account||'';youtubeStatus();$('yt-headers').value='';$('yt-connect').hidden=true;youtubeMessage('yt-message','已連接 YouTube Music：{account}。登入已保存在本機。',false,{account:youtubeAccount});}
  catch(error){youtubeMessage('yt-panel-message',error.message,true);}
  finally{$('yt-save').disabled=false;}
};
$('yt-logout').onclick=async()=>{
  $('yt-logout').disabled=true;
  try{await api('youtube/disconnect',{});youtubeConnected=false;youtubeAccount='';youtubeStatus();youtubeMessage('yt-message','已登出 YouTube Music，本機保存的登入資訊已刪除。');}
  catch(error){youtubeMessage('yt-message',error.message,true);}
  finally{$('yt-logout').disabled=false;}
};

// --- Transfer page --------------------------------------------------------------
$('import-form').onsubmit=async e=>{e.preventDefault();$('import-button').disabled=true;try{const data=await api('jobs',{url:$('playlist-url').value,direction:$('direction').value});startJob(data.id);history.replaceState(null,'','/transfer?'+new URLSearchParams({job:jobId}));notice('正在讀取歌單並逐首搜尋，較長的歌單需要幾分鐘。');loadHistory();await poll();}catch(error){notice(error.message,true);$('import-button').disabled=false;}};
$('direction').onchange=()=>{resetWorkspace();};

// --- Links page ------------------------------------------------------------------
let parseTimer, parsed={items:[],unsupported:[],counts:{youtube:0,spotify:0}}, playlistCache={};
function renderLinksSummary(){
  const total=parsed.items.length;
  $('links-summary').textContent=$('links-text').value.trim()?(total?t('找到 {total} 個歌曲連結：YouTube {youtube} 個、Spotify {spotify} 個。',{total,youtube:parsed.counts.youtube,spotify:parsed.counts.spotify}):t('還沒有找到 YouTube、YouTube Music 或 Spotify 的歌曲連結。')):'';
  const box=$('links-unsupported');box.hidden=!parsed.unsupported.length;
  if(parsed.unsupported.length){box.querySelector('summary').textContent=t('略過 {n} 個不支援的連結',{n:parsed.unsupported.length});const list=$('links-unsupported-list');list.replaceChildren();for(const u of parsed.unsupported)list.append(node('li',`${u.url} — ${t(u.reason)}`));}
}
$('links-text').oninput=()=>{clearTimeout(parseTimer);parseTimer=setTimeout(async()=>{const text=$('links-text').value;if(!text.trim()){parsed={items:[],unsupported:[],counts:{youtube:0,spotify:0}};renderLinksSummary();return;}try{parsed=await api('links/parse',{text});renderLinksSummary();}catch(_){}},300);};
function renderPlaylistOptions(){
  const target=$('links-target').value, select=$('links-playlist'), keep=select.value;
  select.replaceChildren();
  const fresh=node('option',t('建立新歌單'));fresh.value='__new__';select.append(fresh);
  for(const p of playlistCache[target]||[]){const o=node('option',p.count!==undefined&&p.count!==null?`${p.name} (${p.count})`:p.name);o.value=p.id;o.dataset.name=p.name;select.append(o);}
  select.value=[...select.options].some(o=>o.value===keep)?keep:'__new__';
  $('links-title-box').hidden=select.value!=='__new__';
}
async function loadPlaylists(){
  const target=$('links-target').value;
  $('links-message').hidden=true;$('links-authorize').hidden=true;
  if(playlistCache[target]){renderPlaylistOptions();return;}
  if(target==='spotify'&&!spotifyConnected){youtubeMessage('links-message','請先到帳號頁連接 Spotify。',true);renderPlaylistOptions();return;}
  if(target==='spotify'&&!spotifyCanModify){youtubeMessage('links-message','Spotify 尚未授權建立或修改歌單，請先授權（已連接過的帳號也需要重新授權一次）。',true);$('links-authorize').hidden=false;renderPlaylistOptions();return;}
  if(target==='youtube'&&!youtubeConnected){youtubeMessage('links-message','請先到帳號頁連接 YouTube Music。',true);renderPlaylistOptions();return;}
  try{const data=await api('playlists/'+target);playlistCache[target]=data.playlists;}
  catch(error){youtubeMessage('links-message',error.message,true);}
  renderPlaylistOptions();
}
$('links-target').onchange=loadPlaylists;
$('links-playlist').onchange=renderPlaylistOptions;
$('links-authorize').onclick=e=>connectSpotify(e,'youtube-to-spotify');
$('links-start').onclick=async()=>{
  const text=$('links-text').value;
  if(!text.trim()){youtubeMessage('links-message','請先貼上含有歌曲連結的文字。',true);$('links-text').focus();return;}
  const select=$('links-playlist'), option=select.options[select.selectedIndex];
  const playlist=select.value==='__new__'?{mode:'new',name:$('links-title').value}:{mode:'existing',id:select.value,name:option?.dataset.name||option?.textContent||''};
  $('links-start').disabled=true;$('links-message').hidden=true;
  try{const data=await api('links/jobs',{text,target:$('links-target').value,playlist});startJob(data.id);if(playlist.mode==='new'&&playlist.name)$('target-title').value=playlist.name;history.replaceState(null,'','/links?'+new URLSearchParams({job:jobId}));notice('正在讀取每個連結並比對，連結多時需要幾分鐘。');loadHistory();await poll();}
  catch(error){youtubeMessage('links-message',error.message,true);$('links-start').disabled=false;}
};

// --- Create / add ----------------------------------------------------------------
$('create').onclick=async()=>{
  creating=true;clearTimeout(syncTimer);updateCount();$('import-button').disabled=true;$('links-start').disabled=true;$('create').textContent=t(existingPlaylist()?'正在加入歌單…':'正在建立歌單…');
  try{const result=await api('create',{job:jobId,title:$('target-title').value,selections:selected,lang});job.attempted=true;job.result=result;showResult(result);notice(result.complete===false?result.warning:result.verified?(result.added!==undefined?'歌曲已加入歌單。':'轉移完成。'):'建立成功，但未能完整核對內容，請開啟歌單檢查。',result.complete===false);if(existingPlaylist())delete playlistCache[job.target];}
  catch(error){notice(error.message,true);try{job=await api('jobs/'+encodeURIComponent(jobId));}catch(_){job.attempted=true;}}
  finally{creating=false;$('import-button').disabled=false;$('links-start').disabled=false;applyJobLabels();updateCount();loadHistory();}
};
$('new-job').onclick=()=>{if(creating||job?.status==='matching'){notice('目前有工作正在進行，完成後再開始新的比對。',true);return;}resetWorkspace();if(currentView==='accounts')showView('transfer',true);if(isNarrow())setHistoryOpen(false);(currentView==='links'?$('links-text'):$('playlist-url')).focus();};
$('target-title').oninput=()=>{if(job?.status==='ready'&&!job.attempted)saveSelections();};
$('lang-toggle').onclick=()=>setLanguage(lang==='en'?'zh':'en');

// --- Sidebar open/close: inline column on wide screens, overlay drawer on narrow ones; choice remembered.
function isNarrow(){return matchMedia('(max-width:960px)').matches;}
function setHistoryOpen(open){
  document.body.classList.toggle('sidebar-open',open);
  $('history-toggle').setAttribute('aria-expanded',String(open));
  $('history-toggle').setAttribute('aria-label',t(open?'收起比對紀錄':'開啟比對紀錄'));
  $('history-backdrop').hidden=!(open&&isNarrow());
  try{localStorage.setItem('songferry.history',open?'open':'closed');}catch(_){}
}
function historyOpen(){return document.body.classList.contains('sidebar-open');}
$('history-toggle').onclick=()=>setHistoryOpen(!historyOpen());
$('history-collapse').onclick=()=>{setHistoryOpen(false);$('history-toggle').focus();};
$('history-backdrop').onclick=()=>setHistoryOpen(false);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&historyOpen()&&isNarrow())setHistoryOpen(false);});
(function(){let saved=null;try{saved=localStorage.getItem('songferry.history');}catch(_){}
  document.body.classList.add('no-transition');setHistoryOpen(saved?saved==='open':!isNarrow());requestAnimationFrame(()=>document.body.classList.remove('no-transition'));})();

// --- Boot ------------------------------------------------------------------------
(function(){let saved=null;try{saved=localStorage.getItem('songferry.view');}catch(_){}
  const route=routeFromPath();showView(route||(VIEWS.includes(saved)?saved:'accounts'),false);
  if(!route)history.replaceState(null,'','/'+currentView+location.search);})();
setLanguage(initialLanguage()).then(()=>api('status')).then(data=>{
  token=data.csrf;savedClientId=data.client_id;spotifyConnected=data.spotify;spotifyCanWrite=!!data.spotify_can_write;spotifyCanModify=!!data.spotify_can_modify;
  youtubeConnected=!!data.youtube;youtubeAccount=data.youtube_account||'';
  $('client-id').value=data.client_id;
  spotifyStatus();youtubeStatus();
  if(!data.client_id)$('setup').open=true;
  $('direction').value=new URLSearchParams(location.search).get('direction')||sessionStorage.getItem('songferryDirection')||'spotify-to-youtube';
  if(!$('direction').value)$('direction').value='spotify-to-youtube';
  configureDirection();
  loadHistory();
  if(currentView==='links')loadPlaylists();
  // Only a job named in the URL is reopened, so visiting another page never drags the last job along.
  const restoredJob=new URLSearchParams(location.search).get('job');
  if(restoredJob&&/^[A-Za-z0-9_-]{16,64}$/.test(restoredJob)){jobId=restoredJob;sessionStorage.setItem('songferryJob',jobId);$('review').hidden=false;$('import-button').disabled=true;$('links-start').disabled=true;poll();}
  const params=new URLSearchParams(location.search);
  if(params.get('login')==='cancelled'){showView('accounts',false);spotifyMessage('已取消 Spotify 登入，可再次按「連接 Spotify」。');}
}).catch(error=>{
  spotifyMessage(t('{error} 請確認本機程式正在執行，再重新整理頁面。',{error:t(error.message)}),true);
  $('login').textContent=t('無法取得連線設定');
});
