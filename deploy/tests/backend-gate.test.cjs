const test = require('node:test');
const assert = require('node:assert/strict');
const { waitForBackend } = require('../../frontend/scripts/wait-for-backend.cjs');
test('waits through pending/wrong releases and accepts only the matching ready SHA', async () => {
  let calls = 0;
  const values = [{sha:'other',ready:true},{sha:'expected',ready:false},{sha:'expected',ready:true}];
  await waitForBackend({baseUrl:'https://example.invalid',sha:'expected',pause:async()=>{},
    fetcher:async()=>({ok:true,json:async()=>values[calls++]})});
  assert.equal(calls, 3);
});
test('backend failures stop frontend publication after the bounded wait', async () => {
  await assert.rejects(waitForBackend({baseUrl:'https://example.invalid',sha:'expected',timeoutMs:1,
    fetcher:async()=>{throw Error('unavailable')},pause:async()=>{await new Promise(r=>setTimeout(r,5))}}), /publication stopped/);
});
