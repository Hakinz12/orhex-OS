import assert from "node:assert/strict";
import test from "node:test";

import { mapWalletVerificationError } from "../src/lib/wallet/verification-errors.mjs";

test("maps server verification categories to distinct safe client messages", () => {
  const categories = [
    ["wallet_challenge_invalid", 409, /did not match/i],
    ["wallet_challenge_expired", 409, /challenge expired/i],
    ["wallet_challenge_not_found", 409, /not found/i],
    ["wallet_challenge_consumed", 409, /already used/i],
    ["wallet_signed_message_mismatch", 409, /different from the issued challenge/i],
    ["wallet_address_mismatch", 409, /different Sui address/i],
    ["wallet_signature_invalid", 409, /signature could not be verified/i],
    ["wallet_signature_verification_failed", 409, /did not verify for the issued message/i],
    ["wallet_signature_scheme_unsupported", 409, /scheme is not supported/i],
    ["wallet_signature_encoding_invalid", 409, /invalid signature format/i],
    ["wallet_signature_missing", 409, /did not return a signature/i],
    ["wallet_derived_address_mismatch", 409, /different Sui account/i],
    ["wallet_verification_unavailable", 503, /service is unavailable/i],
    ["wallet_verification_internal_error", 500, /internal error/i],
  ];

  for (const [code, status, message] of categories) {
    const mapped = mapWalletVerificationError(code, status);
    assert.equal(mapped.error, code);
    assert.equal(mapped.status, status);
    assert.match(mapped.message, message);
    assert.doesNotMatch(mapped.message, /orv_|0x[0-9a-f]{20,}|[A-Za-z0-9+/]{80,}={0,2}/i);
  }
});

test("falls back safely when the backend provides an unknown wallet error", () => {
  const mapped = mapWalletVerificationError("unknown_backend_code", 409);
  assert.equal(mapped.error, "wallet_verification_failed");
  assert.match(mapped.message, /new connection attempt/i);
});
