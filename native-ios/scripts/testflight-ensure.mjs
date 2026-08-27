import { readFile } from "node:fs/promises";
import { sign } from "node:crypto";

const API_ORIGIN = "https://api.appstoreconnect.apple.com";
const DEFAULT_TIMEOUT_SECONDS = 20 * 60;
const DEFAULT_POLL_SECONDS = 15;

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

function compactApiError(payload, fallback) {
  if (!payload || !Array.isArray(payload.errors)) return fallback;
  return payload.errors
    .map((error) => [error.status, error.code, error.title, error.detail].filter(Boolean).join(" "))
    .join(" | ");
}

async function main() {
  const issuerId = required("ASC_API_ISSUER_ID");
  const keyId = required("ASC_API_KEY_ID");
  const keyPath = required("ASC_API_KEY_PATH");
  const appId = required("ASC_APP_ID");
  const buildNumber = required("ASC_BUILD_NUMBER");
  const assignInternalGroup = booleanEnvironment("ASC_ASSIGN_INTERNAL_GROUP");
  const groupId = assignInternalGroup ? required("ASC_BETA_GROUP_ID") : null;
  const timeoutSeconds = positiveInteger("ASC_PROCESSING_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS);
  const pollSeconds = positiveInteger("ASC_PROCESSING_POLL_SECONDS", DEFAULT_POLL_SECONDS);
  const privateKey = await readFile(keyPath, "utf8");

  async function request(path, options = {}) {
    const response = await fetch(`${API_ORIGIN}${path}`, {
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
      throw new Error(compactApiError(payload, fallback));
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
      "fields[builds]": "version,uploadedDate,processingState,expired",
      limit: "20",
      sort: "-uploadedDate",
    });
    const buildsPayload = await request(`/v1/builds?${query}`);
    build = buildsPayload?.data?.[0] ?? null;
    lastState = build?.attributes?.processingState ?? "NOT_FOUND";
    console.log(`TestFlight build ${buildNumber}: ${lastState}`);

    if (lastState === "VALID") break;
    if (lastState === "FAILED" || lastState === "INVALID") {
      throw new Error(`Apple rejected TestFlight build ${buildNumber} during processing (${lastState}).`);
    }
    await sleep(pollSeconds * 1000);
  }

  if (!build || lastState !== "VALID") {
    throw new Error(
      `TestFlight build ${buildNumber} did not become VALID within ${timeoutSeconds} seconds (last state: ${lastState}).`,
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

  console.log(
    JSON.stringify(
      {
        appId,
        buildId: build.id,
        buildNumber: build.attributes.version,
        uploadedDate: build.attributes.uploadedDate,
        processingState: build.attributes.processingState,
        internalGroupAssigned: assignInternalGroup,
        groupId,
        groupName: group?.attributes?.name ?? null,
        groupHasAccessToAllBuilds: group?.attributes?.hasAccessToAllBuilds ?? null,
        testerCount,
        testerStates,
      },
      null,
      2,
    ),
  );
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
