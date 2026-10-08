// Vercel publishes dependent frontend code only after the same backend release is ready.
async function waitForBackend({ baseUrl, sha, timeoutMs = 20 * 60 * 1000, fetcher = fetch, pause = ms => new Promise(r => setTimeout(r, ms)) }) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    try {
      const response = await fetcher(baseUrl.replace(/\/$/, '') + '/api/deployment', { signal: AbortSignal.timeout(10000), cache: 'no-store' });
      const value = await response.json();
      if (response.ok && value.ready === true && value.sha === sha) return;
    } catch { /* Initial deployment, pending CI or a short maintenance window. */ }
    await pause(10000);
  }
  throw new Error('Matching backend release is not ready; frontend publication stopped');
}
module.exports = { waitForBackend };
if (require.main === module) {
  const branch = process.env.VERCEL_GIT_COMMIT_REF;
  if (process.env.VERCEL === '1' && ['main', 'codex/diagnosis-roadmap'].includes(branch)) {
    const baseUrl = process.env.BACKEND_INTERNAL_URL;
    const sha = process.env.VERCEL_GIT_COMMIT_SHA;
    if (!baseUrl || !/^[0-9a-f]{40}$/.test(sha || '')) {
      console.error('Explicit backend URL and Git SHA are required'); process.exit(1);
    }
    waitForBackend({ baseUrl, sha }).then(() => console.log('Matching backend release ready')).catch(e => { console.error(e.message); process.exitCode = 1; });
  }
}
