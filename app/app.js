'use strict';

const $ = selector => document.querySelector(selector);
const $$ = selector => Array.from(document.querySelectorAll(selector));
const state = {
  session: null,
  dashboard: null,
  current: null,
  filter: 'all',
  search: '',
  historyStatus: 'accepted',
  historyPage: 1,
  historyTotal: 0,
  playbook: null,
  detailGeneration: 0,
  detailController: null,
  aiReplyLines: [],
  pendingReply: '',
  sending: false,
};

function node(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text ?? '';
  if (className) element.className = className;
  return element;
}

function notice(message) {
  $('#notice').textContent = message || '';
  $('#notice').hidden = !message;
  if (message) {
    setTimeout(() => {
      if ($('#notice').textContent === message) notice('');
    }, 9000);
  }
}

async function api(path, body, signal) {
  const options = {credentials: 'same-origin', signal};
  if (body !== undefined) {
    if (!state.session) throw new Error('The workspace is not ready. Reload this page.');
    options.method = 'POST';
    options.headers = {'Content-Type': 'application/json', 'X-CSRF-Token': state.session.csrf};
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Could not reach the local workspace. Make sure it is running.');
  }
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error('The local workspace returned an unreadable response.');
  }
  if (!response.ok) throw new Error(result.error || 'The request failed.');
  return result;
}

function formatDate(value, includeTime = false) {
  if (!value) return 'Unknown date';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return 'Unknown date';
  const options = includeTime
    ? {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}
    : {day: 'numeric', month: 'short', year: 'numeric'};
  return date.toLocaleDateString([], options);
}

function relativeDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return 'recently';
  const seconds = Math.max(0, (Date.now() - date.valueOf()) / 1000);
  if (seconds < 90) return 'just now';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
  if (seconds < 604800) return Math.floor(seconds / 86400) + 'd ago';
  return formatDate(value);
}

function formatSize(bytes) {
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(2) + ' GB';
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + ' MB';
  if (bytes >= 1e3) return (bytes / 1e3).toFixed(1) + ' KB';
  return Number(bytes || 0) + ' B';
}

function initials(name) {
  return String(name || 'Moderator').split(/\s+/).filter(Boolean).slice(0, 2)
    .map(part => part[0]).join('').toUpperCase();
}

function creatorName(creator) {
  const person = creator?.person_or_org || creator || {};
  if (person.name) return person.name;
  return [person.given_name, person.family_name].filter(Boolean).join(' ') || 'Unknown author';
}

function visibleText(value) {
  return String(value || '').replace(/<\/?(?:p|div|li|br)[^>]*>/gi, ' ')
    .replace(/<[^>]*>/g, ' ').replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<').replace(/&gt;/gi, '>').replace(/\s+/g, ' ').trim();
}

function setAccount(account) {
  if (!account) return;
  const display = account.display_name || 'Moderator';
  $('#hello-name').textContent = display.split(' ')[0] || 'moderator';
  $('#account-name').textContent = display;
  $('#account-title').textContent = display;
  $('#avatar').textContent = initials(display);
}

const aiProviders = {
  openrouter: {placeholder: 'Auto: free model'},
  deepseek: {placeholder: 'Auto-detected'},
  openai: {placeholder: 'Auto-detected'},
  custom: {placeholder: 'Model ID'},
};

function updateAIProviderFields(provider) {
  const preset = aiProviders[provider] || aiProviders.openrouter;
  const custom = provider === 'custom';
  $('#ai-endpoint-field').hidden = !custom;
  $('#ai-endpoint').required = custom;
  $('#ai-model').placeholder = preset.placeholder;
}

function renderAIConnection(connection = state.session?.ai) {
  const ai = connection || {
    configured: false,
    provider: '',
    label: '',
    endpoint: '',
    model: '',
    has_key: false,
    available: false,
  };
  const provider = aiProviders[ai.provider] ? ai.provider : 'openrouter';
  $('#ai-provider-select').value = provider;
  $('#ai-endpoint').value = ai.endpoint || '';
  $('#ai-model').value = ai.model || '';
  $('#ai-api-key').value = '';
  $('#ai-api-key').placeholder = ai.has_key
    ? 'Key stored - leave blank to keep it'
    : 'Paste your API key';
  $('#clear-ai-settings').hidden = !ai.configured;
  updateAIProviderFields(provider);

  const badge = $('#ai-settings-badge');
  badge.textContent = ai.configured ? 'Configured' : 'Not configured';
  badge.className = 'ai-action ' + (ai.configured ? 'ready_to_approve' : 'neutral');
  if (ai.configured) {
    const label = (ai.label || 'AI provider') + ' \u00b7 ' + (ai.model || 'auto');
    $('#ai-settings-status').textContent = label + ' via ' + ai.endpoint + '. ' +
      (ai.has_key ? 'An API key is stored in server memory.' : 'No API key is stored.');
    $('#ai-provider').textContent = label;
  } else {
    $('#ai-settings-status').textContent =
      'Choose a provider and paste an API key to enable contextual AI checks.';
    $('#ai-provider').textContent = 'not configured';
  }
}

function setView(name) {
  $$('.view').forEach(view => { view.hidden = view.id !== name + '-view'; });
  $$('.nav-item').forEach(button => {
    const active = button.dataset.view === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-current', active ? 'page' : 'false');
  });
  if (name === 'history' && !$('#history-list').children.length) loadHistory();
  if (name === 'playbook' && !state.playbook) loadPlaybook();
}

async function loadDashboard(refresh = false) {
  $('#queue-loading').hidden = false;
  $('#queue-list').replaceChildren();
  $('#queue-empty').hidden = true;
  $('#refresh').disabled = true;
  notice('');
  try {
    const result = await api('/api/dashboard' + (refresh ? '?refresh=1' : ''));
    state.dashboard = result;
    setAccount(result.account);
    $('#stat-open').textContent = result.summary.open;
    $('#stat-new').textContent = result.summary.new;
    $('#stat-replies').textContent = result.summary.replies;
    $('#stat-waiting').textContent = result.summary.waiting;
    $('#refreshed-at').textContent = 'Updated ' + relativeDate(result.refreshed_at);
    $('#account-status').textContent = state.session.comments_enabled
      ? 'Connected - comments-only access verified'
      : 'Connected - read-only access verified';
    $('#disconnect').hidden = false;
    renderQueue();
    if (result.timeline_errors) {
      notice(result.timeline_errors + ' timelines could not be read; those requests are shown as new.');
    }
  } catch (error) {
    state.dashboard = null;
    $('#queue-empty').hidden = false;
    $('#queue-empty b').textContent = 'Dashboard unavailable';
    $('#queue-empty span').textContent = error.message;
    $('#account-status').textContent = error.message;
    if (!state.session?.has_token) toggleAccount(true);
  } finally {
    $('#queue-loading').hidden = true;
    $('#refresh').disabled = false;
  }
}

const bucketLabels = {new: 'New record', replied: 'Author replied', waiting: 'Waiting'};

function filteredQueue() {
  if (!state.dashboard) return [];
  const term = state.search.toLocaleLowerCase();
  return state.dashboard.requests.filter(item =>
    (state.filter === 'all' || item.bucket === state.filter) &&
    (!term || (item.title + ' ' + item.request_id + ' ' + item.record_id)
      .toLocaleLowerCase().includes(term)));
}

function renderQueue() {
  const items = filteredQueue();
  $('#queue-count').textContent = items.length + (items.length === 1 ? ' request' : ' requests');
  $('#queue-empty').hidden = items.length !== 0;
  $('#queue-list').replaceChildren(...items.map(item => {
    const button = node('button', '', 'queue-item');
    button.type = 'button';
    button.dataset.requestId = item.request_id;
    button.classList.toggle('active', state.current?.request?.id === item.request_id);

    const record = node('span', '', 'queue-record');
    const top = node('span', '', 'queue-item-top');
    top.append(node('span', bucketLabels[item.bucket] || item.bucket, 'badge ' + item.bucket),
      node('code', item.record_id));
    record.append(top, node('strong', item.title));

    const conversation = node('span', '', 'queue-conversation');
    conversation.append(node('span', item.comment_count
      ? item.comment_count + (item.comment_count === 1 ? ' message' : ' messages')
      : 'No messages', 'queue-message-count'));
    conversation.append(node('span', item.last_message || 'No conversation yet', 'queue-snippet'));

    button.append(record, conversation, node('time', relativeDate(item.updated), 'queue-updated'),
      node('span', 'Review  >', 'queue-open'));
    button.addEventListener('click', () => loadDetail(item.request_id));
    return button;
  }));
}

function showDetailState(which) {
  $('#detail-empty').hidden = which !== 'empty';
  $('#detail-loading').hidden = which !== 'loading';
  $('#detail').hidden = which !== 'detail';
}

function recommendation(result) {
  if (result.status === 'stop') return ['!', 'Stop and escalate', result.note];
  if (result.status === 'changes') return [String(result.findings.length), 'Changes to review', result.note];
  if (result.status === 'warn') return ['?', 'Human judgment needed', result.note];
  return ['✓', 'Rule-based checks passed', result.note];
}

async function loadDetail(requestId) {
  setView('review');
  state.detailGeneration += 1;
  const generation = state.detailGeneration;
  if (state.detailController) state.detailController.abort();
  state.detailController = new AbortController();
  showDetailState('loading');
  $('#review-toolbar-title').textContent = 'Opening submission...';
  try {
    const result = await api('/api/dashboard/request/' + encodeURIComponent(requestId), undefined,
      state.detailController.signal);
    if (generation !== state.detailGeneration) return;
    state.current = result;
    state.aiReplyLines = [];
    renderDetail(result);
    showDetailState('detail');
    renderQueue();
    if (result.ai_eligible && state.session.ai_available) {
      loadAI(generation);
    } else if (!state.session.ai_available) {
      showAIMessage('No AI provider is available. Configure one in Settings; rule-based findings remain available.', true);
    } else if (!result.request.is_open) {
      showAIMessage('AI review is not run for historical closed requests.', false);
    } else {
      showAIMessage('Automatic AI review runs when a request is new or the author has replied.', true);
    }
  } catch (error) {
    if (error.name === 'AbortError') return;
    if (generation !== state.detailGeneration) return;
    state.current = null;
    showDetailState('empty');
    $('#detail-empty h2').textContent = 'Could not open submission';
    $('#detail-empty p').textContent = error.message;
    notice(error.message);
  }
}

function renderDetail(result) {
  $('#review-toolbar-title').textContent = result.title || 'Submission review';
  $('#record-bucket').textContent = bucketLabels[result.queue_bucket] || result.queue_bucket || 'Review';
  $('#record-bucket').className = 'badge ' + (result.queue_bucket || 'neutral');
  $('#record-status').textContent = result.request.status || 'unknown';
  $('#record-id').textContent = result.context.record_id;
  $('#record-title').textContent = result.title;
  const creators = result.record.metadata?.creators || [];
  $('#record-authors').textContent = creators.map(creatorName).join(' · ') || 'No authors supplied';
  $('#archive-link').href = result.request.url;
  $('#conversation-link').href = result.request.url;
  $('#reply-thread-link').href = result.request.url;
  $('#edit-record').href = result.request.record_url;
  $('#edit-record').textContent = result.request.is_open ? 'Edit in Archive ↗' : 'View record in Archive ↗';

  const rec = recommendation(result);
  $('.recommendation').dataset.status = result.status;
  $('#recommendation-icon').textContent = rec[0];
  $('#recommendation-title').textContent = rec[1];
  $('#recommendation-copy').textContent = rec[2];
  $('#verdict-badge').textContent = result.headline;
  $('#finding-count').textContent = result.findings.length;
  $('#comment-count').textContent = result.conversation.length;

  const findingNodes = result.findings.map(finding => {
    const item = node('li', '');
    const copy = node('div', '');
    const headline = node('div', String(finding.text || '').replace(/^[-\s]+/, ''), 'finding-copy');
    if (finding.handling === 'moderator_edit') {
      headline.append(node('span', 'Moderator edit', 'handling-badge'));
    }
    copy.append(headline);
    if (finding.detail) copy.append(node('div', finding.detail, 'finding-detail'));
    item.append(node('span', finding.key, 'finding-key'), copy);
    return item;
  });
  if (!findingNodes.length) {
    findingNodes.push(node('li', 'No deterministic issues found. Continue with the AI and human checks.', 'all-clear'));
  }
  $('#findings').replaceChildren(...findingNodes);
  $('#manual-checks').replaceChildren(...result.eyeball.map(text => node('div', text, 'manual-check')));

  const metadata = result.record.metadata || {};
  $('#description').textContent = visibleText(metadata.description) || 'No description supplied.';
  $('#keywords').replaceChildren(...(metadata.subjects || [])
    .map(item => node('span', item.subject || item.id, 'chip')));
  if (!(metadata.subjects || []).length) $('#keywords').append(node('span', 'No keywords supplied', 'helper'));
  const references = result.record.custom_fields?.mc_references || [];
  $('#references').replaceChildren(...references.map(ref => node('li',
    ref.ref_citation || ref.citation || 'Incomplete reference')));
  if (!references.length) $('#references').append(node('li', 'No references supplied.'));
  $('#funding').textContent = result.funders.join(' · ') || 'No funding supplied.';

  const rawEntries = result.record.files?.entries || [];
  const entries = Array.isArray(rawEntries)
    ? rawEntries
    : Object.entries(rawEntries).map(([key, value]) => ({...value, key}));
  const total = entries.reduce((sum, item) => sum + Number(item.size || 0), 0);
  $('#file-summary').textContent = entries.length + (entries.length === 1 ? ' file · ' : ' files · ') + formatSize(total);
  $('#files').replaceChildren(...entries.map(file => {
    const row = node('tr', '');
    row.append(node('td', file.key), node('td', formatSize(file.size)));
    return row;
  }));

  if (result.conversation.length) {
    $('#conversation').replaceChildren(...result.conversation.map(message => {
      const card = node('article', '', 'message ' + message.role);
      const meta = node('div', '', 'message-meta');
      meta.append(node('b', message.author), node('time', formatDate(message.created, true)));
      card.append(meta, node('p', message.content));
      return card;
    }));
  } else {
    $('#conversation').replaceChildren(node('div', 'No conversation yet. This is a new submission.', 'conversation-empty'));
  }

  $('#reply').value = result.reply_draft || '';
  $('#reply').placeholder = result.status === 'ok'
    ? 'No rule-based changes were drafted. Write a human-reviewed reply only if needed.'
    : 'No calibrated automatic wording fits this case. Write a human-reviewed reply.';
  $('#copy-status').textContent = '';
  $('#reply-state').textContent = result.reply_eligible ? 'NOT SENT' : 'SENDING DISABLED';
  $('#reply-state').className = result.reply_eligible ? 'not-sent' : 'not-sent disabled';
  $$('#human-checks input').forEach(input => { input.checked = false; });
  resetAI();
  switchDetailTab('review');
  updateReplyControls();
  updateGate();
}

function resetAI() {
  $('#ai-action').textContent = 'Waiting';
  $('#ai-action').className = 'ai-action neutral';
  $('#ai-loading').hidden = true;
  $('#ai-error').hidden = true;
  $('#ai-result').hidden = true;
  $('#ai-replies').hidden = true;
  $('#ai-reply-lines').replaceChildren();
  const ai = state.session?.ai;
  $('#ai-provider').textContent = ai?.configured
    ? (ai.label || 'AI provider') + ' \u00b7 ' + (ai.model || 'auto')
    : (ai?.label || 'not configured');
}

function showAIMessage(message, canRetry) {
  $('#ai-loading').hidden = true;
  $('#ai-result').hidden = true;
  $('#ai-error-copy').textContent = message;
  $('#ai-error').hidden = false;
  $('#retry-ai').hidden = !canRetry || !state.session?.ai_available || !state.current?.request?.is_open;
  $('#ai-action').textContent = 'Human review';
  $('#ai-action').className = 'ai-action human_review';
}

async function loadAI(generation = state.detailGeneration) {
  if (!state.current) return;
  const reviewId = state.current.review_id;
  $('#ai-error').hidden = true;
  $('#ai-result').hidden = true;
  $('#ai-loading').hidden = false;
  $('#ai-action').textContent = 'Reviewing...';
  $('#ai-action').className = 'ai-action loading';
  try {
    const result = await api('/api/ai-review', {review_id: reviewId});
    if (generation !== state.detailGeneration || state.current?.review_id !== reviewId) return;
    renderAI(result);
  } catch (error) {
    if (generation !== state.detailGeneration || state.current?.review_id !== reviewId) return;
    showAIMessage(error.message, true);
  }
}

const actionLabels = {
  ready_to_approve: 'Suggest: ready after checks',
  request_changes: 'Suggest: request changes',
  clarify_scope: 'Suggest: clarify scope',
  suggest_decline: 'Suggest: consider decline',
  escalate: 'Suggest: escalate',
  human_review: 'Suggest: human review',
};

function renderAI(result) {
  $('#ai-loading').hidden = true;
  $('#ai-error').hidden = true;
  $('#ai-result').hidden = false;
  $('#ai-action').textContent = actionLabels[result.suggested_action] || 'Suggest: human review';
  $('#ai-action').className = 'ai-action ' + (result.suggested_action || 'human_review');
  $('#ai-provider').textContent = result.provider || state.session?.ai?.label || 'configured provider';
  $('#ai-scope').textContent = (result.scope.verdict || 'unclear') + ' · ' + (result.scope.confidence || 'low') + ' confidence';
  $('#ai-scope-reason').textContent = result.scope.reason || 'No reason supplied.';
  $('#ai-description').textContent = result.description.ok ? 'Adequate' : 'Needs attention';
  $('#ai-description-reason').textContent = result.description.reason || 'No reason supplied.';
  $('#ai-note').textContent = result.moderator_note || 'Complete the human checks before deciding.';
  state.aiReplyLines = Array.isArray(result.reply_lines) ? result.reply_lines : [];
  $('#ai-replies').hidden = !state.aiReplyLines.length;
  $('#ai-reply-lines').replaceChildren(...state.aiReplyLines.map(line => {
    const item = node('div', '', 'ai-reply-line');
    item.append(node('code', line.key), node('span', line.text));
    return item;
  }));
}

async function addAIReplies() {
  if (!state.aiReplyLines.length || !state.current) return;
  if (!state.playbook) await loadPlaybook();
  const unique = state.aiReplyLines.map(line => String(line.text || '').trim())
    .filter(text => text && !$('#reply').value.includes(text));
  if (!unique.length) {
    notice('Those suggested lines are already in the draft.');
    return;
  }
  const templates = state.playbook?.templates || {};
  let draft = $('#reply').value.trim();
  const scopeOnly = state.aiReplyLines.some(line => String(line.key).startsWith('r'));
  if (!draft) {
    const opening = scopeOnly ? templates.clarification_opening : templates.first_review_opening;
    const closing = scopeOnly ? templates.short_closing : templates.changes_closing;
    draft = [opening, unique.join('\n'), closing].filter(Boolean).join('\n\n');
  } else {
    const insertion = unique.join('\n');
    const closings = [templates.changes_closing, templates.short_closing].filter(Boolean);
    const closing = closings.find(value => draft.endsWith(value));
    if (closing) draft = draft.slice(0, -closing.length).trimEnd() + '\n\n' + insertion + '\n\n' + closing;
    else draft += '\n\n' + insertion;
  }
  $('#reply').value = draft;
  switchDetailTab('reply');
  updateReplyControls();
  $('#reply').focus();
  notice('AI-suggested canned wording was added locally. Review and edit it before sending.');
}

function switchDetailTab(name) {
  $$('.detail-tab').forEach(button => {
    const active = button.dataset.detailTab === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  $$('.tab-panel').forEach(panel => { panel.hidden = panel.id !== name + '-tab'; });
}

function checks() {
  return Object.fromEntries($$('#human-checks input').map(input => [input.name, input.checked]));
}

function updateReplyControls() {
  const content = $('#reply').value;
  const canSend = Boolean(content.trim() && state.current?.reply_eligible && state.session?.comments_enabled && !state.sending);
  $('#copy-reply').disabled = !content.trim();
  $('#send-reply').disabled = !canSend;
  $('#reply-count').textContent = content.length.toLocaleString() + (content.length === 1 ? ' character' : ' characters');
  $('#send-reply').title = state.current?.reply_eligible
    ? 'Preview the exact reply and confirm the request ID before posting'
    : 'Replies are available only for open Dashboard requests';
}

function updateGate() {
  if (!state.current) return;
  const complete = Object.values(checks()).every(Boolean);
  $('#prepare').disabled = !complete || !state.current.handoff_eligible;
  if (!state.current.request.is_open) {
    $('#gate-note').textContent = 'This historical request is closed.';
    $('#decision-title').textContent = 'Historical decision';
  } else if (!state.current.handoff_eligible) {
    $('#gate-note').textContent = 'Resolve the findings before approval can be prepared.';
    $('#decision-title').textContent = state.current.findings.length ? 'Changes required' : 'Human review required';
  } else if (!complete) {
    $('#gate-note').textContent = 'Complete all four checks before preparing approval.';
    $('#decision-title').textContent = 'Complete the final checks';
  } else {
    $('#gate-note').textContent = 'Ready for the final read-only safety check.';
    $('#decision-title').textContent = 'Ready to prepare approval';
  }
  $('#decision-copy').textContent = 'AI and rules only suggest. Accept, decline, edit, and publish stay in the Archive.';
}

async function loadHistory() {
  $('#history-loading').hidden = false;
  $('#history-list').replaceChildren();
  try {
    const result = await api('/api/dashboard/history?status=' + encodeURIComponent(state.historyStatus) +
      '&page=' + state.historyPage + '&size=25');
    state.historyTotal = result.total;
    $('#history-total').textContent = result.total.toLocaleString() + ' ' + state.historyStatus + ' requests';
    $('#history-page').textContent = 'Page ' + result.page + ' of ' + Math.max(1, Math.ceil(result.total / result.size));
    $('#history-prev').disabled = result.page <= 1;
    $('#history-next').disabled = result.page * result.size >= result.total;
    $('#history-list').replaceChildren(...result.requests.map(item => {
      const row = node('article', '', 'history-row');
      const title = node('div', '');
      title.append(node('h3', item.title), node('code', item.record_id));
      row.append(title, node('span', item.status, 'badge neutral'), node('time', formatDate(item.updated)));
      const open = node('button', 'Review', 'text-button');
      open.addEventListener('click', () => loadDetail(item.request_id));
      row.append(open);
      return row;
    }));
  } catch (error) {
    notice(error.message);
  } finally {
    $('#history-loading').hidden = true;
  }
}

async function loadPlaybook() {
  try {
    if (!state.playbook) state.playbook = await api('/api/playbook');
    const source = state.playbook.source;
    $('#playbook-source').textContent = source.moderator + ' · read-only sample';
    $('#sample-requests').textContent = source.sampled_requests;
    $('#sample-comments').textContent = source.sampled_comments;
    $('#sample-decisions').textContent = source.accepted_decisions_calibrated;
    $('#sample-date').textContent = state.playbook.calibrated_at;
    $('#playbook-attribution').textContent = source.attribution + ' ' + source.storage + ' ' + state.playbook.calibration.caveat;
    $('#schema-corrections').replaceChildren(...state.playbook.calibration.schema_corrections.map(item => node('li', item)));
    $('#principles').replaceChildren(...state.playbook.principles.map(item => node('li', item)));
    $('#reply-template').textContent = state.playbook.templates.first_review_opening +
      '\n\n[Field]\n- Requested change\n\n' + state.playbook.templates.changes_closing;
    return state.playbook;
  } catch (error) {
    notice(error.message);
    return null;
  }
}

function toggleAccount(open) {
  const panel = $('#account-panel');
  panel.hidden = open === undefined ? !panel.hidden : !open;
  $('#account-toggle').setAttribute('aria-expanded', String(!panel.hidden));
  if (!panel.hidden && !state.session?.has_token) $('#token').focus();
}

function showProviderChangedMessage() {
  if (!state.current) return;
  resetAI();
  showAIMessage('The AI provider changed. Run AI review to evaluate this submission with the new settings.', true);
}

function updateReplyConfirmation() {
  const matches = state.current && $('#reply-confirmation').value.trim() === state.current.request.id;
  $('#reply-trigger').disabled = !matches || !$('#reply-ack').checked || state.sending;
}

function openReplyDialog() {
  if (!state.current || $('#send-reply').disabled) return;
  state.pendingReply = $('#reply').value.trim();
  $('#reply-preview-title').textContent = state.current.title;
  $('#reply-preview-target').textContent = state.current.request.id;
  $('#reply-preview').textContent = state.pendingReply;
  $('#reply-confirmation').value = '';
  $('#reply-ack').checked = false;
  $('#reply-send-status').textContent = '';
  $('#reply-trigger').textContent = 'Send reply now';
  $('#reply-dialog-close').disabled = false;
  state.sending = false;
  updateReplyConfirmation();
  $('#reply-dialog').showModal();
  $('#reply-confirmation').focus();
}

async function submitReply() {
  if (!state.current || $('#reply-trigger').disabled || state.sending) return;
  const requestId = state.current.request.id;
  const reviewId = state.current.review_id;
  state.sending = true;
  $('#reply-trigger').disabled = true;
  $('#reply-dialog-close').disabled = true;
  $('#reply-send-status').textContent = 'Re-reading the record and conversation, then posting exactly once...';
  updateReplyControls();
  try {
    const result = await api('/api/reply/send', {
      review_id: reviewId,
      content: state.pendingReply,
      confirm: $('#reply-confirmation').value.trim(),
      acknowledge_external_send: $('#reply-ack').checked,
    });
    $('#reply-state').textContent = 'SENT';
    $('#reply-state').className = 'sent';
    $('#reply-trigger').textContent = 'Reply sent';
    $('#reply-send-status').textContent = result.message;
    notice('Reply posted to request ' + requestId + '.');
    setTimeout(async () => {
      $('#reply-dialog').close();
      state.current = null;
      showDetailState('empty');
      setView('dashboard');
      await loadDashboard(true);
    }, 900);
  } catch (error) {
    $('#reply-send-status').textContent = error.message;
    $('#reply-trigger').textContent = 'Reopen review before retrying';
  } finally {
    state.sending = false;
    $('#reply-dialog-close').disabled = false;
    updateReplyControls();
  }
}

$$('.nav-item').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
$$('.stat-card').forEach(button => button.addEventListener('click', () => {
  state.filter = button.dataset.filter;
  $$('.stat-card').forEach(item => item.classList.toggle('selected', item === button));
  renderQueue();
}));
$('#queue-search').addEventListener('input', event => { state.search = event.target.value.trim(); renderQueue(); });
$('#refresh').addEventListener('click', () => loadDashboard(true));
$('#back-dashboard').addEventListener('click', () => setView('dashboard'));
$('#empty-dashboard').addEventListener('click', () => setView('dashboard'));
$$('.detail-tab').forEach(button => button.addEventListener('click', () => switchDetailTab(button.dataset.detailTab)));
$('#human-checks').addEventListener('change', updateGate);
$('#reply').addEventListener('input', updateReplyControls);
$('#retry-ai').addEventListener('click', () => loadAI());
$('#add-ai-replies').addEventListener('click', addAIReplies);
$('#copy-reply').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('#reply').value);
    $('#copy-status').textContent = 'Copied - nothing sent';
  } catch {
    $('#reply').focus();
    $('#reply').select();
    $('#copy-status').textContent = 'Selected - press Ctrl+C';
  }
});

$('#account-toggle').addEventListener('click', () => toggleAccount());
$('#account-close').addEventListener('click', () => toggleAccount(false));
$('#account-form').addEventListener('submit', async event => {
  event.preventDefault();
  const token = $('#token').value.trim();
  $('#token').value = '';
  $('#save-token').disabled = true;
  try {
    const result = await api('/api/account', {token});
    Object.assign(state.session, result);
    toggleAccount(false);
    await loadDashboard(true);
  } catch (error) {
    notice(error.message);
  } finally {
    $('#save-token').disabled = false;
  }
});
$('#ai-provider-select').addEventListener('change', event => updateAIProviderFields(event.target.value));
$('#ai-settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  const provider = $('#ai-provider-select').value;
  const body = {
    provider,
    endpoint: provider === 'custom' ? $('#ai-endpoint').value.trim() : '',
    model: $('#ai-model').value.trim(),
  };
  const apiKey = $('#ai-api-key').value;
  if (apiKey) body.api_key = apiKey;
  $('#ai-api-key').value = '';
  $('#save-ai-settings').disabled = true;
  $('#clear-ai-settings').disabled = true;
  $('#ai-settings-status').textContent = 'Saving connection settings...';
  try {
    const result = await api('/api/ai-settings', body);
    state.session.ai = result.ai;
    state.session.ai_available = result.ai_available;
    renderAIConnection(result.ai);
    showProviderChangedMessage();
    notice('AI connection saved in server memory. It will be verified when you run AI review.');
  } catch (error) {
    $('#ai-settings-status').textContent = error.message;
    notice(error.message);
  } finally {
    $('#save-ai-settings').disabled = false;
    $('#clear-ai-settings').disabled = false;
  }
});
$('#clear-ai-settings').addEventListener('click', async () => {
  $('#save-ai-settings').disabled = true;
  $('#clear-ai-settings').disabled = true;
  $('#ai-settings-status').textContent = 'Forgetting API settings...';
  try {
    const result = await api('/api/ai-settings', {clear: true});
    state.session.ai = result.ai;
    state.session.ai_available = result.ai_available;
    renderAIConnection(result.ai);
    showProviderChangedMessage();
    notice('API settings forgotten. Configure a provider to enable contextual AI checks.');
  } catch (error) {
    $('#ai-settings-status').textContent = error.message;
    notice(error.message);
  } finally {
    $('#save-ai-settings').disabled = false;
    $('#clear-ai-settings').disabled = false;
  }
});
$('#disconnect').addEventListener('click', async () => {
  try {
    await api('/api/disconnect', {});
    state.session.has_token = false;
    state.dashboard = null;
    state.current = null;
    $('#queue-list').replaceChildren();
    showDetailState('empty');
    setView('dashboard');
    $('#account-status').textContent = 'No token loaded';
    $('#disconnect').hidden = true;
    toggleAccount(true);
  } catch (error) {
    notice(error.message);
  }
});

$$('[data-history-status]').forEach(button => button.addEventListener('click', () => {
  state.historyStatus = button.dataset.historyStatus;
  state.historyPage = 1;
  $$('[data-history-status]').forEach(item => item.classList.toggle('active', item === button));
  loadHistory();
}));
$('#history-prev').addEventListener('click', () => {
  if (state.historyPage > 1) { state.historyPage -= 1; loadHistory(); }
});
$('#history-next').addEventListener('click', () => { state.historyPage += 1; loadHistory(); });

$('#send-reply').addEventListener('click', openReplyDialog);
$('#reply-confirmation').addEventListener('input', updateReplyConfirmation);
$('#reply-ack').addEventListener('change', updateReplyConfirmation);
$('#reply-trigger').addEventListener('click', submitReply);
$('#reply-dialog-close').addEventListener('click', () => {
  if (!state.sending) $('#reply-dialog').close();
});
$('#reply-dialog').addEventListener('cancel', event => {
  if (state.sending) event.preventDefault();
});

$('#prepare').addEventListener('click', () => {
  if (!state.current || $('#prepare').disabled) return;
  $('#handoff-record-title').textContent = state.current.title;
  $('#confirmation-target').textContent = state.current.request.id;
  $('#confirmation').value = '';
  $('#trigger').disabled = true;
  $('#handoff-status').textContent = '';
  $('#final-link').hidden = true;
  $('#handoff-dialog').showModal();
});
$('#handoff-close').addEventListener('click', () => $('#handoff-dialog').close());
$('#confirmation').addEventListener('input', () => {
  $('#trigger').disabled = !state.current || $('#confirmation').value.trim() !== state.current.request.id;
});
$('#trigger').addEventListener('click', async () => {
  if (!state.current || $('#trigger').disabled) return;
  $('#trigger').disabled = true;
  $('#handoff-status').textContent = 'Re-reading the request and file metadata...';
  try {
    const result = await api('/api/handoff', {
      review_id: state.current.review_id,
      checks: checks(),
      confirm: $('#confirmation').value.trim(),
    });
    $('#handoff-status').textContent = 'The draft is unchanged. Nothing was published; make the final decision in the Archive.';
    $('#final-link').href = result.url;
    $('#final-link').hidden = false;
  } catch (error) {
    $('#handoff-status').textContent = error.message;
  }
});

(async function init() {
  try {
    state.session = await api('/api/session');
    renderAIConnection();
    $('#mock-banner').hidden = !state.session.mock;
    $('#mode-label').textContent = state.session.mock
      ? (state.session.comments_enabled ? 'Comments only · local mock' : 'Read-only · local mock')
      : (state.session.comments_enabled ? 'Comments only · production' : 'Read-only · production');
    loadPlaybook();
    if (state.session.has_token) {
      await loadDashboard();
    } else {
      $('#queue-loading').hidden = true;
      $('#queue-empty').hidden = false;
      $('#queue-empty b').textContent = 'Connect your moderator account';
      $('#queue-empty span').textContent = 'Add a token to read the queue.';
      toggleAccount(true);
    }
  } catch (error) {
    notice(error.message);
  }
})();
