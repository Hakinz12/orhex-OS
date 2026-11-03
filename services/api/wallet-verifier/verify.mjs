import { verifyPersonalMessageSignature } from "@mysten/sui/verify";

const chunks = [];
for await (const chunk of process.stdin) {
  chunks.push(chunk);
}

try {
  const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const message = Uint8Array.from(Buffer.from(request.message, "base64"));
  const publicKey = await verifyPersonalMessageSignature(
    message,
    request.signature,
    { address: request.address },
  );
  process.stdout.write(
    JSON.stringify({ ok: true, address: publicKey.toSuiAddress() }),
  );
} catch {
  process.stdout.write(JSON.stringify({ ok: false }));
}
