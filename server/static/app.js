'use strict';
const $ = id => document.getElementById(id);
const attentionSound = new AttentionSound($('sound-toggle'));
let token = sessionStorage.getItem('slopwatchdeluxe-token') || '';
let active = [], archived = [], filter = 'all', provider = 'all', search = '';
let controller, generation = 0, refreshRunning = false, refreshAgain = false;
const labels = {permission_required:'Permission required',input_required:'Input required',turn_finished:'Turn completed',failed:'Failed',interrupted:'Interrupted',manual:'Needs attention'};
const stateLabels = {ATTENTION:'Needs attention',WORKING:'Working',IDLE:'Idle',CLOSED:'Closed'};
const symbols = {permission_required:'◇',input_required:'?',turn_finished:'✓',failed:'!',interrupted:'Ⅱ'};

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined && text !== null) result.textContent = text;
  return result;
}
function error(message = '') { $('error').textContent = message; $('error').hidden = !message; }
function connected(value) {
  $('connection').textContent = value ? 'Live updates' : 'Reconnecting';
  $('connection').classList.toggle('offline', !value);
}
async function api(path, options = {}) {
  const headers = {...options.headers};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body) headers['Content-Type'] = 'application/json';
  const response = await fetch('/api/v1' + path, {cache:'no-store',...options, headers, signal: options.signal || AbortSignal.timeout(8000)});
  if (response.status === 401) {
    $('settings').hidden = false;
    throw new Error('Enter the server’s API token in connection settings.');
  }
  if (!response.ok) throw new Error(`Server request failed (${response.status}). Please retry.`);
  return response.status === 204 ? null : response.json();
}
async function allSessions(isArchived) {
  const result = [];
  for (let offset = 0; ; offset += 1000) {
    const batch = await api(`/sessions?archived=${isArchived}&offset=${offset}`);
    result.push(...batch);
    if (batch.length < 1000) return result;
  }
}
async function refreshBuild() {
  try {
    const health = await api('/health');
    $('build-version').textContent = 'Build ' + (health.build || `v${health.version}`);
  } catch { /* Keep the last known build while reconnecting. */ }
}
async function refresh() {
  if (refreshRunning) { refreshAgain = true; return; }
  refreshRunning = true;
  refreshBuild();
  try {
    do {
      refreshAgain = false;
      [active, archived] = await Promise.all([allSessions(false), allSessions(true)]);
      attentionSound.update(active);
      error(); render();
      $('updated').textContent = 'Updated ' + new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    } while (refreshAgain);
  } catch (exc) { error(exc.message); }
  finally { refreshRunning = false; $('sessions').setAttribute('aria-busy', 'false'); }
}
function age(timestamp) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(timestamp)) / 1000));
  if (seconds < 60) return seconds + 's ago';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ' + Math.floor(seconds % 3600 / 60) + 'm ago';
  return Math.floor(seconds / 86400) + 'd ago';
}
function action(label, session, verb, className='') {
  const button = node('button', className, label);
  button.type = 'button';
  button.setAttribute('aria-label', `${label} ${session.project_name}`);
  button.addEventListener('click', async () => {
    if (verb === 'delete') {
      $('delete-title').textContent = `Delete ${session.project_name}?`;
      const dialog = $('delete-dialog');
      dialog.returnValue = 'cancel';
      dialog.showModal();
      const answer = await new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue), {once:true}));
      if (answer !== 'delete') return;
    }
    button.disabled = true;
    try {
      const base = '/sessions/' + encodeURIComponent(session.id);
      if (verb === 'delete') await api(base, {method:'DELETE'});
      else if (verb === 'acknowledge') await api(base, {method:'PATCH',body:JSON.stringify({state:'IDLE'})});
      else await api(base + '/' + verb, {method:'POST'});
      await refresh();
    } catch (exc) { error(exc.message); }
    finally { button.disabled = false; }
  });
  return button;
}
function card(session) {
  const result = node('article', 'card');
  result.dataset.id = session.id;
  const icon = node('div', 'provider-icon ' + session.provider, session.provider === 'codex' ? '>_' : '✳');
  icon.setAttribute('aria-hidden','true');
  const body = node('div', 'card-body');
  const top = node('div', 'card-top');
  top.append(node('h3','project',session.project_name),node('span','provider-tag',session.provider === 'codex' ? 'Codex' : 'Claude'));
  const host = node('p','host',session.hostname);
  if (session.metadata.git_branch) host.append(node('span','', ' / ' + session.metadata.git_branch));
  body.append(top,host,node('p','cwd',session.cwd || 'Project directory unavailable'));
  if (session.last_message) body.append(node('p','message',session.last_message));
  const right = node('div', 'card-right');
  const label = labels[session.attention_reason] || stateLabels[session.state];
  const symbol = symbols[session.attention_reason] || (session.state === 'WORKING' ? '●' : '○');
  right.append(node('span','badge ' + session.state,symbol + ' ' + label));
  const time = node('time','age',age(session.last_activity_at));
  time.dateTime = session.last_activity_at;
  time.title = 'Last activity: ' + new Date(session.last_activity_at).toLocaleString();
  right.append(time);
  const actions = node('div','card-actions');
  if (session.archived_at) actions.append(action('Restore',session,'restore'));
  else {
    if (session.state === 'ATTENTION') actions.append(action('Acknowledge',session,'acknowledge'));
    actions.append(action('Archive',session,'archive'));
  }
  actions.append(action('Delete',session,'delete','delete'));
  right.append(actions);
  result.append(icon,body,right);
  return result;
}
function render() {
  const counts = {};
  for (const state of ['ATTENTION','WORKING','IDLE']) counts[state] = active.filter(s => s.state === state).length;
  $('attention-count').textContent = counts.ATTENTION;
  $('attention-stat').classList.toggle('has-attention', counts.ATTENTION > 0);
  $('attention-stat').querySelector('small').textContent = counts.ATTENTION ? 'Your next stop' : 'All clear';
  $('working-count').textContent = counts.WORKING;
  $('idle-count').textContent = counts.IDLE;
  $('total-count').textContent = active.filter(s => s.state !== 'CLOSED').length;
  document.title = counts.ATTENTION ? `(${counts.ATTENTION}) SlopWatchDeluxe` : 'SlopWatchDeluxe';
  const rows = (filter === 'archived' ? archived : active).filter(s =>
    (filter === 'archived' || (filter === 'all' ? s.state !== 'CLOSED' : s.state === filter)) &&
    (provider === 'all' || s.provider === provider) &&
    `${s.project_name} ${s.cwd} ${s.hostname}`.toLowerCase().includes(search));
  const fragment = document.createDocumentFragment();
  for (const state of ['ATTENTION','WORKING','IDLE','CLOSED']) {
    const groupRows = rows.filter(s => s.state === state).sort((a,b) => b.updated_at.localeCompare(a.updated_at));
    if (!groupRows.length) continue;
    const section = node('section','group ' + state.toLowerCase());
    const heading = node('h2','group-heading',(filter === 'archived' ? 'ARCHIVED / ' : '') + stateLabels[state].toUpperCase());
    heading.append(node('span','count',groupRows.length));
    section.append(heading,...groupRows.map(card));
    fragment.append(section);
  }
  if (!rows.length) {
    const empty = node('div','empty');
    const firstRun = !active.length && !archived.length && filter === 'all' && !search;
    empty.append(node('div','empty-icon',firstRun ? '⌁' : '✓'),node('h2','',firstRun ? 'Your agents will appear here.' : 'Nothing needs your attention here.'));
    empty.append(node('p','',firstRun ? 'Download the hook client, install it on your development machine, then launch Codex or Claude as usual.' : 'No sessions match this view. Updates arrive automatically as your agents work.'));
    if (firstRun) empty.append(node('code','', 'python3 slopwatchdeluxe.pyz install'));
    fragment.append(empty);
  }
  // All provider content enters through textContent, never HTML.
  $('sessions').replaceChildren(fragment);
}
async function stream() {
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  const current = ++generation;
  while (current === generation && !signal.aborted) {
    let watchdog;
    try {
      const headers = {Accept:'text/event-stream'};
      if (token) headers.Authorization = `Bearer ${token}`;
      const response = await fetch('/api/v1/stream', {headers,signal,cache:'no-store'});
      if (!response.ok) {
        if (response.status === 401) { $('settings').hidden = false; error('Enter the server’s API token in connection settings.'); }
        throw new Error('Stream unavailable');
      }
      connected(true);
      const reader = response.body.getReader();
      let buffer = '';
      const decoder = new TextDecoder();
      while (!signal.aborted) {
        watchdog = setTimeout(() => reader.cancel(), 35000);
        const {value,done} = await reader.read();
        clearTimeout(watchdog);
        if (done) break;
        buffer += decoder.decode(value,{stream:true}).replace(/\r/g,'');
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0,boundary);
          buffer = buffer.slice(boundary + 2);
          if (frame.includes('event: change')) await refresh();
        }
      }
    } catch (exc) { if (signal.aborted) return; }
    finally { clearTimeout(watchdog); }
    if (current !== generation) return;
    connected(false);
    await new Promise(resolve => setTimeout(resolve,2000));
  }
}
document.querySelectorAll('[data-state]').forEach(button => button.addEventListener('click', () => {
  filter = button.dataset.state;
  document.querySelectorAll('.filters button').forEach(b => { const selected = b.dataset.state === filter; b.classList.toggle('selected',selected); b.setAttribute('aria-pressed',String(selected)); });
  render();
}));
$('provider').addEventListener('change', event => {provider = event.target.value; render();});
$('search').addEventListener('input', event => {search = event.target.value.toLowerCase(); render();});
$('settings-button').addEventListener('click', () => { $('settings').hidden = !$('settings').hidden; if (!$('settings').hidden) $('token').focus(); });
$('token-form').addEventListener('submit', event => {
  event.preventDefault(); token = $('token').value.trim(); sessionStorage.setItem('slopwatchdeluxe-token',token); $('token').value = ''; $('settings').hidden = true; refresh(); stream();
});
$('clear-token').addEventListener('click', () => {token = ''; sessionStorage.removeItem('slopwatchdeluxe-token'); $('token').value = ''; refresh(); stream();});
setInterval(() => document.querySelectorAll('time.age').forEach(time => {time.textContent = age(time.dateTime);}),5000);
refresh(); stream();
