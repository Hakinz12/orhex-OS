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
