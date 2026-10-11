const test = require('node:test');
const assert = require('node:assert/strict');
const converter = require('../converter.js');
const online = require('../online.js');
const RT = 'rt_DEMO_ONLY_not_a_real_token_001';
const NEW_RT = 'rt_DEMO_ONLY_rotated_token_001';
const jwt = claims => `eyJhbGciOiJub25lIn0.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.DEMO_SIGNATURE`;
const access = jwt({ exp: 1893456000, 'https://api.openai.com/auth': { chatgpt_account_id: 'fresh-account', chatgpt_plan_type: 'plus' }, 'https://api.openai.com/profile': { email: 'fresh@example.com' } });
const freshTokens = { access_token: access, refresh_token: NEW_RT, id_token: jwt({ email: 'fresh@example.com' }), last_refresh: '2026-10-10T00:00:00Z', expires_at: '2030-01-01T00:00:00Z' };
const parse = value => converter.parseInput(typeof value === 'string' ? value : JSON.stringify(value)).records;

test('RT online refresh fills identity, exports rotated RT and tracks refresh progress', async () => {
  const progress = [];
  const result = await online.refreshBatch(parse(RT), async payload => { assert.equal(payload.client_id, online.DEFAULT_CLIENT_ID); return freshTokens; }, {}, value => progress.push(value));
  assert.equal(result.succeeded, 1);
  assert.equal(result.records[0].email, 'fresh@example.com');
  assert.equal(result.records[0].accountId, 'fresh-account');
  assert.equal(result.records[0].refreshToken, NEW_RT);
  assert.equal(result.records[0].lastRefresh, freshTokens.last_refresh);
  assert.equal(converter.exportRecords(result.records, 'rt').preview, NEW_RT + '\n');
  assert.equal(JSON.parse(converter.exportRecords(result.records, 'cpa').preview).access_token, access);
  const sub2 = JSON.parse(converter.exportRecords(result.records, 'sub2').preview);
  assert.equal(sub2.accounts[0].credentials.last_refresh, freshTokens.last_refresh);
  const roundTrip = converter.parseInput(JSON.stringify(sub2)).records;
  assert.equal(JSON.parse(converter.exportRecords(roundTrip, 'cpa').preview).last_refresh, freshTokens.last_refresh);
  assert.equal(progress[0].completed, 1);
});
test('same RT and client ID are refreshed once across different account IDs and duplicate records', async () => {
  const records = parse([{ type: 'codex', refresh_token: RT, account_id: 'account-one' }, { type: 'codex', refresh_token: RT, account_id: 'account-two' }, { type: 'codex', refresh_token: RT, account_id: 'account-one' }]);
  let requests = 0;
  const result = await online.refreshBatch(records, async () => { requests++; return freshTokens; });
  assert.equal(requests, 1);
  assert.equal(result.succeeded, 3);
  assert.equal(result.records[0].accountId, 'account-one');
  assert.equal(result.records[1].accountId, 'account-two');
});
test('input client ID is used by default and explicit client override takes precedence', async () => {
  const records = parse({ type: 'codex', refresh_token: RT, client_id: 'custom-input-client' });
  await online.refreshBatch(records, async payload => { assert.equal(payload.client_id, 'custom-input-client'); return freshTokens; });
  await online.refreshBatch(records, async payload => { assert.equal(payload.client_id, 'override-client'); return freshTokens; }, { clientId: 'override-client' });
});
test('partial failures and accounts without RT preserve input instead of creating fabricated credentials', async () => {
  const records = parse([{ type: 'codex', refresh_token: RT }, { type: 'codex', refresh_token: 'rt_DEMO_ONLY_bad_token_002' }, { type: 'codex', access_token: access }]);
  const result = await online.refreshBatch(records, async payload => { if (payload.refresh_token !== RT) throw new Error('RT 已失效'); return freshTokens; });
  assert.equal(result.succeeded, 1);
  assert.equal(result.failed, 1);
  assert.equal(result.noRT, 1);
  assert.deepEqual(result.records[1], records[1]);
  assert.deepEqual(result.records[2], records[2]);
  assert.equal(result.issues.length, 2);
});
test('fresh Sub2 credentials replace old token lifetime while business expiry and scheduling remain', async () => {
  const source = { platform: 'openai', type: 'oauth', name: 'demo', concurrency: 30, priority: 7, expires_at: 2000000000, credentials: { refresh_token: RT, access_token: 'old-access-token', expires_at: 1800000000, expires_in: 10, _token_version: 123 } };
  const result = await online.refreshBatch(parse(source), async () => freshTokens);
  const account = JSON.parse(converter.exportRecords(result.records, 'sub2').preview).accounts[0];
  assert.equal(account.credentials.expires_at, '2030-01-01T00:00:00.000Z');
  assert.equal(account.credentials.refresh_token, NEW_RT);
  assert.equal(account.credentials.access_token, access);
  assert.equal(account.credentials.expires_in, undefined);
  assert.equal(account.expires_at, 2000000000);
  assert.equal(account.concurrency, 30);
  assert.equal(account.priority, 7);
});
test('proxy options are sent and malformed refresh responses retain original credentials', async () => {
  const records = parse(RT);
  const result = await online.refreshBatch(records, async payload => { assert.equal(payload.network_mode, 'proxy'); assert.equal(payload.proxy_url, 'http://127.0.0.1:7890'); return {}; }, { networkMode: 'proxy', proxyUrl: 'http://127.0.0.1:7890' });
  assert.equal(result.failed, 1);
  assert.deepEqual(result.records[0], records[0]);
});
test('stopping further work lets in-flight refresh finish and preserves unattempted accounts', async () => {
  const records = parse(Array.from({ length: 5 }, (_, i) => `rt_DEMO_ONLY_not_a_real_token_00${i}`));
  let stop = false;
  const result = await online.refreshBatch(records, async () => { stop = true; await new Promise(resolve => setTimeout(resolve, 5)); return freshTokens; }, { shouldStop: () => stop });
  assert.equal(result.succeeded, 1);
  assert.equal(result.unattempted, 4);
  assert.equal(result.requests, 1);
  assert.deepEqual(result.records[4], records[4]);
});

test('default workflow reuses this session fresh tokens, while expired tokens and changed clients refresh', async () => {
  const initial = await online.refreshBatch(parse(RT), async () => freshTokens);
  const reused = await online.refreshBatch(initial.records, async () => { throw new Error('must not rotate again'); }, { skipFresh: true });
  assert.equal(reused.reused, 1);
  assert.equal(reused.requests, 0);
  let calls = 0;
  const expired = initial.records.map(record => ({ ...record, expiresAt: '2020-01-01T00:00:00Z' }));
  await online.refreshBatch(expired, async () => { calls++; return freshTokens; }, { skipFresh: true });
  await online.refreshBatch(initial.records, async () => { calls++; return freshTokens; }, { skipFresh: true, clientId: 'changed-client' });
  assert.equal(calls, 2);
});

test('RT refresh fills alias claims and favors the current access token over a retained ID token', async () => {
  const oldId = jwt({ email: 'old@example.com', user_id: 'old-user', 'https://api.openai.com/auth': {
    account_id: 'old-account', organization_id: 'old-org', chatgpt_plan_type: 'free'
  } });
  const currentAccess = jwt({ exp: 1893456000, user_id: 'current-user', 'https://api.openai.com/auth': {
    account_id: 'current-account', chatgpt_org_id: 'current-org', chatgpt_plan_type: 'team'
  }, 'https://api.openai.com/profile': { email: 'current@example.com' } });
  const records = parse({ type: 'codex', refresh_token: RT, id_token: oldId });
  const result = await online.refreshBatch(records, async () => ({ access_token: currentAccess, refresh_token: NEW_RT }));
  const fresh = result.records[0];
  assert.equal(fresh.idToken, oldId);
  assert.equal(fresh.email, 'current@example.com');
  assert.equal(fresh.accountId, 'current-account');
  assert.equal(fresh.userId, 'current-user');
  assert.equal(fresh.organizationId, 'current-org');
  assert.equal(fresh.planType, 'team');
  assert.equal(fresh.expiresAt, '2030-01-01T00:00:00.000Z');
  const sub2 = JSON.parse(converter.exportRecords(result.records, 'sub2').preview).accounts[0].credentials;
  assert.equal(sub2.chatgpt_account_id, 'current-account');
  assert.equal(sub2.organization_id, 'current-org');
  const rtOnly = await online.refreshBatch(parse(RT), async () => ({ access_token: currentAccess, refresh_token: NEW_RT }));
  assert.equal(JSON.parse(converter.exportRecords(rtOnly.records, 'cpa').preview).account_id, 'current-account');
  const chosen = await online.refreshBatch(parse({ type: 'codex', refresh_token: RT, account_id: 'chosen-account', id_token: oldId }), async () => ({ access_token: currentAccess, refresh_token: NEW_RT }));
  assert.equal(chosen.records[0].accountId, 'chosen-account');
});

test('refreshing auth.json updates its nested credentials and CPA/Sub2 round-trips keep the rotation', async () => {
  const records = parse({ last_refresh: '2020-01-01T00:00:00Z', expires_at: '2020-01-01T01:00:00Z', tokens: {
    access_token: 'DEMO_ONLY_old_access', refresh_token: RT, id_token: jwt({ email: 'old@example.com' }),
    account_id: 'chosen-account', chatgpt_account_id: 'chosen-account', expired: '2020-01-01T01:00:00Z',
    expires_at: '2020-01-01T01:00:00Z', expires_in: 3600, custom: 'preserved'
  } });
  const result = await online.refreshBatch(records, async () => freshTokens);
  const cpa = JSON.parse(converter.exportRecords(result.records, 'cpa').preview);
  assert.equal(cpa.tokens.access_token, access);
  assert.equal(cpa.tokens.refresh_token, NEW_RT);
  assert.equal(cpa.tokens.account_id, 'chosen-account');
  assert.equal(cpa.tokens.email, 'fresh@example.com');
  assert.equal(cpa.tokens.expired, '2030-01-01T00:00:00.000Z');
  assert.equal(cpa.tokens.expires_at, '2030-01-01T00:00:00.000Z');
  assert.equal(cpa.expires_at, '2030-01-01T00:00:00.000Z');
  assert.equal(cpa.tokens.expires_in, undefined);
  assert.equal(cpa.tokens.custom, 'preserved');
  let roundTrip = parse(cpa);
  const sub2 = converter.exportRecords(roundTrip, 'sub2').preview;
  roundTrip = parse(converter.exportRecords(parse(sub2), 'cpa').preview);
  assert.equal(roundTrip[0].accessToken, access);
  assert.equal(roundTrip[0].refreshToken, NEW_RT);
  assert.equal(roundTrip[0].email, 'fresh@example.com');
  assert.equal(roundTrip[0].accountId, 'chosen-account');
  assert.equal(roundTrip[0].expiresAt, '2030-01-01T00:00:00.000Z');
});

test('refresh without token expiry removes every old credential expiry while preserving business expiry', async () => {
  const source = { platform: 'openai', type: 'oauth', name: 'demo', expires_at: 2000000000,
    credentials: { access_token: 'DEMO_ONLY_old_access', refresh_token: RT, chatgpt_account_id: 'chosen-account',
      expired: '2020-01-01T00:00:00Z', expires_at: 1577836800, expires_in: 3600 },
    extra: { _gpt_converter: { version: 1, cpa: { type: 'codex', account_id: 'chosen-account',
      access_token: 'DEMO_ONLY_old_access', refresh_token: RT, expired: '2020-01-01T00:00:00Z',
      expires_at: '2020-01-01T00:00:00Z', expires_in: 3600, tokens: {
        account_id: 'chosen-account', access_token: 'DEMO_ONLY_old_access', refresh_token: RT,
        expired: '2020-01-01T00:00:00Z', expires_at: 1577836800, expires_in: 3600
      } } } } };
  const result = await online.refreshBatch(parse(source), async () => ({ access_token: 'DEMO_ONLY_new_opaque_access', refresh_token: NEW_RT }));
  assert.equal(result.records[0].expiresAt, '');
  const sub2 = JSON.parse(converter.exportRecords(result.records, 'sub2').preview);
  const account = sub2.accounts[0];
  assert.equal(account.expires_at, 2000000000);
  for (const key of ['expired', 'expires_at', 'expires_in']) assert.equal(account.credentials[key], undefined);
  const cpa = JSON.parse(converter.exportRecords(parse(sub2), 'cpa').preview);
  assert.equal(cpa.expired, '');
  assert.equal(cpa.expires_at, undefined);
  assert.equal(cpa.expires_in, undefined);
  for (const key of ['expired', 'expires_at', 'expires_in']) assert.equal(cpa.tokens[key], undefined);
  const reread = parse(cpa)[0];
  assert.equal(reread.expiresAt, '');
  assert.equal(reread.refreshToken, NEW_RT);
  const restoredSub2 = JSON.parse(converter.exportRecords(parse(cpa), 'sub2').preview).accounts[0];
  assert.equal(restoredSub2.expires_at, 2000000000);
  assert.equal(restoredSub2.credentials.refresh_token, NEW_RT);
  assert.equal(restoredSub2.credentials.expires_at, undefined);
});
