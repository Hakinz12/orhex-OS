import assert from "node:assert/strict";
import test from "node:test";

import { Ed25519Keypair } from "@mysten/sui/keypairs/ed25519";
import { getZkLoginSignature, toZkLoginPublicIdentifier } from "@mysten/sui/zklogin";

import { verifyRequest } from "./verify.mjs";

const MESSAGE = new TextEncoder().encode("ORHEX deterministic zkLogin verifier test");
const ISSUER = "accounts.google.com";
const ADDRESS_SEED = 123n;

// The synthetic BCS fixture tests SDK parsing and address handling only;
// proof verification is mocked.
async function createSyntheticZkLoginRequest(addressStyle = "standard") {
  const keypair = Ed25519Keypair.fromSecretKey(new Uint8Array(32).fill(7));
  const ephemeralSignature = await keypair.signPersonalMessage(MESSAGE);
  const claim = Buffer.from(`"iss":"${ISSUER}",`, "utf8").toString("base64url");
  const signature = getZkLoginSignature({
    inputs: {
      proofPoints: { a: [], b: [], c: [] },
      issBase64Details: { value: claim, indexMod4: 0 },
      headerBase64: "",
      addressSeed: ADDRESS_SEED.toString(),
    },
    maxEpoch: 1,
    userSignature: Buffer.from(ephemeralSignature.signature, "base64"),
  });
  const address = toZkLoginPublicIdentifier(ADDRESS_SEED, ISSUER, {
    legacyAddress: addressStyle === "legacy",
  }).toSuiAddress();

  return {
    message: Buffer.from(MESSAGE).toString("base64"),
    signature,
    address,
    network: "mainnet",
  };
}

function mockedClient(coreResult, calls = []) {
  return {
    core: {
      async verifyZkLoginSignature(options) {
        calls.push(options);
        if (coreResult instanceof Error) {
          throw coreResult;
        }
        return coreResult;
      },
    },
  };
}

for (const addressStyle of ["standard", "legacy"]) {
  test(`mocked zkLogin verification accepts ${addressStyle} addresses`, async () => {
    const request = await createSyntheticZkLoginRequest(addressStyle);
    const coreCalls = [];
    const clients = [];
    const response = await verifyRequest(request, ({ network, url }) => {
      clients.push({ network, url });
      return mockedClient({ success: true, errors: [] }, coreCalls);
    });

    assert.deepEqual(response, {
      ok: true,
      address: request.address,
      scheme: "ZkLogin",
    });
    assert.deepEqual(clients, [
      { network: "mainnet", url: "https://graphql.mainnet.sui.io/graphql" },
    ]);
    assert.equal(coreCalls.length, 1);
    assert.equal(coreCalls[0].bytes, Buffer.from(MESSAGE).toString("base64"));
    assert.equal(coreCalls[0].intentScope, "PersonalMessage");
    assert.equal(coreCalls[0].address, toZkLoginPublicIdentifier(ADDRESS_SEED, ISSUER, {
      legacyAddress: false,
    }).toSuiAddress());
    assert.ok(coreCalls[0].signature);
  });
}

test("zkLogin verification rejects a different expected address", async () => {
  const request = await createSyntheticZkLoginRequest();
  request.address = `0x${"1".repeat(64)}`;
  let coreCalled = false;
  const response = await verifyRequest(request, () => ({
    core: {
      async verifyZkLoginSignature() {
        coreCalled = true;
        return { success: true, errors: [] };
      },
    },
  }));

  assert.deepEqual(response, {
    ok: false,
    code: "derived_address_mismatch",
    scheme: "ZkLogin",
  });
  assert.equal(coreCalled, false);
});

test("zkLogin verification uses the trusted testnet client and rejects a proof it rejects", async () => {
  const request = await createSyntheticZkLoginRequest();
  request.network = "testnet";
  const clients = [];
  const response = await verifyRequest(request, (clientOptions) => {
    clients.push(clientOptions);
    return mockedClient({ success: false, errors: [{ message: "invalid proof" }] });
  });

  assert.deepEqual(clients, [
    { network: "testnet", url: "https://graphql.testnet.sui.io/graphql" },
  ]);
  assert.deepEqual(response, {
    ok: false,
    code: "signature_verification_failed",
    scheme: "ZkLogin",
  });
});

test("zkLogin client outages return only a sanitized service error", async () => {
  const request = await createSyntheticZkLoginRequest();
  const response = await verifyRequest(request, () =>
    mockedClient(new Error("sensitive exception and proof detail")),
  );

  assert.deepEqual(response, {
    ok: false,
    code: "verification_service_error",
    scheme: "ZkLogin",
  });
  assert.equal(JSON.stringify(response).includes("sensitive"), false);
});

test("malformed zkLogin signatures are rejected before client creation", async () => {
  const request = await createSyntheticZkLoginRequest();
  request.signature = Buffer.from([5, 0]).toString("base64");
  let clientCreated = false;
  const response = await verifyRequest(request, () => {
    clientCreated = true;
    return mockedClient({ success: true, errors: [] });
  });

  assert.deepEqual(response, {
    ok: false,
    code: "signature_encoding_invalid",
    scheme: "ZkLogin",
  });
  assert.equal(clientCreated, false);
});

test("unsupported networks fail closed before client creation", async () => {
  const request = await createSyntheticZkLoginRequest();
  request.network = "untrusted-network";
  let clientCreated = false;
  const response = await verifyRequest(request, () => {
    clientCreated = true;
    return mockedClient({ success: true, errors: [] });
  });

  assert.deepEqual(response, { ok: false, code: "network_unsupported" });
  assert.equal(clientCreated, false);
});

test("devnet verification is explicitly unsupported before client creation", async () => {
  const request = await createSyntheticZkLoginRequest();
  request.network = "devnet";
  let clientCreated = false;
  const response = await verifyRequest(request, () => {
    clientCreated = true;
    return mockedClient({ success: true, errors: [] });
  });

  assert.deepEqual(response, {
    ok: false,
    code: "network_unsupported",
  });
  assert.equal(clientCreated, false);
});

test("Ed25519 personal-message verification remains offline", async () => {
  const keypair = Ed25519Keypair.fromSecretKey(new Uint8Array(32).fill(9));
  const signed = await keypair.signPersonalMessage(MESSAGE);
  const request = {
    message: Buffer.from(MESSAGE).toString("base64"),
    signature: signed.signature,
    address: keypair.toSuiAddress(),
    network: "mainnet",
  };
  const response = await verifyRequest(request, () => {
    assert.fail("Non-zkLogin verification must not construct a Sui client.");
  });

  assert.deepEqual(response, {
    ok: true,
    address: keypair.toSuiAddress(),
    scheme: "ED25519",
  });
});
