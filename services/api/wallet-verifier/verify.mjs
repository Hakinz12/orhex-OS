import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  SIGNATURE_FLAG_TO_SCHEME,
  SIGNATURE_SCHEME_TO_SIZE,
  parseSerializedSignature,
} from "@mysten/sui/cryptography";
import { SuiGraphQLClient } from "@mysten/sui/graphql";
import { verifyPersonalMessageSignature } from "@mysten/sui/verify";

const SUPPORTED_NETWORKS = new Set(["mainnet", "testnet"]);
const GRAPHQL_ENDPOINTS = {
  mainnet: "https://graphql.mainnet.sui.io/graphql",
  testnet: "https://graphql.testnet.sui.io/graphql",
};

function createSuiClient({ network, url }) {
  if (!url) {
    throw new Error("No GraphQL verifier endpoint is configured for this network.");
  }
  return new SuiGraphQLClient({ url, network });
}

function failure(code, scheme) {
  return { ok: false, code, ...(scheme ? { scheme } : {}) };
}

export async function verifyRequest(request, clientFactory = createSuiClient) {
  if (
    !request ||
    typeof request !== "object" ||
    typeof request.message !== "string" ||
    typeof request.signature !== "string" ||
    typeof request.address !== "string"
  ) {
    return failure("signature_missing");
  }
  if (typeof request.network !== "string" || !SUPPORTED_NETWORKS.has(request.network)) {
    return failure("network_unsupported");
  }

  const message = Buffer.from(request.message, "base64");
  const signatureBytes = Buffer.from(request.signature, "base64");
  if (
    message.length === 0 ||
    message.toString("base64") !== request.message ||
    signatureBytes.length === 0 ||
    signatureBytes.toString("base64") !== request.signature
  ) {
    return failure("signature_encoding_invalid");
  }

  const scheme = SIGNATURE_FLAG_TO_SCHEME[signatureBytes[0]];
  if (!scheme) {
    return failure("signature_scheme_unsupported");
  }

  let parsed;
  try {
    parsed = parseSerializedSignature(request.signature);
  } catch {
    return failure("signature_encoding_invalid", scheme);
  }
  if (
    (parsed.publicKey &&
      SIGNATURE_SCHEME_TO_SIZE[scheme] &&
      parsed.publicKey.length !== SIGNATURE_SCHEME_TO_SIZE[scheme]) ||
    (parsed.signature && parsed.signature.length === 0)
  ) {
    return failure("signature_encoding_invalid", scheme);
  }

  try {
    const options =
      scheme === "ZkLogin"
        ? {
            client: clientFactory({
              network: request.network,
              url: GRAPHQL_ENDPOINTS[request.network],
            }),
            address: request.address,
          }
        : undefined;
    const publicKey = await verifyPersonalMessageSignature(
      Uint8Array.from(message),
      request.signature,
      options,
    );
    const address = publicKey.toSuiAddress();
    if (address.toLowerCase() !== request.address.toLowerCase()) {
      return failure("derived_address_mismatch", scheme);
    }
    return { ok: true, address, scheme };
  } catch (error) {
    const invalidAddress =
      error instanceof Error &&
      (error.message === "Signature is not valid for the provided address" ||
        error.message === "Public key bytes do not match the provided address");
    const invalidSignature =
      error instanceof Error &&
      error.message === "Signature is not valid for the provided message";
    return failure(
      invalidAddress
        ? "derived_address_mismatch"
        : invalidSignature
          ? "signature_verification_failed"
          : "verification_service_error",
      scheme,
    );
  }
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }

  let response;
  try {
    response = await verifyRequest(JSON.parse(Buffer.concat(chunks).toString("utf8")));
  } catch {
    response = failure("verification_service_error");
  }
  process.stdout.write(JSON.stringify(response));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
