import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test, { after, before } from "node:test";
import { pathToFileURL } from "node:url";
import ts from "typescript";

const sourcePath = path.resolve("src/lib/sui-data.ts");
let tempDirectory;
let SuiWalletDataController;
let formatCoinBalance;
let formatCoinHolding;
let getCoinDisplayName;
let getNativeSuiBalance;

before(async () => {
  tempDirectory = await mkdtemp(path.join(os.tmpdir(), "orhex-sui-data-"));
  const source = await readFile(sourcePath, "utf8");
  const result = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
    reportDiagnostics: true,
  });
  const errors = (result.diagnostics ?? []).filter(
    (diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error,
  );
  assert.deepEqual(errors, [], "Sui dashboard data module should transpile without errors");
  const compiledPath = path.join(tempDirectory, "sui-data.mjs");
  await writeFile(compiledPath, result.outputText);
  ({
    SuiWalletDataController,
    formatCoinBalance,
    formatCoinHolding,
    getCoinDisplayName,
    getNativeSuiBalance,
  } =
    await import(`${pathToFileURL(compiledPath).href}?test=${Date.now()}`));
});

after(async () => {
  if (tempDirectory) await rm(tempDirectory, { recursive: true, force: true });
});

const walletA = `0x${"a".repeat(64)}`;
const walletB = `0x${"b".repeat(64)}`;

function balancePage(address, balances = [], nextCursor = null) {
  return {
    sui_address: address,
    network: "mainnet",
    balances,
    pagination: { next_cursor: nextCursor, has_next_page: nextCursor !== null },
  };
}

function objectPage(address, objects = [], nextCursor = null) {
  return {
    sui_address: address,
    network: "mainnet",
    objects,
    pagination: { next_cursor: nextCursor, has_next_page: nextCursor !== null },
  };
}

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function balance(coinType, raw, amount, decimals, symbol) {
  return { coin_type: coinType, raw_balance: raw, amount, decimals, symbol };
}

function object(objectId, version = "1") {
  return {
    object_id: objectId,
    version,
    digest: "1".repeat(32),
    type: "0x2::example::Item",
  };
}

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  assert.fail("Timed out waiting for wallet data state.");
}

test("connected address loads native SUI, token metadata, and owned objects from the BFF", async () => {
  const calls = [];
  const controller = new SuiWalletDataController(async (url, init) => {
    calls.push({ url, init });
    if (url.includes("/balances")) {
      return jsonResponse(balancePage(walletA, [
        balance("0x2::sui::SUI", "1500000000", "1.5", 9, "SUI"),
        balance("0xabc::usd::USDC", "1250", null, null, null),
      ]));
    }
    return jsonResponse(objectPage(walletA, [object("0x3", "12")]));
  });

  controller.setAddress(walletA);
  assert.equal(controller.getSnapshot().balanceStatus, "loading");
  assert.equal(controller.getSnapshot().objectsStatus, "loading");
  await waitFor(() =>
    controller.getSnapshot().balanceStatus === "ready" &&
    controller.getSnapshot().objectsStatus === "ready",
  );

  const state = controller.getSnapshot();
  assert.equal(calls.length, 2);
  assert.ok(calls.every(({ url }) => url.startsWith("/api/sui/wallet/")));
  assert.ok(calls.every(({ init }) => init.headers.Accept === "application/json"));
  assert.equal(state.sui_address, walletA);
  assert.deepEqual(state.balances.map((item) => item.coin_type), [
    "0x2::sui::SUI",
    "0xabc::usd::USDC",
  ]);
  assert.equal(getNativeSuiBalance(state.balances)?.amount, "1.5");
  assert.equal(getCoinDisplayName(state.balances[0]), "Sui");
  assert.equal(getCoinDisplayName(state.balances[1]), "USDC");
  assert.equal(formatCoinHolding(state.balances[0]), "1.5 SUI");
  assert.equal(formatCoinHolding(state.balances[1]), "1250 raw units");
  assert.equal(formatCoinBalance(state.balances[1]), "1250 raw units");
  assert.equal(state.objects[0].object_id, "0x3");
});

test("injected browser fetch retains its required global receiver", async () => {
  const invocations = [];
  const browserFetch = function (url) {
    "use strict";
    assert.equal(this, globalThis);
    invocations.push(url);
    return Promise.resolve(jsonResponse(
      url.includes("/balances") ? balancePage(walletA) : objectPage(walletA),
    ));
  };
  const controller = new SuiWalletDataController(browserFetch);
  controller.setAddress(walletA);
  await waitFor(() =>
    controller.getSnapshot().balanceStatus === "ready" &&
    controller.getSnapshot().objectsStatus === "ready",
  );
  assert.equal(invocations.length, 2);
  assert.ok(invocations.some((url) => url.includes("/balances")));
  assert.ok(invocations.some((url) => url.includes("/objects")));
  controller.dispose();
});

test("empty balance and object responses settle into ready empty states", async () => {
  const controller = new SuiWalletDataController(async (url) =>
    jsonResponse(url.includes("/balances") ? balancePage(walletA) : objectPage(walletA)),
  );
  controller.setAddress(walletA);
  await waitFor(() =>
    controller.getSnapshot().balanceStatus === "ready" &&
    controller.getSnapshot().objectsStatus === "ready",
  );
  assert.deepEqual(controller.getSnapshot().balances, []);
  assert.deepEqual(controller.getSnapshot().objects, []);
  assert.equal(controller.getSnapshot().hasMoreBalances, false);
  assert.equal(controller.getSnapshot().hasMoreObjects, false);
});

test("native SUI balance recognizes canonical padded coin type addresses", () => {
  const native = balance(`0x${"0".repeat(63)}2::sui::SUI`, "1000000000", "1", 9, "SUI");
  assert.equal(getNativeSuiBalance([native]), native);
});

test("pagination requests only the next page when requested", async () => {
  const urls = [];
  const controller = new SuiWalletDataController(async (url) => {
    urls.push(url);
    if (url.includes("/balances")) {
      return jsonResponse(url.includes("cursor=")
        ? balancePage(walletA, [balance("0xabc::coin::NEXT", "9", "0.9", 1, "NXT")])
        : balancePage(walletA, [balance("0x2::sui::SUI", "10", "0.00000001", 9, "SUI")], "balances-page-2"));
    }
    return jsonResponse(url.includes("cursor=")
      ? objectPage(walletA, [object("0x5", "2")])
      : objectPage(walletA, [object("0x4")], "objects-page-2"));
  });
  controller.setAddress(walletA);
  await waitFor(() =>
    controller.getSnapshot().balanceStatus === "ready" &&
    controller.getSnapshot().objectsStatus === "ready",
  );
  assert.equal(urls.length, 2);
  assert.equal(controller.getSnapshot().hasMoreBalances, true);
  assert.equal(controller.getSnapshot().hasMoreObjects, true);

  await Promise.all([controller.loadMore("balances"), controller.loadMore("objects")]);
  assert.equal(urls.length, 4);
  assert.ok(urls.some((url) => url.includes("cursor=balances-page-2")));
  assert.ok(urls.some((url) => url.includes("cursor=objects-page-2")));
  assert.equal(controller.getSnapshot().balances.length, 2);
  assert.equal(controller.getSnapshot().objects.length, 2);
});

test("failed requests expose retryable errors and refresh can recover", async () => {
  let attempts = 0;
  const controller = new SuiWalletDataController(async (url) => {
    if (url.includes("/balances") && attempts++ === 0) {
      return jsonResponse({ error: "provider_unavailable", message: "Temporarily unavailable." }, 503);
    }
    return jsonResponse(url.includes("/balances") ? balancePage(walletA) : objectPage(walletA));
  });
  controller.setAddress(walletA);
  await waitFor(() => controller.getSnapshot().balanceStatus === "error");
  assert.equal(controller.getSnapshot().balanceError, "Temporarily unavailable.");

  await controller.refresh();
  assert.equal(controller.getSnapshot().balanceStatus, "ready");
  assert.equal(controller.getSnapshot().balanceError, null);
});

test("disconnect clears data and disconnected state performs no request", async () => {
  let requests = 0;
  const controller = new SuiWalletDataController(async (url) => {
    requests += 1;
    return jsonResponse(url.includes("/balances")
      ? balancePage(walletA, [balance("0x2::sui::SUI", "1", "0.000000001", 9, "SUI")])
      : objectPage(walletA, [object("0x3")]));
  });
  controller.setAddress(null);
  assert.equal(requests, 0);
  controller.setAddress(walletA);
  await waitFor(() => controller.getSnapshot().balanceStatus === "ready");
  assert.ok(controller.getSnapshot().balances.length > 0);

  controller.setAddress(null);
  assert.equal(controller.getSnapshot().sui_address, "");
  assert.deepEqual(controller.getSnapshot().balances, []);
  assert.deepEqual(controller.getSnapshot().objects, []);
  assert.equal(controller.getSnapshot().balanceStatus, "idle");
});

test("switching accounts ignores late responses from the previous address", async () => {
  const deferred = new Map();
  const controller = new SuiWalletDataController((url) => new Promise((resolve) => {
    const address = url.includes(walletA) ? walletA : walletB;
    const resource = url.includes("/balances") ? "balances" : "objects";
    deferred.set(`${address}:${resource}`, resolve);
  }));
  controller.setAddress(walletA);
  await waitFor(() => deferred.size === 2);
  controller.setAddress(walletB);
  await waitFor(() => deferred.size === 4);

  deferred.get(`${walletB}:balances`)(jsonResponse(
    balancePage(walletB, [balance("0x2::sui::SUI", "22", "0.000000022", 9, "SUI")]),
  ));
  deferred.get(`${walletB}:objects`)(jsonResponse(objectPage(walletB, [object("0xb")])));
  await waitFor(() =>
    controller.getSnapshot().balanceStatus === "ready" &&
    controller.getSnapshot().objectsStatus === "ready",
  );
  deferred.get(`${walletA}:balances`)(jsonResponse(
    balancePage(walletA, [balance("0x2::sui::SUI", "11", "0.000000011", 9, "SUI")]),
  ));
  deferred.get(`${walletA}:objects`)(jsonResponse(objectPage(walletA, [object("0xa")])));
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(controller.getSnapshot().sui_address, walletB);
  assert.equal(controller.getSnapshot().balances[0].raw_balance, "22");
  assert.equal(controller.getSnapshot().objects[0].object_id, "0xb");
});
