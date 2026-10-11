(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.GPTConverter = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const LABELS = { rt: 'RT', sub2: 'Sub2API', cpa: 'CPA' };
  const LIMIT = 5000;
  const clone = value => JSON.parse(JSON.stringify(value));
  const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const str = (...values) => values.find(value => typeof value === 'string' && value.trim())?.trim() || '';

  function jwtPayload(token) {
    try {
      const parts = token.split('.');
      if (parts.length !== 3) return {};
      let encoded = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      encoded += '='.repeat((4 - encoded.length % 4) % 4);
      const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
      const value = JSON.parse(new TextDecoder().decode(bytes));
      return isObject(value) ? value : {};
    } catch { return {}; }
  }

  function isoTime(value) {
    if (value === undefined || value === null || value === '') return '';
    let milliseconds;
    if (typeof value === 'number' || /^\d+(\.\d+)?$/.test(String(value))) {
      const number = Number(value);
      milliseconds = number < 1e12 ? number * 1000 : number;
    } else milliseconds = Date.parse(value);
    if (!Number.isFinite(milliseconds)) return '';
    try { return new Date(milliseconds).toISOString(); } catch { return ''; }
  }

  function cleanCPA(value) {
    const output = clone(value);
    delete output._gpt_converter;
    return output;
  }

  function cleanSub(value) {
    const output = clone(value);
    if (isObject(output.extra)) {
      delete output.extra._gpt_converter;
      if (!Object.keys(output.extra).length) delete output.extra;
    }
    return output;
  }

  function normalize(tokens, context) {
    for (const key of ['refresh_token', 'access_token', 'id_token']) {
      if (tokens[key] !== undefined && tokens[key] !== null && typeof tokens[key] !== 'string') {
        throw new Error(`${key} 必须是字符串`);
      }
    }
    const accessToken = str(tokens.access_token, tokens.accessToken);
    const idToken = str(tokens.id_token, tokens.idToken);
    const refreshToken = str(tokens.refresh_token, tokens.refreshToken);
    if (!accessToken && !refreshToken && !idToken) throw new Error('没有找到 Token 字段');
    const access = jwtPayload(accessToken);
    const identity = jwtPayload(idToken);
    const accessAuth = isObject(access['https://api.openai.com/auth']) ? access['https://api.openai.com/auth'] : {};
    const idAuth = isObject(identity['https://api.openai.com/auth']) ? identity['https://api.openai.com/auth'] : {};
    const profile = isObject(access['https://api.openai.com/profile']) ? access['https://api.openai.com/profile'] : {};
    const organization = value => str(value.organization_id, value.chatgpt_organization_id, value.chatgpt_org_id, value.org_id);
    const email = str(tokens.email, context.email, access.email, profile.email, identity.email);
    const explicitAccountId = str(tokens.chatgpt_account_id, tokens.account_id);
    const accountId = str(explicitAccountId, accessAuth.chatgpt_account_id, accessAuth.account_id, idAuth.chatgpt_account_id, idAuth.account_id);
    const expiry = tokens.expired ?? tokens.expires_at ?? access.exp;
    const expiresAt = isoTime(expiry);
    const warnings = [];
    if (!accessToken) warnings.push('缺少 access_token：导出后可能需要目标服务联网刷新');
    if (!refreshToken) warnings.push('缺少 refresh_token：无法导出 RT，也无法靠 RT 自动续期');
    if (!accountId) warnings.push('缺少账号 ID：请联网获取完整凭据');
    if (expiry !== undefined && expiry !== null && expiry !== '' && !expiresAt) warnings.push('到期时间无法识别，保留原字段');
    return {
      ...context, accessToken, idToken, refreshToken, email, accountId, expiresAt,
      accountIdExplicit: Boolean(explicitAccountId),
      userId: str(tokens.chatgpt_user_id, tokens.user_id, accessAuth.chatgpt_user_id, accessAuth.user_id, access.user_id, idAuth.chatgpt_user_id, idAuth.user_id, identity.user_id),
      organizationId: str(organization(tokens), organization(accessAuth), organization(access), organization(idAuth), organization(identity)),
      clientId: str(tokens.client_id, access.client_id),
      planType: str(tokens.plan_type, accessAuth.chatgpt_plan_type, idAuth.chatgpt_plan_type),
      lastRefresh: str(tokens.last_refresh, context.lastRefresh, context.cpaDocument?.last_refresh),
      name: str(context.name, email), warnings
    };
  }

  function parseInput(text, options = {}) {
    const sourceFormat = options.sourceFormat || 'auto';
    if (!['auto', 'rt', 'sub2', 'cpa'].includes(sourceFormat)) throw new Error('未知输入格式');
    const label = options.label || '粘贴输入';
    const records = [];
    const issues = [];
    let skipped = 0;
    const issue = (location, message) => { skipped++; if (issues.length < 200) issues.push({ location, message }); };
    function append(tokens, context, location) {
      if (records.length >= LIMIT) throw new Error(`最多支持 ${LIMIT} 个账号，请分批转换`);
      if (sourceFormat !== 'auto' && context.format !== sourceFormat) {
        issue(location, `识别为 ${LABELS[context.format]}，与指定的输入格式不一致`);
        return;
      }
      try { records.push(normalize(tokens, { ...context, location })); }
      catch (error) { issue(location, error.message); }
    }
    function parseRT(line, location) {
      const value = line.trim();
      if (!value || value.startsWith('#') || value.startsWith('//') || /^refresh_?token$/i.test(value)) return;
      const parts = value.split(/----|\t|\||,/).map(part => part.trim());
      const token = parts[parts.length - 1];
      const email = parts.length > 1 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(parts[0]) ? parts[0] : '';
      if (token.length < 16 || !/^[A-Za-z0-9._~+\/=\-]+$/.test(token) || (parts.length > 1 && !email)) {
        issue(location, '不是有效的 RT 文本；支持每行一个 Token 或「邮箱----Token」');
        return;
      }
      append({ refresh_token: token, email }, { format: 'rt' }, location);
    }
    function walk(value, location, proxies = [], depth = 0) {
      if (depth > 12) { issue(location, 'JSON 嵌套过深'); return; }
      if (typeof value === 'string') { parseRT(value, location); return; }
      if (Array.isArray(value)) {
        for (let i = 0; i < value.length; i++) walk(value[i], `${location} / 第 ${i + 1} 项`, proxies, depth + 1);
        return;
      }
      if (!isObject(value)) { issue(location, '需要账号对象、数组或 RT 字符串'); return; }
      if (Array.isArray(value.accounts)) {
        walk(value.accounts, location, Array.isArray(value.proxies) ? value.proxies : proxies, depth + 1);
        return;
      }
      if (isObject(value.data) || Array.isArray(value.data)) { walk(value.data, location, proxies, depth + 1); return; }
      if (isObject(value.credentials)) {
        if (value.platform && value.platform !== 'openai') { issue(location, '仅支持 OpenAI / ChatGPT 账号'); return; }
        if (value.type && value.type !== 'oauth') { issue(location, '仅支持 OAuth 账号，不支持 API Key 账号'); return; }
        const capsule = isObject(value.extra?._gpt_converter) ? value.extra._gpt_converter : {};
        append(value.credentials, {
          format: 'sub2', name: value.name, email: value.email,
          subAccount: cleanSub(value), proxies: clone(proxies),
          cpaDocument: isObject(capsule.cpa) ? cleanCPA(capsule.cpa) : undefined
        }, location);
        return;
      }
      if (value.type && value.type !== 'codex' && value.type !== 'rt') {
        issue(location, '仅支持 type=codex 的 CPA 凭据'); return;
      }
      if (value.type === 'codex' || isObject(value.tokens) || value.access_token || value.id_token || value.account_id || value.expired) {
        const tokens = isObject(value.tokens) ? { ...value, ...value.tokens } : value;
        const capsule = isObject(value._gpt_converter) ? value._gpt_converter : {};
        append(tokens, {
          format: 'cpa', name: value.name, lastRefresh: value.last_refresh,
          cpaDocument: cleanCPA(value),
          subAccount: isObject(capsule.sub2?.account) ? cleanSub(capsule.sub2.account) : undefined,
          proxies: Array.isArray(capsule.sub2?.proxies) ? clone(capsule.sub2.proxies) : []
        }, location);
        return;
      }
      if (typeof value.refresh_token === 'string') {
        append(value, { format: 'rt', email: value.email }, location);
        return;
      }
      issue(location, '无法识别 RT、Sub2API 或 CPA 结构');
    }
    const content = text.replace(/^\uFEFF/, '').trim();
    if (!content) return { records, issues, skipped, formats: [] };
    if (content.length > 20 * 1024 * 1024) throw new Error('单次文本最多 20 MB，请分批转换');
    if (/^[\[{\"]/.test(content)) {
      try {
        const value = JSON.parse(content);
        walk(value, label);
      } catch (error) {
        if (!(error instanceof SyntaxError)) throw error;
        const lines = content.split(/\r?\n/).filter(line => line.trim());
        if (lines.length > 1 && lines.every(line => /^[\[{]/.test(line.trim()))) {
          lines.forEach((line, i) => {
            try { walk(JSON.parse(line), `${label} / 第 ${i + 1} 行`); }
            catch { issue(`${label} / 第 ${i + 1} 行`, 'JSON 语法错误'); }
          });
        } else issue(label, 'JSON 语法错误，请检查逗号、引号和括号；不会按 RT 文本处理损坏的 JSON');
      }
    } else content.split(/\r?\n/).forEach((line, i) => parseRT(line, `${label} / 第 ${i + 1} 行`));
    return { records, issues, skipped, formats: [...new Set(records.map(record => record.format))] };
  }

  function mergeResults(results, deduplicate = true) {
    let records = results.flatMap(result => result.records);
    const issues = results.flatMap(result => result.issues);
    let duplicates = 0;
    if (deduplicate) {
      const positions = new Map();
      const unique = [];
      const quality = record => [record.accessToken, record.idToken, record.email, record.subAccount, record.cpaDocument].filter(Boolean).length;
      for (const record of records) {
        const key = `${record.refreshToken || record.accessToken || record.idToken}\u0000${record.accountId}`;
        if (positions.has(key)) {
          duplicates++;
          const index = positions.get(key);
          if (quality(record) > quality(unique[index])) unique[index] = record;
        } else { positions.set(key, unique.length); unique.push(record); }
      }
      records = unique;
    }
    if (records.length > LIMIT) throw new Error(`最多支持 ${LIMIT} 个账号，请分批转换`);
    return { records, issues: issues.slice(0, 200), skipped: results.reduce((sum, result) => sum + result.skipped, 0), duplicates,
      formats: [...new Set(records.map(record => record.format))] };
  }

  function toCPA(record, preserveExtra) {
    const output = preserveExtra && record.cpaDocument ? cleanCPA(record.cpaDocument) : {};
    Object.assign(output, {
      type: 'codex', id_token: record.idToken, access_token: record.accessToken,
      refresh_token: record.refreshToken, account_id: record.accountId,
      email: record.email, last_refresh: record.lastRefresh
    });
    if (record.expiresAt) output.expired = record.expiresAt;
    else if (output.expired === undefined) output.expired = '';
    if (record.planType) output.plan_type = record.planType;
    if (record.clientId) output.client_id = record.clientId;
    if (record.userId) output.chatgpt_user_id = record.userId;
    if (record.organizationId) output.organization_id = record.organizationId;
    // Codex auth.json carries the same credentials in tokens. Keep that copy
    // aligned so importing this CPA file cannot restore an older rotation.
    if (isObject(output.tokens)) {
      Object.assign(output.tokens, {
        access_token: record.accessToken, refresh_token: record.refreshToken,
        id_token: record.idToken, account_id: record.accountId, email: record.email,
        chatgpt_user_id: record.userId, organization_id: record.organizationId,
        plan_type: record.planType, client_id: record.clientId, last_refresh: record.lastRefresh
      });
      if (output.tokens.chatgpt_account_id !== undefined) output.tokens.chatgpt_account_id = record.accountId;
      for (const key of ['expired', 'expires_at']) if (output.tokens[key] !== undefined) {
        if (record.expiresAt) output.tokens[key] = record.expiresAt;
        else if (record.refreshed) delete output.tokens[key];
      }
      if (record.refreshed) {
        delete output.tokens.expires_in;
        delete output.tokens._token_version;
      }
    }
    if (preserveExtra && record.subAccount) {
      output._gpt_converter = { version: 1, sub2: { account: cleanSub(record.subAccount), proxies: record.proxies || [] } };
    }
    return output;
  }

  function toSub2(record, index, options) {
    const preserve = options.preserveExtra !== false;
    const output = preserve && record.subAccount ? cleanSub(record.subAccount) : {
      name: record.name || `OpenAI ${String(index + 1).padStart(3, '0')}`,
      platform: 'openai', type: 'oauth', credentials: {},
      concurrency: options.concurrency ?? 10, priority: options.priority ?? 1
    };
    output.platform = 'openai';
    output.type = 'oauth';
    if (!str(output.name)) output.name = record.email || `OpenAI ${String(index + 1).padStart(3, '0')}`;
    const credentials = { ...(isObject(output.credentials) ? output.credentials : {}) };
    const mappings = {
      access_token: record.accessToken, refresh_token: record.refreshToken, id_token: record.idToken,
      email: record.email, chatgpt_account_id: record.accountId, chatgpt_user_id: record.userId,
      organization_id: record.organizationId, client_id: record.clientId, plan_type: record.planType,
      last_refresh: record.lastRefresh
    };
    for (const [key, value] of Object.entries(mappings)) if (value || credentials[key] !== undefined) credentials[key] = value;
    // A preserved Sub2API timestamp keeps its original representation (Unix seconds or RFC3339).
    if (record.expiresAt && (!record.subAccount || !preserve)) credentials.expires_at = record.expiresAt;
    output.credentials = credentials;
    if (preserve && record.cpaDocument) {
      output.extra = { ...(isObject(output.extra) ? output.extra : {}),
        _gpt_converter: { version: 1, cpa: cleanCPA(record.cpaDocument) } };
    }
    return output;
  }

  function safeFilename(name) {
    return String(name).replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').replace(/[. ]+$/g, '').slice(0, 110) || 'account';
  }

  function exportRecords(records, target, options = {}) {
    if (!LABELS[target]) throw new Error('未知输出格式');
    const warnings = [];
    const files = [];
    const preserve = options.preserveExtra !== false;
    if (target === 'rt') {
      const values = [];
      records.forEach((record, i) => {
        if (record.refreshToken) values.push(record.refreshToken);
        else warnings.push({ location: record.location || `第 ${i + 1} 个账号`, message: '缺少 refresh_token，无法导出 RT，已跳过' });
      });
      const tokens = options.deduplicate === false ? values : [...new Set(values)];
      if (tokens.length) files.push({ name: 'refresh-tokens.txt', content: tokens.join('\n') + '\n' });
      if (tokens.length) warnings.push({ location: 'RT 导出', message: 'RT 文本只保留 refresh_token；邮箱、访问令牌和其他字段不会写入文本' });
      return { files, preview: files[0]?.content || '', warnings, count: tokens.length };
    }
    if (target === 'sub2') {
      if (!records.length) return { files, preview: '', warnings, count: 0 };
      const accounts = records.map((record, index) => toSub2(record, index, options));
      const proxyMap = new Map();
      if (preserve) for (const record of records) {
        for (const proxy of record.proxies || []) {
          if (!isObject(proxy) || !str(proxy.proxy_key)) continue;
          if (proxyMap.has(proxy.proxy_key) && JSON.stringify(proxyMap.get(proxy.proxy_key)) !== JSON.stringify(proxy)) {
            warnings.push({ location: '代理配置', message: '存在同名代理键但配置不同，已采用首个配置' });
          } else proxyMap.set(proxy.proxy_key, clone(proxy));
        }
      }
      for (const account of accounts) if (account.proxy_key && !proxyMap.has(account.proxy_key)) {
        delete account.proxy_key;
        warnings.push({ location: account.name, message: '未找到关联代理配置，已移除悬空的 proxy_key' });
      }
      const payload = { type: 'sub2api-data', version: 1, exported_at: options.now || new Date().toISOString(), proxies: [...proxyMap.values()], accounts };
      const content = JSON.stringify(payload, null, 2) + '\n';
      files.push({ name: 'sub2api-accounts.json', content });
      return { files, preview: content, warnings, count: accounts.length };
    }
    const names = new Set();
    records.forEach((record, index) => {
      const base = `codex-${safeFilename(record.email || record.accountId || record.name || `account-${String(index + 1).padStart(3, '0')}`)}`;
      let occurrence = 1;
      let name = `${base}.json`;
      while (names.has(name.toLowerCase())) name = `${base}-${++occurrence}.json`;
      names.add(name.toLowerCase());
      files.push({ name, content: JSON.stringify(toCPA(record, preserve), null, 2) + '\n' });
    });
    return { files, preview: files[0]?.content || '', warnings, count: files.length };
  }

  function maskPreview(content, target) {
    if (target === 'rt') return content.split('\n').map(line => line ? `${line.slice(0, 6)}••••••••${line.slice(-4)}` : '').join('\n');
    try {
      const value = JSON.parse(content);
      function redact(item) {
        if (Array.isArray(item)) return item.map(redact);
        if (!isObject(item)) return item;
        return Object.fromEntries(Object.entries(item).map(([key, entry]) => {
          if (/token|password|secret|api[_-]?key/i.test(key) && typeof entry === 'string' && entry) return [key, `${entry.slice(0, 5)}••••••••`];
          return [key, redact(entry)];
        }));
      }
      return JSON.stringify(redact(value), null, 2) + '\n';
    } catch { return content; }
  }

  return { LABELS, parseInput, mergeResults, exportRecords, maskPreview, jwtPayload, isoTime, safeFilename };
});
