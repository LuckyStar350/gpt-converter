(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const converter = window.GPTConverter;
  const archive = window.LocalArchive;
  const state = { target: 'sub2', documents: [], importIssues: [], parsed: null, output: null, currentFile: 0, revealed: false, loading: false, job: 0, refreshing: false, onlineSnapshot: null, bridgeKey: '', stopRefresh: false };
  let toastTimer;
  let detectTimer;

  function toast(message) {
    $('toast').textContent = message;
    $('toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500);
  }
  function message(content, type = '') {
    $('statusMessage').textContent = content;
    $('statusMessage').className = `status-message ${type}`;
    $('statusMessage').hidden = !content;
  }
  function invalidate(clearSnapshot = true) {
    if (clearSnapshot) state.onlineSnapshot = null;
    state.parsed = null;
    state.output = null;
    state.currentFile = 0;
    $('reportSection').hidden = true;
    $('statusMessage').hidden = true;
    $('resultBadge').textContent = '等待转换';
    $('resultBadge').className = 'subtle-badge';
    renderOutput();
  }
  function updateInputMeta() {
    $('inputCount').textContent = `${$('inputText').value.length.toLocaleString()} 字符${state.documents.length ? ` · ${state.documents.length} 个导入文件` : ''}`;
    clearTimeout(detectTimer);
    detectTimer = setTimeout(() => {
      try {
        const results = state.documents.map(doc => converter.parseInput(doc.content, { label: doc.name }));
        if ($('inputText').value.trim()) results.push(converter.parseInput($('inputText').value));
        const formats = [...new Set(results.flatMap(result => result.formats))];
        $('detectedFormat').textContent = formats.length > 1 ? '已识别 · 混合格式' : formats.length ? `已识别 · ${converter.LABELS[formats[0]]}` : '等待有效输入';
      } catch { $('detectedFormat').textContent = '待检查输入'; }
    }, 250);
  }
  function renderDocuments() {
    const element = $('importedFiles');
    element.replaceChildren();
    element.hidden = !state.documents.length;
    state.documents.slice(0, 5).forEach((doc, index) => {
      const chip = document.createElement('span');
      chip.className = 'file-chip';
      const label = document.createElement('span');
      label.textContent = doc.name;
      label.title = doc.name;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '×';
      button.setAttribute('aria-label', `移除 ${doc.name}`);
      button.addEventListener('click', () => { state.documents.splice(index, 1); invalidate(); renderDocuments(); updateInputMeta(); });
      chip.append(label, button);
      element.append(chip);
    });
    if (state.documents.length > 5) {
      const more = document.createElement('span');
      more.textContent = `另有 ${state.documents.length - 5} 个文件`;
      element.append(more);
    }
  }
  async function importFiles(fileList) {
    if (state.loading || state.refreshing) { toast('正在处理，请稍候'); return; }
    const files = Array.from(fileList).filter(file => /\.(json|jsonl|txt|zip)$/i.test(file.name));
    if (!files.length) { message('请选择 JSON、JSONL、TXT 或 ZIP 文件。', 'error'); return; }
    if (files.length > 5000) { message('最多导入 5000 个文件，请分批导入。', 'error'); return; }
    const job = ++state.job;
    invalidate();
    state.loading = true;
    $('convertButton').disabled = true;
    message('正在本地读取文件…');
    const documents = [];
    const issues = [];
    let size = state.documents.reduce((sum, doc) => sum + new TextEncoder().encode(doc.content).length, 0);
    try {
      for (const file of files) {
        if (job !== state.job) return;
        const name = file.webkitRelativePath || file.name;
        try {
          if (file.size > 20 * 1024 * 1024) throw new Error('单文件超过 20 MB，请分批导入');
          const entries = /\.zip$/i.test(file.name) ? await archive.readZip(await file.arrayBuffer()) : [{ name, content: new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer()) }];
          for (const entry of entries) {
            const length = new TextEncoder().encode(entry.content).length;
            if (length > 20 * 1024 * 1024) throw new Error('单文件超过 20 MB');
            if (size + length > 64 * 1024 * 1024) throw new Error('导入内容总计超过 64 MB，请清空后分批导入');
            if (state.documents.length + documents.length >= 5000) throw new Error('最多导入 5000 个文件');
            size += length;
            documents.push({ name: /\.zip$/i.test(file.name) ? `${file.name} / ${entry.name}` : entry.name, content: entry.content });
          }
        } catch (error) { issues.push({ location: name, message: error.message }); }
      }
      if (job !== state.job) return;
      state.documents.push(...documents);
      state.importIssues.push(...issues);
      invalidate();
      renderDocuments();
      updateInputMeta();
      message(`已导入 ${documents.length} 个文件。${issues.length ? ` ${issues.length} 个文件存在问题，转换报告将显示详情。` : '选择目标格式后开始转换。'}`, issues.length ? 'warning' : '');
    } finally {
      if (job === state.job) { state.loading = false; $('convertButton').disabled = false; }
      $('fileInput').value = '';
      $('folderInput').value = '';
    }
  }

  function options() {
    const concurrency = Number($('concurrency').value);
    const priority = Number($('priority').value);
    if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 1000) throw new Error('并发数应为 1–1000 的整数');
    if (!Number.isInteger(priority) || priority < 0 || priority > 1000) throw new Error('优先级应为 0–1000 的整数');
    return { deduplicate: $('deduplicate').checked, preserveExtra: $('preserveExtra').checked, concurrency, priority };
  }
  function parseSource(deduplicate) {
    if (state.onlineSnapshot) return converter.mergeResults([state.onlineSnapshot], deduplicate);
    const sourceFormat = $('sourceFormat').value;
    const results = state.documents.map(doc => converter.parseInput(doc.content, { sourceFormat, label: doc.name }));
    if ($('inputText').value.trim()) results.push(converter.parseInput($('inputText').value, { sourceFormat }));
    if (state.importIssues.length) results.push({ records: [], issues: state.importIssues, skipped: state.importIssues.length });
    return results.length ? converter.mergeResults(results, deduplicate) : null;
  }
  function renderConversion() {
    if (state.loading || state.refreshing) return;
    try {
      const config = options();
      state.parsed = parseSource(config.deduplicate);
      if (!state.parsed) { invalidate(); message('请先粘贴内容或导入文件。', 'error'); $('inputText').focus(); return; }
      // Incomplete failures stay in the input/report. Never present an RT-only
      // placeholder as a completed Sub2/CPA credential.
      const exportable = state.target === 'rt' ? state.parsed.records : state.parsed.records.filter(record => record.accessToken && record.accountId);
      state.output = converter.exportRecords(exportable, state.target, config);
      const excluded = state.parsed.records.length - exportable.length;
      if (excluded) state.output.warnings.push({ location: '转换结果', message: `${excluded} 个账号缺少访问令牌或账号 ID，未导出到 ${converter.LABELS[state.target]}；原 RT 仍保留在输入中` });
      state.currentFile = 0;
      renderOutput();
      renderReport();
      const { count } = state.output;
      const skipped = state.parsed.skipped;
      const incomplete = state.parsed.records.filter(record => !record.accessToken || !record.accountId).length;
      const hasOnlineIssues = state.parsed.issues.some(issue => issue.online);
      const duplicateNote = state.parsed.duplicates ? ` 已去除 ${state.parsed.duplicates} 个重复项。` : '';
      $('resultBadge').textContent = count ? `${count} 个账号 · ${hasOnlineIssues ? '查看报告' : '已转换'}` : '没有可导出结果';
      $('resultBadge').className = count ? 'subtle-badge ready' : 'subtle-badge';
      if (!count) message(`没有可导出的 ${converter.LABELS[state.target]} 结果，请查看转换报告。`, 'error');
      else message(`已生成 ${count} 个账号的 ${converter.LABELS[state.target]} 结果。${duplicateNote}${skipped ? ` 跳过 ${skipped} 个无效或不支持的项目。` : ''}${incomplete && state.target !== 'rt' ? ` ${incomplete} 个账号缺少访问令牌或账号 ID，请查看报告。` : ''}${hasOnlineIssues ? ' 存在刷新失败或未处理项目，详见报告。' : ''}`, skipped || hasOnlineIssues || (incomplete && state.target !== 'rt') ? 'warning' : '');
    } catch (error) { invalidate(false); message(error.message, 'error'); }
  }
  function renderOutput() {
    const output = state.output;
    const hasOutput = Boolean(output?.files.length);
    $('emptyOutput').hidden = hasOutput;
    $('outputText').hidden = !hasOutput;
    $('copyButton').disabled = !hasOutput;
    $('downloadButton').disabled = !hasOutput;
    $('outputCount').textContent = `${output?.count || 0} 个账号${state.target === 'cpa' && hasOutput ? ` · ${output.files.length} 个文件` : ''}`;
    const filename = $('outputFilename');
    filename.replaceChildren();
    if (hasOutput && output.files.length > 1) {
      const select = document.createElement('select');
      select.setAttribute('aria-label', '选择 CPA 预览文件');
      output.files.forEach((file, index) => {
        const option = document.createElement('option');
        option.value = String(index);
        option.textContent = `${index + 1}. ${file.name}`;
        select.append(option);
      });
      select.value = String(state.currentFile);
      select.addEventListener('change', () => { state.currentFile = Number(select.value); renderPreview(); });
      filename.append(select);
    } else filename.textContent = hasOutput ? output.files[0].name : { rt: 'refresh-tokens.txt', sub2: 'sub2api-accounts.json', cpa: 'codex-account.json' }[state.target];
    const downloadLabel = state.target === 'rt' ? '下载 TXT' : state.target === 'cpa' && output?.files.length > 1 ? '下载 ZIP' : '下载 JSON';
    $('downloadButton').querySelector('span').textContent = downloadLabel;
    $('targetDescription').textContent = {
      rt: '每行一个 refresh_token，导出为 TXT 文本。',
      sub2: '合并为一个 JSON，可在 Sub2API「导入数据」中使用。',
      cpa: '每个账号生成独立 JSON，批量下载为 ZIP，解压后导入 CPA。'
    }[state.target];
    renderPreview();
  }
  function renderPreview() {
    const file = state.output?.files[state.currentFile];
    $('outputText').value = file ? state.revealed ? file.content : converter.maskPreview(file.content, state.target) : '';
    const revealText = state.revealed ? '隐藏 Token' : '显示 Token';
    // Keep the static icon while changing its adjacent text.
    const button = $('revealButton');
    if (button.lastChild.nodeType === Node.TEXT_NODE) button.lastChild.textContent = revealText;
    else button.append(document.createTextNode(revealText));
    button.setAttribute('aria-pressed', String(state.revealed));
    $('previewNote').textContent = `${state.revealed ? '完整预览' : 'Token 默认隐藏'}${state.output?.files.length > 1 ? ` · 第 ${state.currentFile + 1} / ${state.output.files.length} 个文件` : ''}`;
  }
  function renderReport() {
    const parsed = state.parsed;
    const output = state.output;
    $('reportSection').hidden = false;
    $('reportTotal').textContent = `${parsed.records.length} 个可解析账号`;
    $('reportStats').replaceChildren();
    const missing = parsed.records.filter(record => !record.accessToken || !record.accountId).length;
    const stats = [[output.count, '已导出'], [parsed.duplicates, '重复项'], [parsed.skipped, '无效 / 不支持'], [missing, '字段不完整']];
    for (const [number, label] of stats) {
      const span = document.createElement('span');
      const strong = document.createElement('b');
      strong.textContent = number.toLocaleString();
      span.append(strong, document.createTextNode(label));
      $('reportStats').append(span);
    }
    $('accountRows').replaceChildren();
    parsed.records.slice(0, 200).forEach((record, index) => {
      const row = document.createElement('tr');
      const values = [record.email || record.name || `账号 ${index + 1}`, converter.LABELS[record.format], record.refreshToken ? '✓ 有' : '缺失', record.accessToken ? '✓ 有' : '缺失', record.warnings.join('；') || (record.refreshed ? '已联网刷新，使用最新凭据' : '已有凭据字段已保留；有效性未联网验证')];
      values.forEach((value, column) => {
        const cell = document.createElement('td');
        cell.textContent = value;
        if (column === 2 || column === 3) cell.className = value.startsWith('✓') ? 'cell-ok' : 'cell-missing';
        row.append(cell);
      });
      $('accountRows').append(row);
    });
    $('issueList').replaceChildren();
    const issues = [...parsed.issues, ...output.warnings];
    issues.slice(0, 200).forEach(issue => {
      const item = document.createElement('li');
      item.textContent = `${issue.location}：${issue.message}`;
      $('issueList').append(item);
    });
    $('reportLimit').hidden = parsed.records.length <= 200;
    if (!output.count || parsed.skipped || parsed.issues.length) {
      $('reportDetails').hidden = false;
      $('detailsButton').textContent = '收起详情';
    }
  }
  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = name;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copyOutput() {
    const file = state.output?.files[state.currentFile];
    if (!file) return;
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(file.content);
      else throw new Error('fallback');
      toast(state.output.files.length > 1 ? '已复制当前文件的完整内容' : '已复制完整内容');
    } catch {
      const textarea = document.createElement('textarea');
      textarea.className = 'clipboard-fallback';
      textarea.value = file.content;
      document.body.append(textarea);
      textarea.select();
      const success = document.execCommand('copy');
      textarea.remove();
      if (success) toast('已复制完整内容');
      else { state.revealed = true; renderPreview(); $('outputText').focus(); $('outputText').select(); toast('请按 Ctrl / ⌘ + C 复制已选中的完整内容'); }
    }
  }

  function sample(format) {
    const jwt = claims => `eyJhbGciOiJub25lIn0.${btoa(JSON.stringify(claims)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')}.DEMO_SIGNATURE`;
    const account = {
      type: 'codex', email: 'demo@example.com', account_id: 'demo-account-001',
      refresh_token: 'rt_DEMO_ONLY_not_a_real_token_001',
      access_token: jwt({ exp: 1893456000, 'https://api.openai.com/auth': { chatgpt_account_id: 'demo-account-001', chatgpt_plan_type: 'plus' } }),
      id_token: jwt({ email: 'demo@example.com' }), last_refresh: '2026-01-01T00:00:00Z', expired: '2030-01-01T00:00:00Z', plan_type: 'plus'
    };
    if (format === 'rt') return 'rt_DEMO_ONLY_not_a_real_token_001\nrt_DEMO_ONLY_not_a_real_token_002';
    if (format === 'cpa') return JSON.stringify(account, null, 2);
    return JSON.stringify({ type: 'sub2api-data', version: 1, exported_at: '2026-01-01T00:00:00Z', proxies: [], accounts: [{ name: '演示账号', platform: 'openai', type: 'oauth', credentials: { email: account.email, chatgpt_account_id: account.account_id, refresh_token: account.refresh_token, access_token: account.access_token, id_token: account.id_token, expires_at: account.expired, plan_type: 'plus' }, concurrency: 10, priority: 1 }] }, null, 2);
  }

  async function bridgeRequest(path, payload) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(path, { method: 'POST', cache: 'no-store', headers: {
        'Content-Type': 'application/json', 'X-Local-GPT-Key': state.bridgeKey
      }, body: JSON.stringify(payload), signal: controller.signal });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `本机服务返回 HTTP ${response.status}`);
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('本机请求超时；请勿反复刷新旧 RT');
      if (error instanceof TypeError) throw new Error('无法连接本机服务，请重新启动工具');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function lockRefresh(locked) {
    state.refreshing = locked;
    const controls = ['inputText', 'sourceFormat', 'clearButton', 'folderButton', 'convertButton',
      'fileInput', 'folderInput', 'deduplicate', 'preserveExtra', 'concurrency', 'priority',
      'checkNetworkButton', 'networkMode', 'httpProxyUrl', 'oauthClientId', 'shutdownButton'];
    controls.forEach(id => { $(id).disabled = locked; });
    document.querySelectorAll('[data-example], [data-target], .file-chip button').forEach(button => { button.disabled = locked; });
    if (!locked) $('httpProxyUrl').disabled = $('networkMode').value !== 'proxy';
    $('refreshProgress').hidden = !locked;
  }
  async function refreshAndConvert() {
    if (state.refreshing || state.loading) return;
    if (!state.bridgeKey) await checkBridge();
    if (state.refreshing || state.loading) return;
    if (!state.bridgeKey) { message('请双击「启动工具.bat」打开工具，再联网转换。直接打开 HTML 无法刷新凭据。', 'warning'); return; }
    let parsed;
    try {
      options();
      parsed = parseSource(false);
      if (!parsed?.records.length) { renderConversion(); return; }
      if ($('networkMode').value === 'proxy' && !$('httpProxyUrl').value.trim()) throw new Error('请先填写代理地址');
    } catch (error) { message(error.message, 'error'); return; }
    const settings = { clientId: $('oauthClientId').value.trim(), networkMode: $('networkMode').value,
      proxyUrl: $('httpProxyUrl').value.trim(), skipFresh: true, shouldStop: () => state.stopRefresh };
    state.stopRefresh = false;
    $('stopRefreshButton').disabled = false;
    state.output = null;
    renderOutput();
    lockRefresh(true);
    $('progressText').textContent = '正在发送 OAuth 刷新请求…';
    message('正在联网补全，完成后会使用最新凭据生成结果。');
    try {
      const result = await window.GPTOnline.refreshBatch(parsed.records, async payload => (await bridgeRequest('/api/refresh', payload)).tokens, settings,
        progress => { $('progressText').textContent = `已处理 ${progress.completed} / ${progress.total} 组 RT · 成功 ${progress.succeeded} 个账号 · 失败 ${progress.failed} 个账号`; });
      const inputIssues = parsed.issues.filter(issue => !issue.online);
      state.onlineSnapshot = { records: result.records, issues: [...inputIssues, ...result.issues], skipped: parsed.skipped };
      // Keep rotated RTs in the source as well as the download result. Format switching,
      // importing another file or clicking refresh again therefore uses the latest RT.
      const latest = converter.exportRecords(result.records, 'cpa', { preserveExtra: true }).files;
      state.documents = latest.map(file => ({ name: file.name, content: file.content }));
      state.importIssues = inputIssues;
      $('inputText').value = '';
      $('sourceFormat').value = 'auto';
      renderDocuments(); updateInputMeta();
      lockRefresh(false);
      renderConversion();
      const warning = result.failed || result.noRT || result.unattempted || (state.target !== 'rt' && state.output.count < result.records.length);
      $('resultBadge').textContent = state.output.count ? `${state.output.count} 个账号 · ${warning ? '查看报告' : '已完成'}` : '没有完整凭据可导出';
      message(`联网转换完成：${result.succeeded} 个账号刷新成功，${result.failed} 个失败${result.reused ? `，${result.reused} 个使用本次已刷新的凭据` : ''}${result.unattempted ? `，${result.unattempted} 个未发送` : ''}${result.noRT ? `，${result.noRT} 个无 RT 无法刷新` : ''}。${result.failed || result.noRT || result.unattempted ? '原数据保留在输入中，失败原因见报告。' : ''}${state.output.count ? '请下载保存最新结果。' : '未生成完整凭据，请处理报告中的问题。'}`, warning ? 'warning' : '');
    } catch (error) { message(error.message, 'error'); }
    finally { lockRefresh(false); }
  }
  async function checkBridge() {
    if (location.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(location.hostname)) return;
    try {
      const response = await fetch('/api/health', { cache: 'no-store', signal: AbortSignal.timeout(3000) });
      const health = await response.json();
      if (!response.ok || health.service !== 'local-gpt-converter') return;
      if (health.version !== '1.3.0') { $('serviceNote').textContent = '本机仍运行旧版本，请关闭旧服务后重新双击「启动工具.bat」。'; return; }
      state.bridgeKey = health.session_key;
      $('serviceBadge').textContent = '本机服务已连接';
      $('serviceBadge').className = 'subtle-badge ready';
      $('serviceNote').textContent = `已就绪 · 当前系统连接：${health.connection}。点击「联网转换」使用 RT 获取最新凭据。`;
      $('modeBadge').querySelector('span:last-child').textContent = '默认联网 · 本机运行';
      $('shutdownButton').hidden = false;
    } catch { $('serviceNote').textContent = '本机服务未响应，请重新启动「启动工具.bat」。'; }
  }

  async function checkNetwork() {
    if (state.refreshing || state.loading) return;
    if (!state.bridgeKey) await checkBridge();
    if (state.refreshing || state.loading) return;
    if (!state.bridgeKey) { message('请先双击「启动工具.bat」启动本机服务。', 'warning'); return; }
    lockRefresh(true);
    $('stopRefreshButton').hidden = true;
    $('progressText').textContent = '正在检测所选连接…';
    try {
      const result = await bridgeRequest('/api/network-check', { client_id: $('oauthClientId').value.trim(), network_mode: $('networkMode').value, proxy_url: $('httpProxyUrl').value.trim() });
      $('networkResult').hidden = false;
      $('networkResult').textContent = `${result.connection}${result.upstream_status ? ` · HTTP ${result.upstream_status}` : ''}：${result.message}`;
      $('networkResult').className = `network-result ${result.reachable ? 'ready' : 'warning'}`;
    } catch (error) {
      $('networkResult').hidden = false;
      $('networkResult').className = 'network-result warning';
      $('networkResult').textContent = error.message;
    } finally { lockRefresh(false); $('stopRefreshButton').hidden = false; }
  }

  $('inputText').addEventListener('input', () => { invalidate(); updateInputMeta(); });
  $('sourceFormat').addEventListener('change', () => { invalidate(); updateInputMeta(); });
  $('clearButton').addEventListener('click', () => {
    if (state.refreshing) return;
    state.job++;
    state.loading = false;
    state.documents = [];
    state.importIssues = [];
    state.revealed = false;
    $('convertButton').disabled = false;
    $('inputText').value = '';
    $('reportDetails').hidden = true;
    $('detailsButton').textContent = '展开详情';
    invalidate(); renderDocuments(); updateInputMeta();
    $('detectedFormat').textContent = '等待输入';
  });
  $('convertButton').addEventListener('click', () => { void refreshAndConvert(); });
  $('checkNetworkButton').addEventListener('click', () => { void checkNetwork(); });
  $('networkMode').addEventListener('change', () => { $('httpProxyUrl').disabled = $('networkMode').value !== 'proxy'; });
  $('stopRefreshButton').addEventListener('click', () => { state.stopRefresh = true; $('stopRefreshButton').disabled = true; $('progressText').textContent = '等待已经发送的请求完成，随后生成结果…'; });
  $('shutdownButton').addEventListener('click', async () => {
    try {
      await bridgeRequest('/api/shutdown', {});
      state.bridgeKey = '';
      $('serviceBadge').textContent = '本机服务已停止';
      $('serviceBadge').className = 'subtle-badge';
      $('serviceNote').textContent = '本机服务已停止，已生成结果仍可下载。再次联网时请重新启动工具。';
      $('shutdownButton').hidden = true;
    } catch (error) { message(error.message, 'error'); }
  });
  window.addEventListener('beforeunload', event => { if (state.refreshing) { event.preventDefault(); event.returnValue = ''; } });
  document.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); void refreshAndConvert(); } });
  document.querySelectorAll('[data-target]').forEach(button => button.addEventListener('click', () => {
    state.target = button.dataset.target;
    document.querySelectorAll('[data-target]').forEach(tab => { const active = tab === button; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active)); });
    if (state.parsed) renderConversion(); else renderOutput();
  }));
  for (const id of ['deduplicate', 'preserveExtra', 'concurrency', 'priority']) $(id).addEventListener('change', () => { if (state.parsed) renderConversion(); });
  document.querySelectorAll('[data-example]').forEach(button => button.addEventListener('click', () => {
    $('clearButton').click();
    $('sourceFormat').value = 'auto';
    $('inputText').value = sample(button.dataset.example);
    invalidate(); updateInputMeta();
    toast('已填入虚构示例 Token');
  }));
  $('dropZone').addEventListener('click', () => $('fileInput').click());
  $('dropZone').addEventListener('keydown', event => { if (event.target === $('dropZone') && ['Enter', ' '].includes(event.key)) { event.preventDefault(); $('fileInput').click(); } });
  $('folderButton').addEventListener('click', event => { event.stopPropagation(); $('folderInput').click(); });
  $('fileInput').addEventListener('change', event => { void importFiles(event.target.files); });
  $('folderInput').addEventListener('change', event => { void importFiles(event.target.files); });
  for (const eventName of ['dragenter', 'dragover']) $('dropZone').addEventListener(eventName, event => { event.preventDefault(); $('dropZone').classList.add('drag-over'); });
  $('dropZone').addEventListener('dragleave', () => $('dropZone').classList.remove('drag-over'));
  $('dropZone').addEventListener('drop', event => { event.preventDefault(); $('dropZone').classList.remove('drag-over'); void importFiles(event.dataTransfer.files); });
  document.addEventListener('dragover', event => event.preventDefault());
  document.addEventListener('drop', event => event.preventDefault());
  $('revealButton').addEventListener('click', () => { state.revealed = !state.revealed; renderPreview(); });
  $('copyButton').addEventListener('click', () => { void copyOutput(); });
  $('downloadButton').addEventListener('click', () => {
    const files = state.output?.files;
    if (!files?.length) return;
    try {
      if (files.length > 1) saveBlob(new Blob([archive.createZip(files)], { type: 'application/zip' }), 'cpa-accounts.zip');
      else saveBlob(new Blob([files[0].content], { type: state.target === 'rt' ? 'text/plain;charset=utf-8' : 'application/json;charset=utf-8' }), files[0].name);
      toast('下载已开始，文件包含完整 Token');
    } catch (error) { message(error.message, 'error'); }
  });
  $('detailsButton').addEventListener('click', () => { $('reportDetails').hidden = !$('reportDetails').hidden; $('detailsButton').textContent = $('reportDetails').hidden ? '展开详情' : '收起详情'; });
  $('helpButton').addEventListener('click', () => $('helpDialog').showModal());
  $('closeHelp').addEventListener('click', () => $('helpDialog').close());
  $('helpDialog').addEventListener('click', event => { if (event.target === $('helpDialog')) { const rect = $('helpDialog').getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('helpDialog').close(); } });
  renderOutput();
  void checkBridge();
})();
