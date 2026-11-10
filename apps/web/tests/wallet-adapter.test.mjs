import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test, { after, before } from "node:test";
import { pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import {
  SUI_MAINNET_CHAIN,
  SUI_TESTNET_CHAIN,
  SuiSignPersonalMessage,
  StandardConnect,
  StandardEvents,
} from "@mysten/wallet-standard";

const walletSource = path.resolve("src/lib/wallet/sui.ts");
const walletStateSourcePath = path.resolve("src/lib/wallet/state.tsx");
const rootLayoutSourcePath = path.resolve("src/app/layout.tsx");
let tempDirectory;
let createSuiWalletAdapter;
let walletStateSource;
let rootLayoutSource;

before(async () => {
  tempDirectory = await mkdtemp(path.join(process.cwd(), ".wallet-test-"));
  const source = await readFile(walletSource, "utf8");
  walletStateSource = await readFile(walletStateSourcePath, "utf8");
  rootLayoutSource = await readFile(rootLayoutSourcePath, "utf8");
  const result = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
    reportDiagnostics: true,
  });
  const errors = (result.diagnostics ?? []).filter((diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error);
  assert.deepEqual(errors, [], "wallet adapter transpilation should not report TypeScript errors");
  const compiledPath = path.join(tempDirectory, "sui.mjs");
  await writeFile(compiledPath, result.outputText);
  ({ createSuiWalletAdapter } = await import(`${pathToFileURL(compiledPath).href}?test=${Date.now()}`));
});

after(async () => {
  if (tempDirectory) await rm(tempDirectory, { recursive: true, force: true });
});

function createRegistry(initialWallets = []) {
  const wallets = [...initialWallets];
  const listeners = { register: new Set(), unregister: new Set() };
  let unsubscriptions = 0;
  return {
    api: {
      get: () => [...wallets],
      on(event, listener) {
        listeners[event].add(listener);
        return () => {
          if (listeners[event].delete(listener)) unsubscriptions += 1;
        };
      },
    },
    register(wallet) {
      wallets.push(wallet);
      for (const listener of listeners.register) listener(wallet);
    },
    unregister(wallet) {
      const index = wallets.indexOf(wallet);
      if (index >= 0) wallets.splice(index, 1);
      for (const listener of listeners.unregister) listener(wallet);
    },
    get unsubscriptions() {
      return unsubscriptions;
    },
    get listenerCount() {
      return listeners.register.size + listeners.unregister.size;
    },
  };
}

function createWallet({
  name = "Mock Sui Wallet",
  chains = [SUI_MAINNET_CHAIN],
  accounts = [],
  connect = async () => ({ accounts }),
  disconnect = async () => {},
  signPersonalMessage,
} = {}) {
  const eventListeners = new Set();
  let eventUnsubscriptions = 0;
  const wallet = {
    version: "1.0.0",
    name,
    icon: "data:image/svg+xml;base64,",
    chains,
    accounts,
    features: {
      [StandardConnect]: { version: "1.0.0", connect },
      "standard:disconnect": { version: "1.0.0", disconnect },
      [StandardEvents]: {
        version: "1.0.0",
        on(_event, listener) {
          eventListeners.add(listener);
          return () => {
            if (eventListeners.delete(listener)) eventUnsubscriptions += 1;
          };
        },
      },
      ...(signPersonalMessage
        ? { [SuiSignPersonalMessage]: { version: "1.1.0", signPersonalMessage } }
        : {}),
    },
    emitChange() {
      for (const listener of eventListeners) listener({ accounts: wallet.accounts });
    },
    get eventListenerCount() {
      return eventListeners.size;
    },
    get eventUnsubscriptions() {
      return eventUnsubscriptions;
    },
  };
  return wallet;
}

function createAccount(address, chainId = SUI_MAINNET_CHAIN) {
  return {
    address,
    publicKey: new Uint8Array(32),
    chains: [chainId],
    features: [],
  };
}

test("connects to a Sui account and exposes live adapter state", async () => {
  const account = createAccount("0xabc");
  let receivedOptions;
  const wallet = createWallet({
    accounts: [account],
    connect: async (options) => {
      receivedOptions = options;
      return { accounts: [account] };
    },
  });
  const registry = createRegistry([wallet]);
  const adapter = createSuiWalletAdapter({ registry: registry.api });
  const stop = adapter.start();

  assert.equal(adapter.status, "connected");
  assert.equal(adapter.address, "0xabc");
  assert.equal(adapter.providerId, "Mock Sui Wallet");
  assert.equal(adapter.getActiveAccount().address, "0xabc");

  await adapter.connect();
  assert.deepEqual(receivedOptions, { silent: false });
  assert.equal(adapter.connected, true);
  stop();
});

test("signs the exact challenge bytes using the Wallet Standard account and chain", async () => {
  const account = createAccount("0xsigner");
  const message = new Uint8Array([0, 1, 2, 255]);
  let received;
  const wallet = createWallet({
    accounts: [account],
    signPersonalMessage: async (input) => {
      received = input;
      return {
        bytes: btoa(String.fromCharCode(...input.message)),
        signature: btoa("serialized-sui-signature"),
      };
    },
  });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });

  await adapter.connect();
  assert.deepEqual(await adapter.signPersonalMessage(message), {
    bytes: btoa(String.fromCharCode(...message)),
    signature: btoa("serialized-sui-signature"),
  });
  assert.equal(received.account, account);
  assert.equal(received.chain, SUI_MAINNET_CHAIN);
  assert.deepEqual([...received.message], [...message]);
});

test("diagnoses signing resolution without exposing response values", async () => {
  const message = new Uint8Array([0, 1, 2, 255]);
  const signature = btoa("private-signature-value");
  const wallet = createWallet({
    name: "Diagnostic Mock Wallet",
    accounts: [createAccount("0xsigner")],
    signPersonalMessage: async () => ({
      bytes: btoa(String.fromCharCode(...message)),
      signature,
    }),
  });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
  const diagnostics = [];
  await adapter.connect();

  await adapter.signPersonalMessage(message, (event) => diagnostics.push(event));

  assert.deepEqual(diagnostics.map(({ stage }) => stage), [
    "signing_started",
    "signing_resolved",
    "signature_response_validated",
    "signed_message_matches",
  ]);
  assert.equal(diagnostics[0].wallet_name, "Diagnostic Mock Wallet");
  assert.equal(diagnostics[1].bytes_present, true);
  assert.equal(diagnostics[1].signature_present, true);
  assert.equal(diagnostics[2].bytes_base64_valid, true);
  assert.equal(diagnostics[2].signature_base64_valid, true);
  assert.equal(diagnostics[3].signed_message_matches, true);
  const serialized = JSON.stringify(diagnostics);
  assert.equal(serialized.includes(signature), false);
  assert.equal(serialized.includes(btoa(String.fromCharCode(...message))), false);
  assert.equal(serialized.includes(String.fromCharCode(...message)), false);
  const signedWithBrokenDiagnostic = await adapter.signPersonalMessage(message, () => {
    throw new Error("diagnostic sink failed");
  });
  assert.equal(signedWithBrokenDiagnostic.signature, signature);
});

test("reads getter-backed signing response fields once for validation and diagnostics", async () => {
  const message = new Uint8Array([5, 6, 7]);
  const values = {
    bytes: btoa(String.fromCharCode(...message)),
    signature: btoa("serialized-signature"),
  };
  const reads = { bytes: 0, signature: 0 };
  const wallet = createWallet({
    accounts: [createAccount("0xsigner")],
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
  });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
  const diagnostics = [];
  await adapter.connect();

  const signed = await adapter.signPersonalMessage(message, (event) => diagnostics.push(event));

  assert.deepEqual(reads, { bytes: 1, signature: 1 });
  assert.deepEqual(signed, values);
  assert.equal(diagnostics[1].bytes_present, true);
  assert.equal(diagnostics[1].signature_present, true);
  assert.equal(JSON.stringify(diagnostics).includes(values.bytes), false);
  assert.equal(JSON.stringify(diagnostics).includes(values.signature), false);
});

test("sanitizes and bounds the wallet name with one metadata read per signing attempt", async (t) => {
  const cases = [
    ["ordinary name", "Slush Wallet", "Slush Wallet"],
    ["controls and formatting", " \u0000\u202eWallet\u200b\nName ", "WalletName"],
    ["long name", "A".repeat(120), "A".repeat(80)],
    ["empty name", " \u200b\n", "Unknown wallet"],
  ];

  for (const [label, name, expected] of cases) {
    await t.test(label, async () => {
      let nameReads = 0;
      const wallet = createWallet({
        accounts: [createAccount("0xsigner")],
        signPersonalMessage: async (input) => ({
          bytes: btoa(String.fromCharCode(...input.message)),
          signature: btoa("signature"),
        }),
      });
      Object.defineProperty(wallet, "name", {
        configurable: true,
        get() {
          nameReads += 1;
          return name;
        },
      });
      const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
      await adapter.connect();
      nameReads = 0;
      const diagnostics = [];

      await adapter.signPersonalMessage(new Uint8Array([1]), (event) => diagnostics.push(event));

      assert.equal(nameReads, 1);
      assert.equal(diagnostics[0].wallet_name, expected);
      assert.equal(diagnostics[1].wallet_name, expected);
      assert.equal(/[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/u.test(expected), false);
      assert.equal(expected.length <= 80, true);
    });
  }
});

test("uses a safe fallback when wallet-name metadata is invalid", async () => {
  let invalidName = false;
  const wallet = createWallet({
    accounts: [createAccount("0xsigner")],
    signPersonalMessage: async (input) => ({
      bytes: btoa(String.fromCharCode(...input.message)),
      signature: btoa("signature"),
    }),
  });
  Object.defineProperty(wallet, "name", {
    configurable: true,
    get() {
      return invalidName ? 42 : "Mock Sui Wallet";
    },
  });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
  await adapter.connect();
  invalidName = true;
  const diagnostics = [];

  await adapter.signPersonalMessage(new Uint8Array([1]), (event) => diagnostics.push(event));

  assert.equal(diagnostics[0].wallet_name, "Unknown wallet");
  assert.equal(diagnostics[1].wallet_name, "Unknown wallet");
});

test("wallet response getter errors retain their original validation error", async () => {
  const getterError = new Error("wallet response getter failed");
  const wallet = createWallet({
    accounts: [createAccount("0xsigner")],
    signPersonalMessage: async () => ({
      get signature() {
        throw getterError;
      },
      get bytes() {
        return btoa("message");
      },
    }),
  });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
  const diagnostics = [];
  await adapter.connect();

  await assert.rejects(
    adapter.signPersonalMessage(new Uint8Array([1]), (event) => diagnostics.push(event)),
    (error) => error === getterError,
  );
  assert.deepEqual(diagnostics.map(({ stage }) => stage), ["signing_started"]);
});

test("rejects malformed or mismatched Wallet Standard signing responses", async (t) => {
  const message = new Uint8Array([1, 2, 3]);
  for (const [name, response, expectedError] of [
    ["mismatched bytes", { bytes: btoa("other"), signature: btoa("signature") }, /different message/],
    ["invalid bytes", { bytes: "!", signature: btoa("signature") }, /invalid personal-message signature/],
    ["invalid signature", { bytes: btoa(String.fromCharCode(1, 2, 3)), signature: "!" }, /invalid personal-message signature/],
    ["missing bytes", { signature: btoa("signature") }, /incomplete personal-message signature/],
  ]) {
    await t.test(name, async () => {
      const diagnostics = [];
      const wallet = createWallet({
        accounts: [createAccount("0xsigner")],
        signPersonalMessage: async () => response,
      });
      const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
      await adapter.connect();
      await assert.rejects(
        adapter.signPersonalMessage(message, (event) => diagnostics.push(event)),
        expectedError,
      );
      const invalid = diagnostics.find(({ stage }) => stage === "signature_response_invalid");
      assert.ok(invalid);
      assert.equal(JSON.stringify(diagnostics).includes(btoa("signature")), false);
      if (name === "mismatched bytes") {
        assert.equal(invalid.reason, "signed_message_mismatch");
        assert.equal(invalid.signed_message_matches, false);
      }
      if (name === "invalid bytes") {
        assert.equal(invalid.reason, "base64_invalid");
        assert.equal(invalid.bytes_base64_valid, false);
      }
      if (name === "invalid signature") {
        assert.equal(invalid.reason, "base64_invalid");
        assert.equal(invalid.signature_base64_valid, false);
      }
      if (name === "missing bytes") {
        assert.equal(invalid.reason, "fields_missing_or_invalid");
        assert.equal(invalid.bytes_present, false);
      }
    });
  }
});

test("propagates wallet signature rejection without returning a signature", async () => {
  const diagnostics = [];
  const secretErrorText = "wallet exception containing sensitive context";
  const wallet = createWallet({
    accounts: [createAccount("0xsigner")],
    signPersonalMessage: async () => {
      throw new Error(secretErrorText);
    },
  });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
  await adapter.connect();

  await assert.rejects(
    adapter.signPersonalMessage(new Uint8Array([1, 2, 3]), (event) => diagnostics.push(event)),
    new RegExp(secretErrorText),
  );
  assert.deepEqual(diagnostics, [
    { stage: "signing_started", wallet_name: "Mock Sui Wallet", signing_attempted: true },
    { stage: "signing_rejected", exception_class: "Error" },
  ]);
  assert.equal(JSON.stringify(diagnostics).includes(secretErrorText), false);
});

test("reports user rejection without retaining an account", async () => {
  const wallet = createWallet({
    connect: async () => {
      throw new Error("User rejected the connection request.");
    },
  });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });

  await adapter.connect();
  assert.equal(adapter.status, "error");
  assert.equal(adapter.address, null);
  assert.match(adapter.error, /rejected/);
});

test("reports no installed compatible wallet", async () => {
  const adapter = createSuiWalletAdapter({ registry: createRegistry().api });
  const stop = adapter.start();
  await adapter.connect();

  assert.equal(adapter.status, "error");
  assert.match(adapter.error, /No compatible Sui wallet/);
  stop();
});

test("discovers and subscribes to a wallet registered after adapter start", async () => {
  const registry = createRegistry();
  const adapter = createSuiWalletAdapter({ registry: registry.api });
  const stop = adapter.start();
  const wallet = createWallet({ connect: async () => ({ accounts: [createAccount("0xlate")] }) });

  registry.register(wallet);
  assert.equal(adapter.providerId, wallet.name);
  assert.equal(wallet.eventListenerCount, 1);
  await adapter.connect();
  assert.equal(adapter.address, "0xlate");

  stop();
});

test("tracks account changes from wallet-standard events", () => {
  const wallet = createWallet({ accounts: [createAccount("0xfirst")] });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
  const stop = adapter.start();

  wallet.accounts = [createAccount("0xsecond", SUI_TESTNET_CHAIN)];
  wallet.emitChange();
  assert.equal(adapter.address, "0xsecond");
  assert.equal(adapter.network, "testnet");
  assert.equal(adapter.getViewModel().chainId, SUI_TESTNET_CHAIN);

  stop();
});

test("provider disconnect clears the account", () => {
  const wallet = createWallet({ accounts: [createAccount("0xconnected")] });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
  const stop = adapter.start();

  wallet.accounts = [];
  wallet.emitChange();
  assert.equal(adapter.status, "disconnected");
  assert.equal(adapter.address, null);
  stop();
});

test("switches to another compatible wallet when the selected wallet is unregistered", () => {
  const first = createWallet({ name: "First", accounts: [createAccount("0xfirst")] });
  const second = createWallet({ name: "Second", accounts: [createAccount("0xsecond")] });
  const registry = createRegistry([first, second]);
  const adapter = createSuiWalletAdapter({ registry: registry.api });
  const stop = adapter.start();

  registry.unregister(first);
  assert.equal(adapter.providerId, "Second");
  assert.equal(adapter.address, "0xsecond");
  stop();
});

test("ORHEX disconnect is local and does not claim to revoke wallet authorization", async () => {
  const account = createAccount("0xauthorized");
  let providerDisconnectCalls = 0;
  const wallet = createWallet({
    accounts: [account],
    disconnect: async () => {
      providerDisconnectCalls += 1;
    },
  });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
  const stop = adapter.start();

  await adapter.disconnect();
  wallet.emitChange();
  assert.equal(adapter.status, "disconnected");
  assert.equal(adapter.address, null);
  assert.equal(providerDisconnectCalls, 0);
  stop();
});

test("cleans up event listeners and supports repeated start and dispose", () => {
  const wallet = createWallet({ accounts: [createAccount("0xcleanup")] });
  const registry = createRegistry([wallet]);
  const adapter = createSuiWalletAdapter({ registry: registry.api });
  const stopFirst = adapter.start();
  const stopSecond = adapter.start();
  assert.equal(registry.listenerCount, 2);
  assert.equal(wallet.eventListenerCount, 1);

  stopFirst();
  assert.equal(registry.listenerCount, 2);
  stopSecond();
  assert.equal(registry.listenerCount, 0);
  assert.equal(wallet.eventListenerCount, 0);
  assert.equal(adapter.status, "disconnected");
  assert.equal(adapter.address, null);
  assert.equal(registry.unsubscriptions, 2);
  assert.equal(wallet.eventUnsubscriptions, 1);

  adapter.dispose();
  adapter.dispose();
  const stopAgain = adapter.start();
  assert.equal(registry.listenerCount, 2);
  assert.equal(wallet.eventListenerCount, 1);
  stopAgain();
  assert.equal(adapter.status, "disconnected");
  assert.equal(adapter.address, null);
});

test("formatAddress shortens addresses with an explicit Unicode ellipsis", () => {
  const functionSource = walletStateSource.match(/export function formatAddress[\s\S]*?\n}/)?.[0];
  assert.ok(functionSource, "formatAddress should be present in wallet state");
  const compiled = ts.transpileModule(functionSource.replace(/^export /, ""), {
    compilerOptions: { module: ts.ModuleKind.None, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const formatAddress = runInNewContext(`${compiled}\nformatAddress`);

  assert.equal(formatAddress("0x1234567890abcdef", 4), "0x12\u2026cdef");
});

test("wallet provider is rooted under session context and verification is address-bound", () => {
  assert.match(
    rootLayoutSource,
    /<SessionProvider>\s*<WalletConnectionProvider>\{children\}<\/WalletConnectionProvider>\s*<\/SessionProvider>/,
  );
  assert.match(
    walletStateSource,
    /wallet\.status === "connected" &&[\s\S]*?verification\?\.verified === true[\s\S]*?verification\.network === wallet\.network[\s\S]*?canonicalWalletAddress\(verification\.address\) ===\s*canonicalWalletAddress\(wallet\.address\)/,
  );
  assert.match(
    walletStateSource,
    /adapter\.onAccountChange\(\(\) => \{[\s\S]*?setVerification\(null\);/,
  );
  assert.match(walletStateSource, /await adapter\.connect\(\{ silent: false \}\);[\s\S]*?completeWalletVerification/);
  assert.match(walletStateSource, /const exposedAddress = verified \? wallet\.address : null;/);
  assert.match(walletStateSource, /const exposedStatus = verified\s*\? "connected"/);
  assert.doesNotMatch(walletStateSource, /\bverify:\s*verify\b/);
});

test("wallet panel has one atomic connect action and no unverified connected state", async () => {
  const panelSource = await readFile(
    path.resolve("src/components/wallet-connection.tsx"),
    "utf8",
  );
  assert.match(panelSource, /onClick=\{\(\) => void runWalletAction\(connect\)\}/);
  assert.doesNotMatch(panelSource, />Verify Wallet</);
  assert.doesNotMatch(panelSource, /connection alone does not verify ownership/i);
});

test("ignores wallets that do not support Sui", async () => {
  const wallet = createWallet({ chains: ["eip155:1"], accounts: [createAccount("0xnot-sui")] });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
  const stop = adapter.start();

  assert.equal(adapter.providerId, "not-configured");
  await adapter.connect();
  assert.equal(adapter.status, "error");
  assert.match(adapter.error, /No compatible Sui wallet/);
  stop();
});

test("silent connection requests are explicitly non-interactive", async () => {
  let receivedOptions;
  const wallet = createWallet({
    connect: async (options) => {
      receivedOptions = options;
      return { accounts: [createAccount("0xsilent")] };
    },
  });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });

  await adapter.connect({ silent: true });
  assert.deepEqual(receivedOptions, { silent: true });
  assert.equal(adapter.address, "0xsilent");
});

test("page-load discovery never calls the approval-capable connect method", () => {
  let connectCalls = 0;
  const wallet = createWallet({
    connect: async () => {
      connectCalls += 1;
      return { accounts: [] };
    },
  });
  const adapter = createSuiWalletAdapter({ registry: createRegistry([wallet]).api });
  const stop = adapter.start();

  assert.equal(connectCalls, 0);
  assert.equal(adapter.status, "disconnected");
  stop();
});

test("a stale lifecycle cleanup cannot stop a later adapter lease", () => {
  const wallet = createWallet({ accounts: [createAccount("0xreused")] });
  const registry = createRegistry([wallet]);
  const adapter = createSuiWalletAdapter({ registry: registry.api });
  const staleStop = adapter.start();

  adapter.dispose();
  const stopAgain = adapter.start();
  staleStop();
  assert.equal(registry.listenerCount, 2);
  assert.equal(wallet.eventListenerCount, 1);
  stopAgain();
  assert.equal(registry.listenerCount, 0);
  assert.equal(wallet.eventListenerCount, 0);
});
