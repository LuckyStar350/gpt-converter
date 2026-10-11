const test = require('node:test');
const assert = require('node:assert/strict');
const converter = require('../converter.js');
const { createZip, readZip } = require('../archive.js');
const jwt = claims => `eyJhbGciOiJub25lIn0.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.DEMO_SIGNATURE`;
const RT = 'rt_DEMO_ONLY_not_a_real_token_001';
const access = jwt({ exp: 1893456000, client_id: 'demo-client', 'https://api.openai.com/auth': { chatgpt_account_id: 'demo-account', chatgpt_user_id: 'demo-user', chatgpt_plan_type: 'plus' }, 'https://api.openai.com/profile': { email: 'demo@example.com' } });
const cpa = { type: 'codex', refresh_token: RT, access_token: access, id_token: jwt({ email: 'demo@example.com' }), account_id: 'demo-account', email: 'demo@example.com', last_refresh: '2026-01-01T00:00:00Z', expired: '2030-01-01T00:00:00Z', plan_type: 'plus', disabled: true, proxy_url: 'socks5://127.0.0.1:1080', custom: { value: '自定义信息' } };
const sub2 = { type: 'sub2api-data', version: 1, exported_at: '2026-01-01T00:00:00Z', proxies: [{ proxy_key: 'demo-proxy', name: '本地代理', protocol: 'socks5', host: '127.0.0.1', port: 1080, status: 'active' }], accounts: [{ name: '演示账号', platform: 'openai', type: 'oauth', concurrency: 20, priority: 0, rate_multiplier: 1.5, notes: '保留备注', proxy_key: 'demo-proxy', expires_at: 2000000000, auto_pause_on_expired: false, credentials: { refresh_token: RT, access_token: access, id_token: cpa.id_token, chatgpt_account_id: 'demo-account', email: 'demo@example.com', expires_at: 1893456000, model_mapping: { 'demo-model': 'demo-model-2' } }, extra: { custom: { enabled: true } } }] };
const parse = value => converter.parseInput(typeof value === 'string' ? value : JSON.stringify(value));
const exportTo = (value, target, options) => converter.exportRecords(parse(value).records, target, options);

test('RT → Sub2API: produces official envelope and does not invent identity or expiry', () => {
  const payload = JSON.parse(exportTo(RT, 'sub2').preview);
  assert.equal(payload.type, 'sub2api-data');
  assert.equal(payload.version, 1);
  assert.equal(payload.accounts[0].platform, 'openai');
  assert.equal(payload.accounts[0].type, 'oauth');
  assert.deepEqual(payload.accounts[0].credentials, { refresh_token: RT });
  assert.equal(payload.accounts[0].concurrency, 10);
  assert.equal(payload.accounts[0].priority, 1);
  assert.equal(payload.accounts[0].expires_at, undefined);
});
test('RT → CPA: schema fields are present and unknown values remain empty', () => {
  const output = JSON.parse(exportTo(RT, 'cpa').preview);
  assert.equal(output.type, 'codex');
  assert.equal(output.refresh_token, RT);
  for (const key of ['access_token', 'id_token', 'account_id', 'email', 'expired', 'last_refresh']) assert.equal(output[key], '');
  assert.equal(output.plan_type, undefined);
});
test('Sub2API → RT: extracts refresh tokens', () => assert.equal(exportTo(sub2, 'rt').preview, RT + '\n'));
test('Sub2API → CPA: maps account ID and access-token expiry', () => {
  const output = JSON.parse(exportTo(sub2, 'cpa').preview);
  assert.equal(output.account_id, 'demo-account');
  assert.equal(output.expired, '2030-01-01T00:00:00.000Z');
  assert.equal(output.refresh_token, RT);
  assert.equal(output.access_token, access);
  assert.notEqual(output.expired, converter.isoTime(sub2.accounts[0].expires_at));
});
test('CPA → RT: extracts refresh tokens', () => assert.equal(exportTo(cpa, 'rt').preview, RT + '\n'));
test('CPA → Sub2API: maps credentials without inventing model mappings', () => {
  const output = JSON.parse(exportTo(cpa, 'sub2').preview).accounts[0];
  assert.equal(output.credentials.chatgpt_account_id, 'demo-account');
  assert.equal(output.credentials.plan_type, 'plus');
  assert.equal(output.credentials.client_id, 'demo-client');
  assert.equal(output.credentials.expires_at, '2030-01-01T00:00:00.000Z');
  assert.equal(output.credentials.model_mapping, undefined);
});
test('CPA → Sub2API → CPA preserves extensions and refresh time without nested capsules', () => {
  const once = exportTo(cpa, 'sub2').preview;
  const restored = JSON.parse(exportTo(once, 'cpa').preview);
  assert.deepEqual(restored.custom, cpa.custom);
  assert.equal(restored.disabled, true);
  assert.equal(restored.proxy_url, cpa.proxy_url);
  assert.equal(restored.last_refresh, cpa.last_refresh);
  assert.equal(restored.access_token, access);
  assert.equal(restored._gpt_converter.sub2.account.extra, undefined);
  const twice = JSON.parse(exportTo(restored, 'sub2').preview);
  assert.equal(twice.accounts[0].extra._gpt_converter.cpa._gpt_converter, undefined);
});
test('Sub2API → CPA → Sub2API preserves proxies, scheduling, account expiry and custom credentials', () => {
  const restored = JSON.parse(exportTo(exportTo(sub2, 'cpa').preview, 'sub2').preview);
  assert.deepEqual(restored.proxies, sub2.proxies);
  assert.equal(restored.accounts[0].concurrency, 20);
  assert.equal(restored.accounts[0].priority, 0);
  assert.equal(restored.accounts[0].rate_multiplier, 1.5);
  assert.equal(restored.accounts[0].notes, '保留备注');
  assert.equal(restored.accounts[0].expires_at, 2000000000);
  assert.equal(restored.accounts[0].auto_pause_on_expired, false);
  assert.equal(restored.accounts[0].proxy_key, 'demo-proxy');
  assert.equal(restored.accounts[0].credentials.expires_at, 1893456000);
  assert.deepEqual(restored.accounts[0].credentials.model_mapping, sub2.accounts[0].credentials.model_mapping);
  assert.deepEqual(restored.accounts[0].extra.custom, sub2.accounts[0].extra.custom);
});
test('turning off extension preservation produces plain CPA / Sub2API structures', () => {
  const output = JSON.parse(exportTo(sub2, 'cpa', { preserveExtra: false }).preview);
  assert.equal(output._gpt_converter, undefined);
  const restored = JSON.parse(exportTo(cpa, 'sub2', { preserveExtra: false }).preview);
  assert.equal(restored.accounts[0].extra, undefined);
  assert.equal(restored.accounts[0].credentials.refresh_token, RT);
});
test('automatic recognition accepts official API wrappers, legacy bundle, arrays, BOM and JSONL', () => {
  assert.equal(parse({ data: sub2 }).records.length, 1);
  assert.equal(parse({ ...sub2, type: 'sub2api-bundle' }).records.length, 1);
  assert.equal(parse([cpa, sub2]).records.length, 2);
  assert.equal(parse('\ufeff' + JSON.stringify(cpa)).records.length, 1);
  assert.equal(parse(JSON.stringify(cpa) + '\n' + JSON.stringify(cpa)).records.length, 2);
});
test('RT supports email separators, comments, BOM and opaque refresh tokens', () => {
  const result = parse(`\ufeff# demo\n// demo\nrefresh_token\ndemo@example.com----${RT}\ndemo2@example.com\t${RT}\nv1.demo_opaque_refresh_token_1234567890`);
  assert.equal(result.records.length, 3);
  assert.equal(result.records[0].email, 'demo@example.com');
  assert.equal(result.records[1].email, 'demo2@example.com');
  assert.equal(result.skipped, 0);
});
test('JSON errors are reported without falling back to RT or exposing token content', () => {
  const result = parse(`{"type":"codex","refresh_token":"${RT}",}`);
  assert.equal(result.records.length, 0);
  assert.equal(result.skipped, 1);
  assert.ok(!JSON.stringify(result.issues).includes(RT));
});
test('mixed batch keeps supported accounts and reports unsupported platform / API keys', () => {
  const result = parse([cpa, { ...sub2.accounts[0], platform: 'claude' }, { ...sub2.accounts[0], type: 'apikey' }, { type: 'gemini', access_token: access }]);
  assert.equal(result.records.length, 1);
  assert.equal(result.skipped, 3);
});
test('malformed token field types are rejected rather than silently removed', () => {
  const result = parse({ type: 'codex', refresh_token: { secret: RT }, access_token: access });
  assert.equal(result.records.length, 0);
  assert.equal(result.skipped, 1);
});
test('explicit source format rejects mismatched files', () => {
  const result = converter.parseInput(JSON.stringify(cpa), { sourceFormat: 'sub2' });
  assert.equal(result.records.length, 0);
  assert.equal(result.skipped, 1);
});
test('JWT claims provide metadata locally; explicit fields win and Unicode survives', () => {
  const result = parse({ type: 'codex', refresh_token: RT, access_token: access, id_token: jwt({ email: '演示@example.com' }), email: '演示@example.com', account_id: 'explicit-account' });
  assert.equal(result.records[0].email, '演示@example.com');
  assert.equal(result.records[0].accountId, 'explicit-account');
  assert.equal(result.records[0].expiresAt, '2030-01-01T00:00:00.000Z');
  assert.deepEqual(converter.jwtPayload('invalid-token'), {});
});
test('JWT account and organization aliases fill CPA and Sub2 identities', () => {
  for (const field of ['organization_id', 'chatgpt_organization_id', 'chatgpt_org_id', 'org_id']) {
    const token = jwt({ user_id: 'alias-user', 'https://api.openai.com/auth': { account_id: 'alias-account', [field]: 'alias-org' } });
    const records = parse({ type: 'codex', refresh_token: RT, access_token: token }).records;
    assert.equal(records[0].accountId, 'alias-account');
    assert.equal(records[0].accountIdExplicit, false);
    assert.equal(records[0].userId, 'alias-user');
    const output = JSON.parse(converter.exportRecords(records, 'sub2').preview).accounts[0].credentials;
    assert.equal(output.chatgpt_account_id, 'alias-account');
    assert.equal(output.organization_id, 'alias-org');
    const identityOnly = parse({ type: 'codex', id_token: jwt({ user_id: 'identity-user', 'https://api.openai.com/auth': { account_id: 'identity-account', [field]: 'identity-org' } }) }).records[0];
    assert.equal(identityOnly.accountId, 'identity-account');
    assert.equal(identityOnly.organizationId, 'identity-org');
    assert.equal(identityOnly.userId, 'identity-user');
  }
  const explicit = parse({ type: 'codex', access_token: access, user_id: 'explicit-user', chatgpt_org_id: 'explicit-org', account_id: 'explicit-account' }).records[0];
  assert.equal(explicit.userId, 'explicit-user');
  assert.equal(explicit.organizationId, 'explicit-org');
  assert.equal(explicit.accountId, 'explicit-account');
  assert.equal(explicit.accountIdExplicit, true);
});
test('missing RT is skipped only for RT export, and expired access tokens are preserved', () => {
  const result = parse([{ ...cpa, refresh_token: '' }, cpa]);
  const output = converter.exportRecords(result.records, 'rt');
  assert.equal(output.count, 1);
  assert.equal(output.preview, RT + '\n');
  assert.equal(output.warnings.length, 2);
  assert.equal(converter.exportRecords(result.records, 'cpa').count, 2);
});
test('deduplication keeps richer credentials but keeps distinct account identities', () => {
  const result = converter.mergeResults([parse({ type: 'codex', refresh_token: RT, account_id: 'demo-account' }), parse(cpa), parse({ ...cpa, account_id: 'another-account' })]);
  assert.equal(result.duplicates, 1);
  assert.equal(result.records.length, 2);
  assert.equal(result.records[0].accessToken, access);
  assert.equal(converter.mergeResults([parse(cpa), parse(cpa)], false).records.length, 2);
});
test('RT deduplication can be disabled', () => {
  const result = parse([cpa, cpa]);
  assert.equal(converter.exportRecords(result.records, 'rt').count, 1);
  assert.equal(converter.exportRecords(result.records, 'rt', { deduplicate: false }).count, 2);
});
test('CPA filenames are safe and unique even on case-insensitive filesystems', () => {
  const output = exportTo([{ ...cpa, email: 'A@example.com' }, { ...cpa, email: 'a@example.com' }, { ...cpa, email: '../:bad*?' }], 'cpa');
  assert.equal(output.files[1].name, 'codex-a@example.com-2.json');
  assert.ok(output.files.every(file => !/[<>:"/\\|?*]/.test(file.name)));
});
test('CPA filename suffixes do not collide with account names that already contain suffixes', () => {
  const records = parse([cpa, cpa, { ...cpa, email: 'demo@example.com-2' }]).records;
  const output = converter.exportRecords(records, 'cpa');
  assert.equal(new Set(output.files.map(file => file.name.toLowerCase())).size, 3);
});
test('expiry supports seconds, milliseconds and offsets without fabricating missing times', () => {
  assert.equal(converter.isoTime(1893456000), '2030-01-01T00:00:00.000Z');
  assert.equal(converter.isoTime(1893456000000), '2030-01-01T00:00:00.000Z');
  assert.equal(converter.isoTime('2030-01-01T08:00:00+08:00'), '2030-01-01T00:00:00.000Z');
  assert.equal(converter.isoTime('not-a-date'), '');
  assert.equal(converter.isoTime(''), '');
  const output = JSON.parse(exportTo({ ...cpa, expired: 'not-a-date' }, 'cpa').preview);
  assert.equal(output.expired, 'not-a-date');
});
test('Sub2API dangling proxies are reported and removed instead of exporting broken references', () => {
  const output = exportTo({ ...sub2, proxies: [] }, 'sub2');
  assert.equal(JSON.parse(output.preview).accounts[0].proxy_key, undefined);
  assert.equal(output.warnings.length, 1);
});
test('preview redacts nested tokens and proxy passwords; downloadable output remains intact', () => {
  const output = exportTo({ ...cpa, custom: { password: 'secret-password' } }, 'sub2');
  const masked = converter.maskPreview(output.preview, 'sub2');
  assert.ok(!masked.includes(RT));
  assert.ok(!masked.includes(access));
  assert.ok(!masked.includes('secret-password'));
  assert.ok(output.files[0].content.includes(RT));
});
test('empty input returns no files; deeply nested and oversized batches fail safely', () => {
  assert.equal(parse('').records.length, 0);
  assert.equal(converter.exportRecords([], 'cpa').files.length, 0);
  let value = cpa;
  for (let i = 0; i < 15; i++) value = { data: value };
  assert.equal(parse(value).skipped, 1);
  assert.throws(() => parse(Array(5001).fill(cpa)), /5000/);
});
test('standard ZIP round-trip preserves filenames, Unicode and JSON tokens', async () => {
  const files = [{ name: '账号一.json', content: JSON.stringify(cpa) }, { name: 'nested/rt.txt', content: RT }, { name: 'readme.md', content: 'ignored' }];
  const restored = await readZip(createZip(files));
  assert.deepEqual(restored, files.slice(0, 2));
});
test('ZIP checksum corruption and truncated ZIPs are rejected', async () => {
  const bytes = createZip([{ name: 'demo.json', content: JSON.stringify(cpa) }]);
  bytes[30 + 'demo.json'.length] ^= 1;
  await assert.rejects(readZip(bytes), /校验失败/);
  await assert.rejects(readZip(new Uint8Array([1, 2, 3])), /无法识别/);
});
