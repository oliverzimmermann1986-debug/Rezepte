import { pathToFileURL } from 'node:url';

export const RELEASE_CONTEXT = 'pruefdienst/release';

export async function requirePruefdienst({ repository, sha, token, fetcher = fetch }) {
  if (!/^[a-zA-Z0-9-]+\/[a-zA-Z0-9_.-]+$/.test(repository ?? '')) throw new Error('Invalid repository');
  if (!/^[a-f0-9]{40}$/.test(sha ?? '')) throw new Error('An exact commit SHA is required');
  if (!token?.trim()) throw new Error('GitHub token required to read Pruefdienst status');
  const response = await fetcher(`https://api.github.com/repos/${repository}/commits/${sha}/status?per_page=100`, {
    method: 'GET', redirect: 'error', signal: AbortSignal.timeout(20000),
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'rezepte-pruefdienst-gate' },
  });
  if (!response.ok) throw new Error(`Cannot read Pruefdienst status (HTTP ${response.status})`);
  const report = await response.json();
  if (report.sha !== sha || !Array.isArray(report.statuses)) throw new Error('Invalid commit status response');
  const statuses = report.statuses.filter(row => row.context === RELEASE_CONTEXT);
  if (statuses.length !== 1 || statuses[0].state !== 'success') {
    const state = statuses.length === 1 ? statuses[0].state : 'missing or ambiguous';
    throw new Error(`Pruefdienst is not green for ${sha}: ${state}. Wait for ${RELEASE_CONTEXT} on this exact commit.`);
  }
  return { sha, context: RELEASE_CONTEXT, state: 'success' };
}

async function main() {
  const result = await requirePruefdienst({ repository: process.env.GITHUB_REPOSITORY,
    sha: process.env.PRUEFDIENST_COMMIT_SHA || process.env.GITHUB_SHA,
    token: process.env.GH_TOKEN || process.env.GITHUB_TOKEN });
  console.log(`${result.context}: success (${result.sha})`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main().catch(error => {
  console.error(error instanceof Error ? error.message : 'Pruefdienst status check failed');
  process.exitCode = 1;
});
