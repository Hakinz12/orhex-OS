import assert from "node:assert/strict";
import test from "node:test";

import {
  completeWalletVerification,
  completeWalletConnectionForCurrentAttempt,
  completeWalletDisconnectForCurrentAttempt,
  runWalletVerificationAttempt,
} from "../src/lib/wallet/verification-flow.mjs";

const address = `0x${"a".repeat(64)}`;
const message = new Uint8Array([0, 1, 2, 255]);
const challenge = {
  challenge_id: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
  purpose: "wallet_link",
  chain: "sui",
  network: "mainnet",
  address,
  message: Buffer.from(message).toString("base64"),
  expires_at: new Date(Date.now() + 60_000).toISOString(),
};

function flow(overrides = {}) {
  return completeWalletVerification({
    address,
    network: "mainnet",
    correlationId: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
    isCurrentWallet: () => true,
    requestChallenge: async () => challenge,
    signPersonalMessage: async (bytes) => {
      assert.deepEqual([...bytes], [...message]);
      return { bytes: challenge.message, signature: Buffer.from("signature").toString("base64") };
    },
    submitVerification: async () => ({
      verified: true,
      chain: "sui",
      network: "mainnet",
      address,
    }),
    onPhase: () => {},
    ...overrides,
  });
}

test("atomic wallet flow signs and submits the backend challenge before reporting verified", async () => {
  const phases = [];
  const diagnostics = [];
  let submitted;
  const result = await flow({
    onPhase: (phase) => phases.push(phase),
    onDiagnostic: (event) => diagnostics.push(event),
    submitVerification: async (input) => {
      submitted = input;
      return { verified: true, chain: "sui", network: "mainnet", address };
    },
  });

  assert.deepEqual(phases, ["awaiting_approval", "verifying"]);
  assert.deepEqual(diagnostics, [{
    stage: "wallet_response",
    bytesPresent: true,
    signaturePresent: true,
    signedMessageMatches: true,
    correlationId: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
  }, {
    stage: "signed_message_matches",
    signed_message_matches: true,
    correlationId: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
  }, {
    stage: "verification_request_submitted",
    request_attempted: true,
    correlationId: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
  }, {
    stage: "verification_response_received",
    verified: true,
    correlationId: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
  }]);
  assert.deepEqual(submitted, {
    challenge_id: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
    signature: Buffer.from("signature").toString("base64"),
    signed_message: challenge.message,
    correlation_id: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
  });
  assert.equal(result.verified, true);
});

test("correlation ID is consistent across signing, verification submission, and response", async () => {
  const correlationId = "8f3a5bb8-a199-4d54-bd16-41a2f1845a93";
  const diagnostics = [];
  let submitted;
  const result = await flow({
    correlationId,
    signPersonalMessage: async (_message, onDiagnostic) => {
      onDiagnostic({ stage: "signing_started", signing_attempted: true });
      onDiagnostic({
        stage: "signing_resolved",
        bytes_present: true,
        signature_present: true,
      });
      return {
        bytes: challenge.message,
        signature: Buffer.from("signature").toString("base64"),
      };
    },
    onDiagnostic: (event) => diagnostics.push(event),
    submitVerification: async (input) => {
      submitted = input;
      return { verified: true, chain: "sui", network: "mainnet", address };
    },
  });

  assert.equal(result.verified, true);
  assert.equal(submitted.correlation_id, correlationId);
  const signingEvents = diagnostics.filter(({ stage }) =>
    stage === "signing_started" || stage === "signing_resolved");
  assert.deepEqual(signingEvents.map(({ stage }) => stage), [
    "signing_started",
    "signing_resolved",
  ]);
  assert.ok(diagnostics.some(({ stage }) => stage === "verification_request_submitted"));
  assert.ok(diagnostics.some(({ stage }) => stage === "verification_response_received"));
  assert.ok(diagnostics.every((event) => event.correlationId === correlationId));
});

test("flow reads getter-backed signature fields once and uses the snapshot for submission", async () => {
  const values = {
    bytes: challenge.message,
    signature: Buffer.from("serialized-signature").toString("base64"),
  };
  const reads = { bytes: 0, signature: 0 };
  let verifiedReads = 0;
  let submitted;
  const diagnostics = [];

  const result = await flow({
    signPersonalMessage: async () => ({
      get bytes() {
        reads.bytes += 1;
        return values.bytes;
      },
      get signature() {
        reads.signature += 1;
        return values.signature;
      },
    }),
    onDiagnostic: (event) => diagnostics.push(event),
    submitVerification: async (input) => {
      submitted = input;
      return {
        get verified() {
          verifiedReads += 1;
          return true;
        },
        chain: "sui",
        network: "mainnet",
        address,
      };
    },
  });

  assert.deepEqual(reads, { bytes: 1, signature: 1 });
  assert.equal(verifiedReads, 1);
  assert.equal(result.verified, true);
  assert.equal(submitted.signature, values.signature);
  assert.equal(submitted.signed_message, values.bytes);
  assert.equal(JSON.stringify(diagnostics).includes(values.signature), false);
  assert.equal(JSON.stringify(diagnostics).includes(values.bytes), false);
});

test("a failed correlation ID generation releases the lock for a later secure attempt", async () => {
  const inFlight = { current: false };
  const attemptCounter = { current: 0 };
  let generationCount = 0;
  let startedAttempts = 0;
  let completedAttempts = 0;
  let recoverableError;

  const runAttempt = () => runWalletVerificationAttempt({
    inFlight,
    attemptCounter,
    createCorrelationId: () => {
      generationCount += 1;
      if (generationCount === 1) throw new Error("secure ID generation failed");
      return crypto.randomUUID();
    },
    onStart: () => {
      startedAttempts += 1;
    },
    run: async ({ correlationId }) => {
      completedAttempts += 1;
      assert.match(correlationId, /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
      return flow({ correlationId });
    },
    onFailure: ({ correlationId, reason }) => {
      assert.equal(correlationId, null);
      assert.match(reason.message, /secure ID generation failed/);
      recoverableError = "Could not start a secure wallet connection. Please retry.";
      return new Error(recoverableError);
    },
  });

  await assert.rejects(runAttempt(), /Could not start a secure wallet connection/);
  assert.equal(inFlight.current, false);
  assert.equal(startedAttempts, 1);
  assert.equal(completedAttempts, 0);
  assert.equal(recoverableError, "Could not start a secure wallet connection. Please retry.");

  const result = await runAttempt();
  assert.equal(result.verified, true);
  assert.equal(inFlight.current, false);
  assert.equal(startedAttempts, 2);
  assert.equal(completedAttempts, 1);
});

test("a stale connect cannot clear a newer attempt's connection-starting state", async () => {
  const inFlight = { current: false };
  const attemptCounter = { current: 0 };
  const connectionStarting = { current: false };
  let resolveStaleConnect;
  let resolveCurrentConnect;
  let resolveStaleStarted;
  const staleStarted = new Promise((resolve) => {
    resolveStaleStarted = resolve;
  });

  const startAttempt = (connect) => runWalletVerificationAttempt({
    inFlight,
    attemptCounter,
    createCorrelationId: () => crypto.randomUUID(),
    onStart: () => {
      connectionStarting.current = true;
    },
    run: async ({ isCurrentAttempt }) => {
      await connect();
      const connected = completeWalletConnectionForCurrentAttempt({
        isCurrentAttempt,
        onConnected: () => {
          connectionStarting.current = false;
        },
      });
      return connected ? "connected" : "stale";
    },
  });

  const staleAttempt = startAttempt(() => {
    resolveStaleStarted();
    return new Promise((resolve) => {
      resolveStaleConnect = resolve;
    });
  });
  await staleStarted;
  assert.equal(connectionStarting.current, true);

  attemptCounter.current += 1;
  inFlight.current = false;
  const currentAttempt = startAttempt(() => new Promise((resolve) => {
    resolveCurrentConnect = resolve;
  }));
  assert.equal(connectionStarting.current, true);
  assert.equal(inFlight.current, true);

  resolveStaleConnect();
  assert.equal(await staleAttempt, "stale");
  assert.equal(connectionStarting.current, true);
  assert.equal(inFlight.current, true);

  resolveCurrentConnect();
  assert.equal(await currentAttempt, "connected");
  assert.equal(connectionStarting.current, false);
  assert.equal(inFlight.current, false);
});

test("a stale failure cannot overwrite state after its disconnect resolves", async () => {
  const attemptCounter = { current: 0 };
  const inFlight = { current: false };
  const intentionalDisconnect = { current: false };
  const activeWalletIdentity = { current: "old-wallet" };
  let resolveDisconnect;
  let disconnectStarted;
  const disconnectStartedPromise = new Promise((resolve) => {
    disconnectStarted = resolve;
  });
  const attemptError = new Error("verification failed");

  const staleAttempt = runWalletVerificationAttempt({
    inFlight,
    attemptCounter,
    createCorrelationId: () => crypto.randomUUID(),
    run: async () => {
      throw attemptError;
    },
    onFailure: async ({ isCurrentAttempt }) => {
      await completeWalletDisconnectForCurrentAttempt({
        isCurrentAttempt,
        disconnect: () => {
          disconnectStarted();
          return new Promise((resolve) => {
            resolveDisconnect = resolve;
          });
        },
        onDisconnectStarted: () => {
          intentionalDisconnect.current = true;
        },
        onDisconnected: () => {
          activeWalletIdentity.current = null;
        },
        onDisconnectFinished: () => {
          intentionalDisconnect.current = false;
        },
      });
    },
  });

  await disconnectStartedPromise;
  assert.equal(intentionalDisconnect.current, true);

  attemptCounter.current += 1;
  inFlight.current = false;
  activeWalletIdentity.current = "new-wallet";
  intentionalDisconnect.current = false;
  resolveDisconnect();

  await assert.rejects(staleAttempt, (error) => error === attemptError);
  assert.equal(activeWalletIdentity.current, "new-wallet");
  assert.equal(intentionalDisconnect.current, false);
  assert.equal(inFlight.current, false);
});

test("wallet account change or disconnection cancels before signature submission", async (t) => {
  for (const changePoint of ["before-signing", "after-signing"]) {
    await t.test(changePoint, async () => {
      let current = true;
      let submissions = 0;
      const diagnostics = [];
      const result = await flow({
        requestChallenge: async () => {
          if (changePoint === "before-signing") current = false;
          return challenge;
        },
        signPersonalMessage: async () => {
          current = false;
          return { bytes: challenge.message, signature: "signature" };
        },
        isCurrentWallet: () => current,
        submitVerification: async () => {
          submissions += 1;
          return { verified: true, chain: "sui", network: "mainnet", address };
        },
        onDiagnostic: (event) => diagnostics.push(event),
      });

      assert.equal(result, null);
      assert.equal(submissions, 0);
      assert.deepEqual(diagnostics, [{
        stage: "wallet_attempt_invalidated",
        after: changePoint === "before-signing" ? "challenge_response" : "signing_response",
        current_attempt: false,
        correlationId: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
      }]);
    });
  }
});

test("invalid challenge details are rejected before the wallet is asked to sign", async () => {
  let prompted = false;
  await assert.rejects(
    flow({
      requestChallenge: async () => ({ ...challenge, network: "testnet" }),
      signPersonalMessage: async () => {
        prompted = true;
        return { bytes: challenge.message, signature: "signature" };
      },
    }),
    /invalid challenge/,
  );
  assert.equal(prompted, false);
});

test("a future challenge expiry permits signing using the injected clock", async () => {
  const currentTime = Date.parse("2026-11-10T05:00:00.000Z");
  let prompted = false;
  const result = await flow({
    now: () => currentTime,
    requestChallenge: async () => ({
      ...challenge,
      expires_at: new Date(currentTime + 60_000).toISOString(),
    }),
    signPersonalMessage: async (bytes) => {
      prompted = true;
      assert.deepEqual([...bytes], [...message]);
      return {
        bytes: challenge.message,
        signature: Buffer.from("signature").toString("base64"),
      };
    },
  });

  assert.equal(prompted, true);
  assert.equal(result.verified, true);
});

test("an expired challenge is rejected before the wallet is asked to sign", async () => {
  const currentTime = Date.parse("2026-11-10T05:00:00.000Z");
  let prompted = false;
  await assert.rejects(
    flow({
      now: () => currentTime,
      requestChallenge: async () => ({
        ...challenge,
        expires_at: new Date(currentTime - 1).toISOString(),
      }),
      signPersonalMessage: async () => {
        prompted = true;
        return { bytes: challenge.message, signature: "signature" };
      },
    }),
    /challenge has expired/,
  );
  assert.equal(prompted, false);
});

test("missing and invalid challenge expiries are rejected before signing", async (t) => {
  const invalidExpiries = [
    ["invalid", "not-a-date"],
    ["missing", undefined],
  ];

  for (const [name, expiresAt] of invalidExpiries) {
    await t.test(name, async () => {
      let prompted = false;
      await assert.rejects(
        flow({
          now: () => Date.parse("2026-11-10T05:00:00.000Z"),
          requestChallenge: async () => {
            const response = { ...challenge };
            if (expiresAt === undefined) {
              delete response.expires_at;
            } else {
              response.expires_at = expiresAt;
            }
            return response;
          },
          signPersonalMessage: async () => {
            prompted = true;
            return { bytes: challenge.message, signature: "signature" };
          },
        }),
        /invalid challenge/,
      );
      assert.equal(prompted, false);
    });
  }
});

test("backend verification errors propagate and a later attempt can request a fresh challenge", async () => {
  let challengeRequests = 0;
  const diagnostics = [];
  const privateSignature = Buffer.from("private-signature-value").toString("base64");
  await assert.rejects(
    flow({
      requestChallenge: async () => {
        challengeRequests += 1;
        return challenge;
      },
      signPersonalMessage: async () => ({
        bytes: challenge.message,
        signature: privateSignature,
      }),
      submitVerification: async () => {
        throw Object.assign(new Error("The challenge expired with sensitive details."), {
          code: "wallet_challenge_expired",
          status: 409,
        });
      },
      onDiagnostic: (event) => diagnostics.push(event),
    }),
    (error) => error.code === "wallet_challenge_expired" && error.status === 409,
  );

  assert.deepEqual(diagnostics.slice(-2), [
    {
      stage: "verification_request_submitted",
      request_attempted: true,
      correlationId: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
    },
    {
      stage: "verification_request_failed",
      exception_class: "Error",
      status: 409,
      error_category: "wallet_challenge_expired",
      correlationId: "6f3a5bb8-a199-4d54-bd16-41a2f1845a91",
    },
  ]);
  const serializedDiagnostics = JSON.stringify(diagnostics);
  assert.equal(serializedDiagnostics.includes(privateSignature), false);
  assert.equal(serializedDiagnostics.includes(challenge.message), false);
  assert.equal(serializedDiagnostics.includes("sensitive details"), false);

  const result = await flow({
    requestChallenge: async () => {
      challengeRequests += 1;
      return { ...challenge, challenge_id: "7f3a5bb8-a199-4d54-bd16-41a2f1845a92" };
    },
  });
  assert.equal(challengeRequests, 2);
  assert.equal(result.verified, true);
});

test("malformed challenge messages are rejected without sending a signature", async () => {
  let submissions = 0;
  await assert.rejects(
    flow({
      requestChallenge: async () => ({ ...challenge, message: "not-base64" }),
      submitVerification: async () => {
        submissions += 1;
        return { verified: true, chain: "sui", network: "mainnet", address };
      },
    }),
    /invalid challenge/,
  );
  assert.equal(submissions, 0);
});

test("mismatched Wallet Standard response bytes are rejected before BFF submission", async () => {
  let submissions = 0;
  const diagnostics = [];
  await assert.rejects(
    flow({
      signPersonalMessage: async () => ({
        bytes: Buffer.from("different message").toString("base64"),
        signature: Buffer.from("signature").toString("base64"),
      }),
      submitVerification: async () => {
        submissions += 1;
        return { verified: true, chain: "sui", network: "mainnet", address };
      },
      onDiagnostic: (event) => diagnostics.push(event),
    }),
    /different message/,
  );
  assert.equal(submissions, 0);
  assert.equal(diagnostics.some(({ stage }) => stage === "verification_request_submitted"), false);
  assert.equal(diagnostics.some(({ stage, signed_message_matches }) =>
    stage === "signed_message_matches" && signed_message_matches === false), true);
  assert.equal(JSON.stringify(diagnostics).includes(challenge.message), false);
});

test("signing rejection and invalid response never attempt verification submission", async (t) => {
  for (const [name, signPersonalMessage, expectedReason] of [
    ["rejection", async () => { throw new Error("private wallet error text"); }, undefined],
    ["invalid response", async () => ({ bytes: challenge.message }), "fields_missing_or_invalid"],
  ]) {
    await t.test(name, async () => {
      let submissions = 0;
      const diagnostics = [];
      const attempt = flow({
        signPersonalMessage,
        submitVerification: async () => {
          submissions += 1;
          return { verified: true, chain: "sui", network: "mainnet", address };
        },
        onDiagnostic: (event) => diagnostics.push(event),
      });

      await assert.rejects(attempt);
      assert.equal(submissions, 0);
      assert.equal(
        diagnostics.some(({ stage }) => stage === "verification_request_submitted"),
        false,
      );
      if (expectedReason) {
        assert.equal(
          diagnostics.some(({ stage, reason }) =>
            stage === "signature_response_invalid" && reason === expectedReason),
          true,
        );
      }
      assert.equal(JSON.stringify(diagnostics).includes("private wallet error text"), false);
    });
  }
});

test("diagnostic callback failures do not change verification behavior", async () => {
  const result = await flow({
    onDiagnostic: () => {
      throw new Error("diagnostic sink failed");
    },
  });
  assert.equal(result.verified, true);
});
