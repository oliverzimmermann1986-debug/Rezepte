import { readFile } from "node:fs/promises";
import { sign } from "node:crypto";
import { pathToFileURL } from "node:url";

const API_ORIGIN = "https://api.appstoreconnect.apple.com";
const DEFAULT_TIMEOUT_SECONDS = 20 * 60;
const DEFAULT_POLL_SECONDS = 15;
export const EXTERNAL_GROUP_ID = "876c2be9-8c62-4708-a9f9-27c2caf77fb2";
// Existing internal distribution verified on build 11601; preserve its access.
export const EXISTING_INTERNAL_ALL_BUILDS_GROUP_ID = "5b41ef54-1e4f-4c3b-98e6-294d6db16233";

async function allPages(request, path) {
  const items = [];
  const seen = new Set();
  while (path) {
    const url = new URL(path, API_ORIGIN);
    if (url.origin !== API_ORIGIN || url.username || url.password || url.hash || !url.pathname.startsWith('/v1/') || seen.has(url.href)) throw new Error('Unsafe or repeated App Store Connect pagination link.');
    seen.add(url.href);
    const result = await request(url.pathname + url.search);
    if (!Array.isArray(result?.data)) throw new Error('Invalid App Store Connect collection response.');
    items.push(...result.data);
    path = result.links?.next || null;
  }
  return items;
}

function resourceBody(type, buildId) {
  return JSON.stringify({ data: { type, relationships: { build: { data: { type: 'builds', id: buildId } } } } });
}

function reviewStateOf(reviews) {
  if (reviews.length > 1) throw new Error('Ambiguous beta review submissions for the selected build.');
  const state = reviews[0]?.attributes?.betaReviewState || null;
  if (state && !['WAITING_FOR_REVIEW', 'IN_REVIEW', 'APPROVED'].includes(state)) throw new Error(`External testing blocked by beta review: ${state}.`);
  return state;
}

// Only this existing private group is authorized. No tester/group creation and
// no public-link or App Store publication endpoints belong to this operation.
export async function ensureExternalDistribution({ request, build, appId, whatToTest }) {
  if (build?.type !== 'builds' || !build.id || build.attributes?.processingState !== 'VALID' || build.attributes?.expired) throw new Error('External testing requires the exact unexpired VALID build.');
  if (build.attributes?.buildAudienceType !== 'APP_STORE_ELIGIBLE') throw new Error('Build is not eligible for external TestFlight testing.');
  const group = (await request(`/v1/betaGroups/${EXTERNAL_GROUP_ID}`))?.data;
  const groupApp = (await request(`/v1/betaGroups/${EXTERNAL_GROUP_ID}/app`))?.data;
  const buildApp = (await request(`/v1/builds/${encodeURIComponent(build.id)}/app`))?.data;
  if (group?.id !== EXTERNAL_GROUP_ID || group.type !== 'betaGroups' || group.attributes?.isInternalGroup !== false || group.attributes?.name !== 'Privater Test') throw new Error('Expected the existing external group Privater Test.');
  if (groupApp?.type !== 'apps' || groupApp.id !== appId || buildApp?.type !== 'apps' || buildApp.id !== appId) throw new Error('External group and build must belong to the requested app.');

  const buildPath = `/v1/builds/${encodeURIComponent(build.id)}`;
  let existingInternalGroups = [];
  const checkNotificationScope = async () => {
    // Read the app's actual groups and each build linkage. Apple's live API
    // rejected the documented betaGroups filter[builds] query with HTTP 400.
    const groups = await allPages(request, `/v1/apps/${encodeURIComponent(appId)}/betaGroups?limit=200`);
    const otherAssignments = [];
    const preserved = [];
    for (const candidate of groups) {
      if (candidate.type !== 'betaGroups' || !candidate.id) throw new Error('Invalid app beta group response.');
      if (candidate.id === EXTERNAL_GROUP_ID) continue;
      if (candidate.id === EXISTING_INTERNAL_ALL_BUILDS_GROUP_ID && candidate.attributes?.isInternalGroup === true && candidate.attributes?.hasAccessToAllBuilds === true) {
        preserved.push({ id: candidate.id, isInternalGroup: true, hasAccessToAllBuilds: true });
        continue;
      }
      const builds = await allPages(request, `/v1/betaGroups/${encodeURIComponent(candidate.id)}/relationships/builds?limit=200`);
      if (candidate.attributes?.hasAccessToAllBuilds === true || builds.some(item => item.id === build.id)) {
        otherAssignments.push({ id: candidate.id, isInternalGroup: candidate.attributes?.isInternalGroup ?? null, hasAccessToAllBuilds: candidate.attributes?.hasAccessToAllBuilds ?? null });
      }
    }
    const individuals = await allPages(request, `${buildPath}/individualTesters?limit=200`);
    if (otherAssignments.length || individuals.length) throw new Error(`Build notification scope exceeds Privater Test and the existing internal all-builds group: other groups=${JSON.stringify(otherAssignments)}; individual tester count=${individuals.length}.`);
    existingInternalGroups = preserved;
  };
  await checkNotificationScope();
  const testers = await allPages(request, `/v1/betaGroups/${EXTERNAL_GROUP_ID}/betaTesters?fields%5BbetaTesters%5D=state&limit=200`);
  if (!testers.length) throw new Error('Privater Test has no existing testers; no invitations will be created.');
  let detail = (await request(`${buildPath}/buildBetaDetail`))?.data;
  if (!detail?.id || detail.type !== 'buildBetaDetails') throw new Error('Missing build beta details.');
  const reviewsPath = `/v1/betaAppReviewSubmissions?filter%5Bbuild%5D=${encodeURIComponent(build.id)}&limit=200`;
  let reviews = await allPages(request, reviewsPath);
  let reviewState = reviewStateOf(reviews);
  const allowed = new Set(['READY_FOR_BETA_SUBMISSION', 'WAITING_FOR_BETA_REVIEW', 'IN_BETA_REVIEW', 'BETA_APPROVED', 'READY_FOR_BETA_TESTING', 'IN_BETA_TESTING']);
  if (!allowed.has(detail.attributes?.externalBuildState) || reviewState === 'REJECTED') throw new Error(`External testing blocked: ${detail.attributes?.externalBuildState || 'UNKNOWN'}; review: ${reviewState || 'NONE'}.`);

  // Inspect existing review data without logging or replacing personal details.
  const localizations = await allPages(request, `/v1/apps/${appId}/betaAppLocalizations?limit=200`);
  const reviewDetail = (await request(`/v1/apps/${appId}/betaAppReviewDetail`))?.data?.attributes;
  const missing = [];
  if (!localizations.length || localizations.some(item => !item.attributes?.description?.trim())) missing.push('betaAppLocalizations.description');
  if (!localizations.some(item => item.attributes?.feedbackEmail?.trim())) missing.push('betaAppLocalizations.feedbackEmail');
  for (const name of ['contactFirstName', 'contactLastName', 'contactEmail', 'contactPhone']) if (!reviewDetail?.[name]?.trim()) missing.push(`betaAppReviewDetails.${name}`);
  if (reviewDetail?.demoAccountRequired === true) for (const name of ['demoAccountName', 'demoAccountPassword']) if (!reviewDetail[name]?.trim()) missing.push(`betaAppReviewDetails.${name}`);
  if (typeof reviewDetail?.demoAccountRequired !== 'boolean') missing.push('betaAppReviewDetails.demoAccountRequired');
  if (!whatToTest?.trim() || whatToTest.length > 4000) missing.push('build.whatToTest');
  if (missing.length) throw new Error(`Missing beta review metadata: ${missing.join(', ')}`);

  const notes = await allPages(request, `${buildPath}/betaBuildLocalizations?limit=200`);
  const german = notes.find(item => item.attributes?.locale === 'de-DE');
  const relationship = `/v1/betaGroups/${EXTERNAL_GROUP_ID}/relationships/builds`;
  let assigned = (await allPages(request, `${relationship}?limit=200`)).some(item => item.id === build.id);
  if (german?.attributes?.whatsNew !== whatToTest.trim()) {
    // ASC names the localized What to Test property whatsNew.
    await request(german ? `/v1/betaBuildLocalizations/${encodeURIComponent(german.id)}` : '/v1/betaBuildLocalizations', {
      method: german ? 'PATCH' : 'POST',
      body: JSON.stringify({ data: { type: 'betaBuildLocalizations', ...(german ? { id: german.id } : {}), attributes: { whatsNew: whatToTest.trim(), ...(german ? {} : { locale: 'de-DE' }) }, ...(german ? {} : { relationships: { build: { data: { type: 'builds', id: build.id } } } }) } }),
    });
  }
  if (!assigned) {
    await request(relationship, { method: 'POST', body: JSON.stringify({ data: [{ type: 'builds', id: build.id }] }) });
    assigned = (await allPages(request, `${relationship}?limit=200`)).some(item => item.id === build.id);
  }
  if (!assigned) throw new Error('External group assignment was not confirmed by Apple.');
  await checkNotificationScope();
  if (detail.attributes?.autoNotifyEnabled !== true) {
    await request(`/v1/buildBetaDetails/${encodeURIComponent(detail.id)}`, { method: 'PATCH', body: JSON.stringify({ data: { type: 'buildBetaDetails', id: detail.id, attributes: { autoNotifyEnabled: true } } }) });
    const confirmation = (await request(`${buildPath}/buildBetaDetail`))?.data;
    if (confirmation?.attributes?.autoNotifyEnabled !== true) throw new Error('Apple did not confirm automatic notification for the private test build.');
  }
  let submitted = false;
  if (detail.attributes?.externalBuildState === 'READY_FOR_BETA_SUBMISSION' && !reviewState) {
    try {
      await request('/v1/betaAppReviewSubmissions', { method: 'POST', body: resourceBody('betaAppReviewSubmissions', build.id) });
      submitted = true;
    } catch (error) {
      // Another authorized run may have submitted the same build concurrently.
      if (error.status !== 409) throw error;
      reviews = await allPages(request, reviewsPath);
      if (!reviewStateOf(reviews)) throw error;
    }
  }
  detail = (await request(`${buildPath}/buildBetaDetail`))?.data;
  reviews = await allPages(request, reviewsPath);
  reviewState = reviewStateOf(reviews);
  let notificationSent = false;
  if (detail?.attributes?.externalBuildState === 'READY_FOR_BETA_TESTING') {
    await checkNotificationScope();
    await request('/v1/buildBetaNotifications', { method: 'POST', body: resourceBody('buildBetaNotifications', build.id) });
    notificationSent = true;
    detail = (await request(`${buildPath}/buildBetaDetail`))?.data;
  }
  const state = detail?.attributes?.externalBuildState || 'UNKNOWN';
  if (!allowed.has(state) || reviewState === 'REJECTED') throw new Error(`External testing blocked after submission: ${state}; review: ${reviewState || 'NONE'}.`);
  return { externalGroupAssigned: true, externalGroupId: EXTERNAL_GROUP_ID, externalGroupName: group.attributes.name,
    existingInternalAllBuildsGroups: existingInternalGroups,
    externalTesterCount: testers.length, externalBuildState: state, betaReviewState: reviewState,
    betaReviewSubmitted: submitted, externalAutoNotifyEnabled: detail?.attributes?.autoNotifyEnabled === true,
    externalNotificationSent: notificationSent, externalTestingAvailable: state === 'IN_BETA_TESTING',
    externalStatus: state === 'IN_BETA_TESTING' ? 'TESTING' : ['WAITING_FOR_BETA_REVIEW', 'IN_BETA_REVIEW'].includes(state) || ['WAITING_FOR_REVIEW', 'IN_REVIEW'].includes(reviewState) || submitted ? 'REVIEW_PENDING' : 'NOT_YET_TESTING' };
}

function required(name) {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function positiveInteger(name, fallback) {
  const raw = process.env[name]?.trim();
  if (!raw) return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`${name} must be a positive integer.`);
  }
  return value;
}

function booleanEnvironment(name, fallback = false) {
  const raw = process.env[name]?.trim().toLowerCase();
  if (!raw) return fallback;
  if (raw === "true" || raw === "1") return true;
  if (raw === "false" || raw === "0") return false;
  throw new Error(`${name} must be true or false.`);
}

function base64url(value) {
  return Buffer.from(value).toString("base64url");
}

function createToken({ issuerId, keyId, privateKey }) {
  const now = Math.floor(Date.now() / 1000);
  const header = base64url(JSON.stringify({ alg: "ES256", kid: keyId, typ: "JWT" }));
  const payload = base64url(
    JSON.stringify({
      iss: issuerId,
      iat: now - 5,
      exp: now + 10 * 60,
      aud: "appstoreconnect-v1",
    }),
  );
  const unsigned = `${header}.${payload}`;
  const signature = sign("sha256", Buffer.from(unsigned), {
    key: privateKey,
    dsaEncoding: "ieee-p1363",
  }).toString("base64url");
  return `${unsigned}.${signature}`;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export function compactApiError(payload, fallback) {
  if (!payload || !Array.isArray(payload.errors)) return fallback;
  // Apple's free-form title/detail may echo review contact or demo values.
  // Keep HTTP context and machine error codes only; never log payload fields.
  const codes = payload.errors.map(error => error.code)
    .filter(code => typeof code === 'string' && /^[A-Z][A-Z0-9_.-]{0,99}$/.test(code));
  const parameters = payload.errors.map(error => error.source?.parameter)
    .filter(parameter => typeof parameter === 'string' && /^[A-Za-z][A-Za-z0-9_.\[\]-]{0,99}$/.test(parameter));
  return (codes.length ? `${fallback} (${[...new Set(codes)].join(', ')})` : fallback)
    + (parameters.length ? `; parameters: ${[...new Set(parameters)].join(', ')}` : '');
}

export function selectExactBuild(payload, { buildNumber, marketingVersion, uploadStartedAt }) {
  const versions = new Map((payload?.included ?? [])
    .filter(item => item.type === 'preReleaseVersions').map(item => [item.id, item.attributes]));
  const matches = (payload?.data ?? []).filter(candidate => {
    const version = versions.get(candidate.relationships?.preReleaseVersion?.data?.id);
    if (candidate.type !== 'builds' || !candidate.id || candidate.attributes?.version !== buildNumber || version?.version !== marketingVersion || version.platform !== 'IOS') return false;
    if (uploadStartedAt === null) return true;
    const uploadedAt = Date.parse(candidate.attributes?.uploadedDate ?? '');
    return Number.isFinite(uploadedAt) && uploadedAt >= uploadStartedAt;
  });
  if (matches.length > 1) throw new Error('Ambiguous exact TestFlight build selection; no changes made.');
  return matches[0] ?? null;
}

async function main() {
  const issuerId = required("ASC_API_ISSUER_ID");
  const keyId = required("ASC_API_KEY_ID");
  const keyPath = required("ASC_API_KEY_PATH");
  const appId = required("ASC_APP_ID");
  const buildNumber = required("ASC_BUILD_NUMBER");
  const marketingVersion = required("ASC_MARKETING_VERSION");
  const assignInternalGroup = booleanEnvironment("ASC_ASSIGN_INTERNAL_GROUP");
  const assignExternalGroup = booleanEnvironment("ASC_ASSIGN_EXTERNAL_GROUP");
  if (assignExternalGroup && assignInternalGroup) throw new Error('Choose either internal or external group assignment.');
  const allowExistingBuild = booleanEnvironment("ASC_ALLOW_EXISTING_BUILD");
  const uploadStartedAtRaw = process.env.ASC_UPLOAD_STARTED_AT?.trim();
  if (!allowExistingBuild && !uploadStartedAtRaw) {
    throw new Error("Missing required environment variable: ASC_UPLOAD_STARTED_AT");
  }
  const uploadStartedAt = uploadStartedAtRaw ? Date.parse(uploadStartedAtRaw) : null;
  if (uploadStartedAtRaw && !Number.isFinite(uploadStartedAt)) {
    throw new Error("ASC_UPLOAD_STARTED_AT must be an ISO-8601 timestamp.");
  }
  const groupId = assignInternalGroup ? required("ASC_BETA_GROUP_ID") : null;
  const timeoutSeconds = positiveInteger("ASC_PROCESSING_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS);
  const pollSeconds = positiveInteger("ASC_PROCESSING_POLL_SECONDS", DEFAULT_POLL_SECONDS);
  const privateKey = await readFile(keyPath, "utf8");

  async function request(path, options = {}) {
    const response = await fetch(`${API_ORIGIN}${path}`, {
      signal: AbortSignal.timeout(30_000),
      ...options,
      headers: {
        Authorization: `Bearer ${createToken({ issuerId, keyId, privateKey })}`,
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = null;
      }
    }
    if (!response.ok) {
      const fallback = `${options.method ?? "GET"} ${path} returned HTTP ${response.status}`;
      const error = new Error(compactApiError(payload, fallback));
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  const deadline = Date.now() + timeoutSeconds * 1000;
  let build = null;
  let lastState = "NOT_FOUND";
  while (Date.now() < deadline) {
    const query = new URLSearchParams({
      "filter[app]": appId,
      "filter[version]": buildNumber,
      "filter[preReleaseVersion.platform]": "IOS",
      "filter[preReleaseVersion.version]": marketingVersion,
      "fields[builds]": "version,uploadedDate,processingState,expired,preReleaseVersion,buildAudienceType",
      "fields[preReleaseVersions]": "version,platform",
      include: "preReleaseVersion",
      limit: "20",
      sort: "-uploadedDate",
    });
    const buildsPayload = await request(`/v1/builds?${query}`);
    build = selectExactBuild(buildsPayload, { buildNumber, marketingVersion, uploadStartedAt });
    lastState = build?.attributes?.processingState ?? "NOT_FOUND";
    console.log(`TestFlight ${marketingVersion} (${buildNumber}): ${lastState}`);

    if (lastState === "VALID") break;
    if (lastState === "FAILED" || lastState === "INVALID") {
      throw new Error(
        `Apple rejected TestFlight ${marketingVersion} (${buildNumber}) during processing (${lastState}).`,
      );
    }
    await sleep(pollSeconds * 1000);
  }

  if (!build || lastState !== "VALID") {
    throw new Error(
      `TestFlight ${marketingVersion} (${buildNumber}) did not become VALID within ${timeoutSeconds} seconds (last state: ${lastState}).`,
    );
  }
  if (build.attributes?.expired) {
    throw new Error(`TestFlight build ${buildNumber} is already expired.`);
  }

  const testerStates = {};
  let testerCount = null;
  let group = null;
  if (assignInternalGroup) {
    const groupPayload = await request(
      `/v1/betaGroups/${encodeURIComponent(groupId)}?fields%5BbetaGroups%5D=name%2CisInternalGroup%2ChasAccessToAllBuilds`,
    );
    group = groupPayload?.data;
    if (!group || group.type !== "betaGroups") {
      throw new Error(`TestFlight group ${groupId} was not found.`);
    }
    if (group.attributes?.isInternalGroup !== true) {
      throw new Error(`TestFlight group ${group.attributes?.name ?? groupId} is not an internal group.`);
    }

    if (group.attributes?.hasAccessToAllBuilds !== true) {
      const relationshipPath = `/v1/betaGroups/${encodeURIComponent(groupId)}/relationships/builds`;
      const relationshipPayload = await request(`${relationshipPath}?limit=200`);
      let assigned = relationshipPayload?.data?.some((item) => item.id === build.id) ?? false;
      if (!assigned) {
        await request(relationshipPath, {
          method: "POST",
          body: JSON.stringify({ data: [{ type: "builds", id: build.id }] }),
        });
        const verificationPayload = await request(`${relationshipPath}?limit=200`);
        assigned = verificationPayload?.data?.some((item) => item.id === build.id) ?? false;
      }
      if (!assigned) {
        throw new Error(`Build ${buildNumber} could not be assigned to TestFlight group ${group.attributes.name}.`);
      }
    }

    const testersQuery = new URLSearchParams({
      "fields[betaTesters]": "state",
      limit: "200",
    });
    const testersPayload = await request(
      `/v1/betaGroups/${encodeURIComponent(groupId)}/betaTesters?${testersQuery}`,
    );
    for (const tester of testersPayload?.data ?? []) {
      const state = tester.attributes?.state ?? "UNKNOWN";
      testerStates[state] = (testerStates[state] ?? 0) + 1;
    }
    testerCount = testersPayload?.data?.length ?? 0;
    if (testerCount === 0) {
      throw new Error(`Internal TestFlight group ${group.attributes.name} has no testers.`);
    }
  }

  const external = assignExternalGroup
    ? await ensureExternalDistribution({ request, build, appId, whatToTest: await readFile(new URL('./testflight-what-to-test-1.9.1.txt', import.meta.url), 'utf8') })
    : { externalGroupAssigned: false };
  console.log(
    JSON.stringify(
      {
        appId,
        buildId: build.id,
        marketingVersion,
        buildNumber: build.attributes.version,
        uploadedDate: build.attributes.uploadedDate,
        processingState: build.attributes.processingState,
        internalGroupAssigned: assignInternalGroup,
        groupId,
        groupName: group?.attributes?.name ?? null,
        groupHasAccessToAllBuilds: group?.attributes?.hasAccessToAllBuilds ?? null,
        testerCount,
        testerStates,
        ...external,
      },
      null,
      2,
    ),
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
