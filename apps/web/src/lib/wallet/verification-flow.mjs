function canonicalWalletAddress(value) {
  if (typeof value !== "string" || !/^0x[0-9a-f]{1,64}$/i.test(value)) return null;
  return `0x${value.slice(2).toLowerCase().padStart(64, "0")}`;
}

function decodeChallengeMessage(value) {
  const validBase64 =
    typeof value === "string" &&
    value.length > 0 &&
    value.length % 4 === 0 &&
    /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value);
  if (!validBase64) {
    throw new Error("The verification service returned an invalid challenge. Connect again to retry.");
  }
  const decoded = atob(value);
  return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
}

function bytesMatchBase64(value, expected) {
  const validBase64 =
    typeof value === "string" &&
    value.length > 0 &&
    value.length % 4 === 0 &&
    /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value);
  if (!validBase64) return false;
  const decoded = atob(value);
  if (decoded.length !== expected.length) return false;
  for (let index = 0; index < expected.length; index += 1) {
    if (decoded.charCodeAt(index) !== expected[index]) return false;
  }
  return true;
}

export async function runWalletVerificationAttempt({
  inFlight,
  attemptCounter,
  createCorrelationId,
  onStart,
  run,
  onFailure,
  onFinally,
}) {
  if (inFlight.current) return;
  inFlight.current = true;
  const attempt = ++attemptCounter.current;
  let correlationId = null;
  const isCurrentAttempt = () => attemptCounter.current === attempt;

  try {
    onStart?.(attempt);
    correlationId = createCorrelationId();
    return await run({ attempt, correlationId, isCurrentAttempt });
  } catch (reason) {
    const replacementError = await onFailure?.({
      attempt,
      correlationId,
      reason,
      isCurrentAttempt,
    });
    throw replacementError instanceof Error ? replacementError : reason;
  } finally {
    if (isCurrentAttempt()) {
      inFlight.current = false;
      onFinally?.(attempt);
    }
  }
}

export function completeWalletConnectionForCurrentAttempt({
  isCurrentAttempt,
  onConnected,
}) {
  if (!isCurrentAttempt()) return false;
  onConnected();
  return true;
}

export async function completeWalletDisconnectForCurrentAttempt({
  isCurrentAttempt,
  disconnect,
  onDisconnectStarted,
  onDisconnected,
  onDisconnectFinished,
}) {
  if (!isCurrentAttempt()) return false;
  onDisconnectStarted();
  try {
    await disconnect();
    if (!isCurrentAttempt()) return false;
    onDisconnected();
    return true;
  } finally {
    if (isCurrentAttempt()) onDisconnectFinished();
  }
}

const SAFE_WALLET_ERROR_CODES = new Set([
  "wallet_challenge_invalid",
  "wallet_challenge_not_found",
  "wallet_challenge_expired",
  "wallet_challenge_consumed",
  "wallet_signed_message_mismatch",
  "wallet_address_mismatch",
  "wallet_signature_invalid",
  "wallet_signature_verification_failed",
  "wallet_signature_scheme_unsupported",
  "wallet_signature_encoding_invalid",
  "wallet_signature_missing",
  "wallet_derived_address_mismatch",
  "wallet_already_linked",
  "wallet_verification_unavailable",
  "wallet_verification_internal_error",
  "wallet_verification_failed",
  "invalid_request",
  "rate_limited",
  "wallet_origin_rejected",
  "wallet_service_unavailable",
]);

function reportDiagnostic(onDiagnostic, event) {
  try {
    onDiagnostic?.(event);
  } catch {
    // Diagnostics must not change verification behavior.
  }
}

function safeErrorDetails(reason) {
  try {
    const exceptionClass =
      reason instanceof Error && /^[A-Za-z][A-Za-z0-9]{0,63}$/.test(reason.name)
        ? reason.name
        : "UnknownError";
    return {
      exception_class: exceptionClass,
      status: Number.isInteger(reason?.status) ? reason.status : null,
      error_category: SAFE_WALLET_ERROR_CODES.has(reason?.code)
        ? reason.code
        : "unclassified",
    };
  } catch {
    return {
      exception_class: "UnknownError",
      status: null,
      error_category: "unclassified",
    };
  }
}

export async function completeWalletVerification({
  address,
  network,
  isCurrentWallet,
  requestChallenge,
  signPersonalMessage,
  submitVerification,
  onPhase,
  onDiagnostic,
  correlationId,
  now = Date.now,
}) {
  const diagnose = (event) => {
    reportDiagnostic(onDiagnostic, { ...event, correlationId });
  };
  const challenge = await requestChallenge({ network, address, correlationId });
  if (!isCurrentWallet()) {
    diagnose({
      stage: "wallet_attempt_invalidated",
      after: "challenge_response",
      current_attempt: false,
    });
    return null;
  }
  const challengeExpiresAt =
    typeof challenge.expires_at === "string" ? Date.parse(challenge.expires_at) : NaN;
  if (
    challenge.purpose !== "wallet_link" ||
    typeof challenge.challenge_id !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(challenge.challenge_id) ||
    challenge.chain !== "sui" ||
    challenge.network !== network ||
    canonicalWalletAddress(challenge.address) !== canonicalWalletAddress(address) ||
    !Number.isFinite(challengeExpiresAt)
  ) {
    throw new Error("The verification service returned an invalid challenge. Connect again to retry.");
  }
  const currentTime = now();
  if (!Number.isFinite(currentTime)) {
    throw new Error("The verification service returned an invalid challenge. Connect again to retry.");
  }
  if (challengeExpiresAt <= currentTime) {
    throw new Error("The wallet challenge has expired. Connect again to retry.");
  }

  const message = decodeChallengeMessage(challenge.message);
  onPhase("awaiting_approval");
  const signed = await signPersonalMessage(message, (event) => {
    diagnose(event);
  });
  if (!isCurrentWallet()) {
    diagnose({
      stage: "wallet_attempt_invalidated",
      after: "signing_response",
      current_attempt: false,
    });
    return null;
  }
  if (!signed) {
    diagnose({
      stage: "signature_response_invalid",
      reason: "fields_missing_or_invalid",
      bytes_present: false,
      signature_present: false,
    });
    throw new Error("The wallet returned an incomplete personal-message signature.");
  }
  const signedSignature = signed.signature;
  if (typeof signedSignature !== "string") {
    diagnose({
      stage: "signature_response_invalid",
      reason: "fields_missing_or_invalid",
      signature_present: false,
    });
    throw new Error("The wallet returned an incomplete personal-message signature.");
  }
  const signedBytes = signed.bytes;
  if (typeof signedBytes !== "string") {
    diagnose({
      stage: "signature_response_invalid",
      reason: "fields_missing_or_invalid",
      bytes_present: false,
      signature_present: true,
    });
    throw new Error("The wallet returned an incomplete personal-message signature.");
  }
  const signedMessageMatches = bytesMatchBase64(signedBytes, message);
  diagnose({
    stage: "wallet_response",
    bytesPresent: true,
    signaturePresent: true,
    signedMessageMatches,
  });
  diagnose({
    stage: "signed_message_matches",
    signed_message_matches: signedMessageMatches,
  });
  if (!signedMessageMatches) {
    diagnose({
      stage: "signature_response_invalid",
      reason: "signed_message_mismatch",
      bytes_present: true,
      signature_present: true,
      signed_message_matches: false,
    });
    throw new Error("The wallet signed a different message than the verification challenge.");
  }

  onPhase("verifying");
  diagnose({
    stage: "verification_request_submitted",
    request_attempted: true,
  });
  let result;
  try {
    result = await submitVerification({
      challenge_id: challenge.challenge_id,
      signature: signedSignature,
      signed_message: signedBytes,
      correlation_id: correlationId,
    });
  } catch (reason) {
    diagnose({
      stage: "verification_request_failed",
      ...safeErrorDetails(reason),
    });
    throw reason;
  }
  const resultVerified = result.verified;
  diagnose({
    stage: "verification_response_received",
    verified: resultVerified === true,
  });
  if (!isCurrentWallet()) {
    diagnose({
      stage: "wallet_attempt_invalidated",
      after: "verification_response",
      current_attempt: false,
    });
    return null;
  }
  if (
    !resultVerified ||
    result.chain !== "sui" ||
    result.network !== network ||
    canonicalWalletAddress(result.address) !== canonicalWalletAddress(address)
  ) {
    throw new Error("The wallet signature did not verify for this account and network.");
  }
  return result;
}
