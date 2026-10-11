(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.GPTOnline = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const DEFAULT_CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann';
  // Build jobs once per RT/client pair. Account IDs may differ while sharing one RT.
  function refreshGroups(records, clientOverride = '', skipFresh = false) {
    const groups = new Map();
    records.forEach((record, index) => {
      if (!record.refreshToken) return;
      if (skipFresh && record.refreshed && record.accessToken && (!record.expiresAt || Date.parse(record.expiresAt) > Date.now() + 30000) && (!clientOverride.trim() || clientOverride.trim() === record.clientId)) return;
      const clientId = clientOverride.trim() || record.clientId || DEFAULT_CLIENT_ID;
      const key = `${clientId}\u0000${record.refreshToken}`;
      if (!groups.has(key)) groups.set(key, { refreshToken: record.refreshToken, clientId, indices: [] });
      groups.get(key).indices.push(index);
    });
    return [...groups.values()];
  }
  async function refreshBatch(records, requestRefresh, settings = {}, onProgress = () => {}) {
    const converter = typeof module === 'object' && module.exports ? require('./converter.js') : globalThis.GPTConverter;
    const output = records.map(record => ({ ...record }));
    const groups = refreshGroups(records, settings.clientId || '', settings.skipFresh);
    const reused = records.filter(record => record.refreshToken).length - groups.reduce((sum, group) => sum + group.indices.length, 0);
    const issues = [];
    let succeeded = 0;
    let failed = 0;
    let completed = 0;
    let unattempted = 0;
    const tasks = groups.slice();
    const worker = async () => {
      while (tasks.length) {
        if (settings.shouldStop?.()) break;
        const group = tasks.shift();
        try {
          const tokens = await requestRefresh({ refresh_token: group.refreshToken, client_id: group.clientId,
            network_mode: settings.networkMode || 'auto', proxy_url: settings.proxyUrl || '' });
          for (const index of group.indices) {
            const previous = records[index];
            const document = {
              type: 'codex', refresh_token: tokens.refresh_token || previous.refreshToken,
              access_token: tokens.access_token, id_token: tokens.id_token || previous.idToken,
              client_id: tokens.client_id || group.clientId, last_refresh: tokens.last_refresh,
              expires_at: tokens.expires_at
            };
            const fresh = converter.parseInput(JSON.stringify(document)).records[0];
            if (!fresh?.accessToken) throw new Error('刷新响应缺少访问令牌');
            // Preserve explicit account choice for multi-account refresh tokens.
            const merged = { ...previous, ...fresh, format: previous.format, location: previous.location,
              name: previous.name, email: fresh.email || previous.email,
              accountId: previous.accountIdExplicit ? previous.accountId : fresh.accountId || previous.accountId,
              accountIdExplicit: previous.accountIdExplicit,
              userId: fresh.userId || previous.userId, organizationId: fresh.organizationId || previous.organizationId,
              planType: fresh.planType || previous.planType,
              subAccount: previous.subAccount, cpaDocument: previous.cpaDocument, proxies: previous.proxies,
              refreshed: true };
            merged.warnings = [];
            if (!merged.accountId) merged.warnings.push('刷新返回的令牌中没有账号 ID');
            if (!merged.email) merged.warnings.push('刷新返回的令牌中没有邮箱');
            if (!merged.expiresAt) merged.warnings.push('刷新返回的令牌中没有到期时间');
            if (merged.subAccount) {
              merged.subAccount = JSON.parse(JSON.stringify(merged.subAccount));
              merged.subAccount.credentials = { ...merged.subAccount.credentials, expires_at: merged.expiresAt,
                last_refresh: merged.lastRefresh, access_token: merged.accessToken,
                refresh_token: merged.refreshToken, id_token: merged.idToken,
                email: merged.email, chatgpt_account_id: merged.accountId, client_id: merged.clientId };
              if (!merged.expiresAt) delete merged.subAccount.credentials.expires_at;
              // expired is a CPA alias that may have been carried as an extra
              // Sub2 credential. It must describe the new token as well.
              if (merged.subAccount.credentials.expired !== undefined) {
                if (merged.expiresAt) merged.subAccount.credentials.expired = merged.expiresAt;
                else delete merged.subAccount.credentials.expired;
              }
              // expires_in describes this new token only. Remove stale lifetime metadata.
              delete merged.subAccount.credentials.expires_in;
              delete merged.subAccount.credentials._token_version;
            }
            if (merged.cpaDocument) {
              merged.cpaDocument = { ...merged.cpaDocument, access_token: merged.accessToken,
                refresh_token: merged.refreshToken, id_token: merged.idToken, email: merged.email,
                account_id: merged.accountId, expired: merged.expiresAt, last_refresh: merged.lastRefresh };
              if (merged.cpaDocument.chatgpt_account_id !== undefined) merged.cpaDocument.chatgpt_account_id = merged.accountId;
              if (merged.cpaDocument.expires_at !== undefined) {
                if (merged.expiresAt) merged.cpaDocument.expires_at = merged.expiresAt;
                else delete merged.cpaDocument.expires_at;
              }
              delete merged.cpaDocument.expires_in;
              delete merged.cpaDocument._token_version;
              if (merged.cpaDocument.tokens && typeof merged.cpaDocument.tokens === 'object' && !Array.isArray(merged.cpaDocument.tokens)) {
                merged.cpaDocument.tokens = { ...merged.cpaDocument.tokens,
                  access_token: merged.accessToken, refresh_token: merged.refreshToken,
                  id_token: merged.idToken, account_id: merged.accountId, email: merged.email,
                  chatgpt_user_id: merged.userId, organization_id: merged.organizationId,
                  plan_type: merged.planType, client_id: merged.clientId, last_refresh: merged.lastRefresh };
                if (merged.cpaDocument.tokens.chatgpt_account_id !== undefined) merged.cpaDocument.tokens.chatgpt_account_id = merged.accountId;
                for (const key of ['expired', 'expires_at']) if (merged.cpaDocument.tokens[key] !== undefined) {
                  if (merged.expiresAt) merged.cpaDocument.tokens[key] = merged.expiresAt;
                  else delete merged.cpaDocument.tokens[key];
                }
                delete merged.cpaDocument.tokens.expires_in;
                delete merged.cpaDocument.tokens._token_version;
              }
            }
            output[index] = merged;
          }
          succeeded += group.indices.length;
        } catch (error) {
          failed += group.indices.length;
          for (const index of group.indices) issues.push({ online: true, location: records[index].location || `账号 ${index + 1}`, message: `联网获取失败，保留原凭据：${error.message}` });
        }
        onProgress({ completed: ++completed, total: groups.length, succeeded, failed });
      }
    };
    const workers = Array.from({ length: Math.min(2, tasks.length) }, worker);
    await Promise.all(workers);
    unattempted = tasks.reduce((sum, group) => sum + group.indices.length, 0);
    if (unattempted) issues.push({ online: true, location: '联网转换', message: `${unattempted} 个账号未发送刷新请求，保留原凭据` });
    const noRT = records.filter(record => !record.refreshToken).length;
    if (noRT) issues.push({ online: true, location: '联网转换', message: `${noRT} 个账号没有 RT，保留原凭据` });
    return { records: output, issues, succeeded, failed, noRT, unattempted, reused, requests: completed };
  }
  return { DEFAULT_CLIENT_ID, refreshGroups, refreshBatch };
});
