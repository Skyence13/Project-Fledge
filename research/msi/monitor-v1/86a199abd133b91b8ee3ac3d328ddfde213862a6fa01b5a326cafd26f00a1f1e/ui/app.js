'use strict';

const byId = id => document.getElementById(id);
let snapshot = null;
let split = 'confirmation';
let fetching = false;
let connectionError = '';
const dateFormat = new Intl.DateTimeFormat(undefined, {
  year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit', timeZoneName: 'short'
});
const states = {
  bounded_study_complete: 'Complete', research_review: 'Research review',
  did_not_meet_criteria: 'Below study threshold', complete: 'Complete', ready: 'Ready',
  review: 'Review', paused: 'Paused'
};
function human(value) {
  if (!value) return 'Unreported';
  const words = String(value).replace(/[_-]/g, ' ');
  return states[value] || words.charAt(0).toUpperCase() + words.slice(1);
}
function time(value) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? dateFormat.format(parsed) : 'Time unreported';
}
function element(type, className, content) {
  const node = document.createElement(type);
  if (className) node.className = className;
  if (content != null) node.textContent = content;
  return node;
}
function validScore(s) {
  return s && Number.isInteger(s.total) && s.total > 0 && Number.isInteger(s.correct)
    && s.correct >= 0 && s.correct <= s.total;
}
function percentage(s) { return (100 * s.correct / s.total).toFixed(1) + '%'; }
function counts(s) {return s.correct.toLocaleString() + '/' + s.total.toLocaleString();}
function tag(text, good) {return element('span', 'tag' + (good ? ' good' : ''), text);}
function freshness() {
  const age = snapshot ? Date.now() - Date.parse(snapshot.observed_at) : NaN;
  byId('stamp').textContent = snapshot
    ? [snapshot.observed_at ? 'Lab observed ' + time(snapshot.observed_at) : 'Awaiting first lab observation',
       snapshot.heartbeat_at && 'Monitor checked ' + time(snapshot.heartbeat_at)].filter(Boolean).join(' · ')
    : 'Waiting for a lab observation';
  const stale = Number.isFinite(age) && age > 2 * 60000;
  const issue = connectionError || (stale ? 'The last MSI observation is over two minutes old. Activity and study results may have changed.' : '');
  byId('warning').textContent = issue;
  byId('warning').hidden = !issue;
  byId('signal').dataset.state = stale || issue ? 'unknown' : snapshot?.activity?.state || 'unknown';
}
function renderStudy() {
  const study = snapshot?.last_completed || (snapshot?.study?.completed_at ? snapshot.study : null);
  byId('study-title').textContent = study?.name || 'No study recorded';
  byId('objective').textContent = study?.objective || '';
  byId('study-foot').textContent = study ? [
    study.result && 'Outcome · ' + human(study.result),
    typeof study.retained_both === 'boolean' && (study.retained_both
      ? 'Earlier skills passed both banks' : 'Earlier-skill retention unconfirmed across both banks'),
    study.completed_at && 'Completed ' + time(study.completed_at)
  ].filter(Boolean).join('   ·   ') : '';
  const banks = study?.splits || {};
  if (!banks[split]) split = banks.confirmation ? 'confirmation' : 'validation';
  const bank = banks[split] || {};
  const marked = bank.marked;
  const practice = bank.practice;
  const stationary = bank.stationary;
  const reviewed = Array.isArray(bank.comparison);
  byId('assay-title').textContent = reviewed ? 'Held-out comparisons'
    : stationary ? 'Stationary target localization' : 'Source recall';
  const comparison = reviewed ? bank.comparison.slice(0, 24)
    .filter(row => typeof row?.label === 'string')
    .map(row => ['given', row.label, row.score]) : stationary ? [
    ['given', 'Wisp, target visible', stationary.visible],
    ['withheld', 'Wisp, one blank frame', stationary.blank],
    ['boundary_only', 'Wisp, both frames dark', stationary.dark_only],
    ['given', 'Current-frame decoder, target visible', stationary.decoder_visible],
    ['boundary_only', 'Current-frame decoder, one blank frame', stationary.decoder_blank],
    ['withheld', 'Last-seen decoder, one blank frame', stationary.last_seen_blank]
  ] : practice ? [
    ['given', 'Accurate feedback · conflicting choices', practice.accurate?.conflict],
    ['withheld', 'Independent feedback · conflicting choices', practice.independent?.conflict],
    ['given', 'Accurate feedback · all choices', practice.accurate?.overall],
    ['withheld', 'Independent feedback · all choices', practice.independent?.overall]
  ] : marked ? [
    ['given', 'Informative marker · conflicting choices', marked.informative?.conflict],
    ['withheld', 'Shuffled marker · conflicting choices', marked.shuffled?.conflict],
    ['given', 'Informative marker · all choices', marked.informative?.overall],
    ['withheld', 'Shuffled marker · all choices', marked.shuffled?.overall]
  ] : [
    ['given', 'Prior grade given', bank.given], ['withheld', 'Prior grade withheld', bank.withheld],
    ['boundary_only', 'Grade boundary only', bank.boundary_only]
  ];
  const visible = comparison.filter(([, , score]) => validScore(score));
  byId('assay').hidden = !Object.keys(banks).length;
  if (!visible.length) byId('assay-title').textContent = 'Study metrics';
  byId('metrics-missing').hidden = !!visible.length || !study;
  for (const button of document.querySelectorAll('[data-split]')) {
    button.disabled = !banks[button.dataset.split];
    button.setAttribute('aria-pressed', String(button.dataset.split === split));
  }
  const bars = byId('bars');
  bars.replaceChildren();
  for (const [key, label, score] of visible) {
    const row = element('div', 'metric');
    const value = element('span', 'value', percentage(score));
    value.append(element('span', 'count', counts(score)));
    const track = element('div', 'track');
    track.setAttribute('aria-hidden','true');
    const fill = element('span', 'fill ' + key);
    fill.style.width = percentage(score);
    track.append(fill);
    row.append(element('span','label',label),value,track);
    bars.append(row);
  }
  const effect = byId('effect');
  effect.replaceChildren();
  effect.hidden = !practice && !marked && !Number.isFinite(bank.delta);
  if (stationary) {
    const visibleScore = stationary.visible;
    const blankScore = stationary.blank;
    effect.hidden = !(validScore(visibleScore) && validScore(blankScore));
    if (!effect.hidden) {
      const pp = 100 * (visibleScore.correct / visibleScore.total - blankScore.correct / blankScore.total);
      effect.textContent = 'Visible minus one blank frame: ' + pp.toFixed(1) + ' percentage points.';
    }
  } else if (practice) {
    for (const [key, label] of [['accurate', 'Accurate'], ['independent', 'Independent']]) {
      const arm = practice[key];
      if (Number.isInteger(arm?.conflict_histories)) {
        effect.append(element('div', '', label + ' feedback produced ' + arm.conflict_histories +
          ' conflicting histories out of ' + arm.histories + '.'));
      }
    }
    if (Number.isInteger(practice.min_conflict_histories)) {
      effect.append(element('div', '', 'Each arm requires at least ' + practice.min_conflict_histories + ' conflicting histories.'));
    }
    if (Number.isFinite(practice.paired_overall_delta)) {
      const pp = 100 * practice.paired_overall_delta;
      effect.append(element('div', '', 'Paired difference in overall recall, accurate minus independent feedback: ' +
        (pp > 0 ? '+' : '') + pp.toFixed(1) + ' pp.'));
    }
  } else if (marked) {
    const score = marked.informative?.conflict;
    const baseline = marked.shuffled?.conflict;
    if (validScore(score) && validScore(baseline)) {
      const gain = 100 * (score.correct / score.total - baseline.correct / baseline.total);
      effect.append(element('strong', '', (gain > 0 ? '+' : '') + gain.toFixed(1) + ' pp'),
        document.createTextNode(' with an informative marker on conflicting choices. '));
    }
    if (Number.isInteger(marked.conflict_histories)) {
      let note = 'The two choices differed in ' + marked.conflict_histories + ' of ' + marked.histories + ' histories.';
      if (marked.conflict_histories < marked.min_conflict_histories) {
        note += ' The study requires at least ' + marked.min_conflict_histories + ' conflict histories.';
      }
      effect.append(document.createTextNode(note));
    }
  } else if (!effect.hidden) {
    const pp = bank.delta * 100;
    effect.append(element('strong', '', (pp > 0 ? '+' : '') + pp.toFixed(1).replace('-', '−') + ' pp'),
      document.createTextNode(' in recall when the preceding grade was given versus withheld.'));
  }
  const skillList = byId('old-skills');
  skillList.replaceChildren();
  for (const [key, label] of [['immediate','Immediate'],['reversal','Reversal'],['mixed_recall','Mixed-delay recall']]) {
    const score = bank.earlier?.[key];
    if (!validScore(score)) continue;
    const row = element('div','old-score');
    row.append(element('span','',label),element('strong','',percentage(score)),element('small','',counts(score)));
    skillList.append(row);
  }
  for (const item of (Array.isArray(bank.skill_rows) ? bank.skill_rows.slice(0, 24) : [])) {
    if (typeof item?.label !== 'string') continue;
    const score = item.score;
    const rated = Number.isFinite(item.rate) && item.rate >= 0 && item.rate <= 1;
    if (!validScore(score) && !rated) continue;
    const row = element('div', 'old-score');
    row.append(element('span', '', item.label), element('strong', '',
      validScore(score) ? percentage(score) : (100 * item.rate).toFixed(1) + '%'));
    if (validScore(score)) row.append(element('small', '', counts(score)));
    skillList.append(row);
  }
  byId('retention').hidden = !skillList.childElementCount;
}
function arms(campaign) {
  const root = byId('arms');
  const openNames = Array.from(root.querySelectorAll('details[open]'), d => d.dataset.arm);
  root.replaceChildren();
  for (const arm of campaign.arms || []) {
    const box = element('article','arm');
    const title = element('div', 'arm-title');
    const name = arm.name === 'trace' ? 'Evolved trace' : arm.name === 'legacy' ? 'Legacy rule' : human(arm.name);
    title.append(element('h3','',name),tag(human(arm.state),arm.state === 'complete'));
    box.append(title, element('p','',arm.stage || 'Stage unreported'));
    const meta = element('div','arm-score');
    meta.append(element('span','','Arm generation'),element('strong','',arm.generation ?? '—'));
    box.append(meta);
    const detail = element('details');
    detail.dataset.arm = arm.name;
    detail.open = openNames.includes(arm.name);
    detail.append(element('summary','','Review scores'));
    const table = element('table');
    table.setAttribute('aria-label',name + ' review scores');
    const heading = element('tr');
    for (const label of ['Skill','Validation','Confirmation']) heading.append(element('th','',label));
    const head = element('thead'); head.append(heading); table.append(head);
    const body = element('tbody');
    for (const [key,label] of [['credit','One-shot'],['delayed','Delayed'],['immediate','Immediate'],['reversal','Reversal']]) {
      const row = element('tr');
      const th = element('th','',label); th.scope = 'row'; row.append(th);
      for (const bank of ['validation','confirmation']) {
        const value = arm[bank]?.[key];
        row.append(element('td','',validScore(value) ? counts(value) : '—'));
      }
      body.append(row);
    }
    table.append(body); detail.append(table); box.append(detail); root.append(box);
  }
  if (!root.childElementCount) root.append(element('p','description','No paired arms recorded.'));
}
function render() {
  const campaign = snapshot.campaign || {};
  const progress = snapshot.progress;
  byId('decision-notice').hidden = !snapshot.latest_decision;
  byId('decision-notice').textContent = snapshot.latest_decision
    ? 'Latest decision · ' + snapshot.latest_decision.study + ' · ' + snapshot.latest_decision.notice : '';
  byId('research-progress').hidden = !progress;
  if (progress) {
    byId('progress-name').textContent = human(progress.study);
    const enrolled = snapshot.evolution?.study === progress.study || snapshot.study?.score_status === 'unscored';
    byId('progress-question').textContent = progress.phase === 'preparing_successor' && snapshot.latest_decision?.study === progress.study
      ? snapshot.latest_decision.question : progress.objective || (enrolled ? snapshot.study.objective : '');
    const counts = Number.isInteger(progress.total) ? progress.completed + ' of ' + progress.total + ' ' + (progress.unit || 'banks') : '';
    byId('progress-count').textContent = [human(progress.phase), counts, time(progress.observed_at)].filter(Boolean).join(' · ');
  }
  const transitions = byId('transitions');
  transitions.replaceChildren();
  for (const event of (snapshot.transitions || []).slice(0,32)) {
    const row = element('div','entry');
    const name = element('div','entry-name',human(event.study));
    name.append(element('span','entry-time',time(event.observed_at)));
    const count = Number.isInteger(event.total) ? ' · ' + event.completed + '/' + event.total + ' ' + (event.unit || 'banks') : '';
    row.append(name, tag(human(event.phase) + count, false));
    transitions.append(row);
  }
  byId('transitions-panel').hidden = !transitions.childElementCount;
  byId('activity').textContent = [snapshot.activity?.label || 'Activity unknown', snapshot.activity?.reason].filter(Boolean).join(' · ');
  byId('phase').textContent = human(campaign.phase);
  byId('generation').textContent = campaign.generation ?? '—';
  renderStudy();
  arms(campaign);
  const events = byId('events');
  events.replaceChildren();
  for (const item of snapshot.history || []) {
    const entry = element('div','entry');
    const name = element('div','entry-name',item.name || 'Unnamed study');
    name.append(element('span','entry-time',item.completed_at ? time(item.completed_at) : human(item.state)));
    entry.append(name,tag(human(item.result || item.state),false));
    events.append(entry);
  }
  if (!events.childElementCount) events.append(element('p','description','No studies recorded.'));
}
async function refresh() {
  if (fetching) return;
  fetching = true;
  byId('refresh').disabled = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const result = await fetch('/api/summary', {cache:'no-store', signal:controller.signal, headers:{Accept:'application/json'}});
    if (!result.ok) throw Error();
    const data = await result.json();
    if (!data || !Number.isFinite(Date.parse(data.heartbeat_at || data.observed_at)) || !data.activity) throw Error();
    snapshot = data;
    connectionError = '';
    render();
  } catch {
    connectionError = snapshot ? 'Update unavailable. Showing the last observation received.' : 'No lab snapshot is available yet. The page will try again automatically.';
    if (!snapshot) {
      byId('activity').textContent = 'Unknown';
      byId('study-title').textContent = 'Study unavailable';
    }
  } finally {
    clearTimeout(timeout); fetching = false; byId('refresh').disabled = false; freshness();
  }
}
byId('refresh').addEventListener('click',refresh);
const pathwayNotes = {
  all: 'Arrows show signal direction. The six green connections into motor neurons can change with feedback.',
  sensory: 'Each receptor projects to all four neurons. Green arrows lead directly to the motors.',
  recurrent: 'H2 and H3 feed activity back to each other across network updates.',
  motor: 'Each motor receives both receptors and one hidden input. A grade changes eligible weights on these pathways.'
};
byId('pathway').addEventListener('change', event => {
  const pathway = event.target.value;
  for (const edge of document.querySelectorAll('.net-edges [data-edge]')) {
    edge.classList.toggle('dim', pathway !== 'all' && !edge.classList.contains(pathway));
  }
  byId('pathway-caption').textContent = pathwayNotes[pathway];
});
for (const button of document.querySelectorAll('[data-split]')) {
  button.addEventListener('click',() => {split = button.dataset.split; renderStudy();});
}
document.addEventListener('visibilitychange',() => {if (!document.hidden) refresh();});
refresh();
setInterval(() => {if (!document.hidden) refresh();},5000);
