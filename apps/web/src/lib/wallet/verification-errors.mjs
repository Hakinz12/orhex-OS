const categorizedErrors = {
  wallet_challenge_invalid: {
    status: 409,
    message: "The wallet challenge did not match this verification attempt. Start a new connection attempt.",
  },
  wallet_challenge_not_found: {
    status: 409,
    message: "The wallet challenge was not found. Start a new connection attempt.",
  },
  wallet_challenge_expired: {
    status: 409,
    message: "The wallet challenge expired. Start a new connection attempt.",
  },
  wallet_challenge_consumed: {
    status: 409,
    message: "The wallet challenge was already used. Start a new connection attempt.",
  },
  wallet_signed_message_mismatch: {
    status: 409,
    message: "The wallet signed a message different from the issued challenge. Start a new connection attempt.",
  },
  wallet_address_mismatch: {
    status: 409,
    message: "The signature belongs to a different Sui address. Connect the intended account and retry.",
  },
  wallet_signature_invalid: {
    status: 409,
    message: "The wallet signature could not be verified. Start a new connection attempt.",
  },
  wallet_signature_verification_failed: {
    status: 409,
    message: "The signature did not verify for the issued message. Start a new connection attempt.",
  },
  wallet_signature_scheme_unsupported: {
    status: 409,
    message: "This wallet's signature scheme is not supported. Try a supported Sui wallet.",
  },
  wallet_signature_encoding_invalid: {
    status: 409,
    message: "The wallet returned an invalid signature format. Reconnect and retry.",
  },
  wallet_signature_missing: {
    status: 409,
    message: "The wallet did not return a signature. Connect again to retry.",
  },
  wallet_derived_address_mismatch: {
    status: 409,
    message: "The signature was produced by a different Sui account. Connect the intended account and retry.",
  },
  wallet_already_linked: {
    status: 409,
    message: "This Sui wallet is linked to a different ORHEX visitor identity.",
  },
  wallet_verification_unavailable: {
    status: 503,
    message: "The wallet verification service is unavailable. Please retry.",
  },
  wallet_verification_internal_error: {
    status: 500,
    message: "Wallet verification could not be completed due to an internal error.",
  },
};

export function mapWalletVerificationError(code, status) {
  const categorized = typeof code === "string" ? categorizedErrors[code] : null;
  if (categorized) return { error: code, ...categorized };
  if (status === 409) {
    return {
      error: "wallet_verification_failed",
      status,
      message: "Wallet verification was rejected. Start a new connection attempt.",
    };
  }
  if (status === 422) {
    return {
      error: "invalid_request",
      status,
      message: "The wallet verification request is invalid.",
    };
  }
  if (status === 429) {
    return {
      error: "rate_limited",
      status,
      message: "Too many wallet verification requests. Please retry shortly.",
    };
  }
  if (status === 403) {
    return {
      error: "wallet_origin_rejected",
      status,
      message: "Wallet verification must be requested by this ORHEX application.",
    };
  }
  if (status === 503) {
    return {
      error: "wallet_service_unavailable",
      status,
      message: "The wallet verification service is temporarily unavailable.",
    };
  }
  return null;
}
