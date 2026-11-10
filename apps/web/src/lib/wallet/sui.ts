import {
  SUI_DEVNET_CHAIN,
  SUI_MAINNET_CHAIN,
  SUI_TESTNET_CHAIN,
  SuiSignPersonalMessage,
  StandardConnect,
  StandardEvents,
  getWallets,
  type Wallet,
  type WalletAccount,
} from "@mysten/wallet-standard";

import type {
  WalletAccountRecord,
  WalletConnectionAdapter,
  WalletConnectionStatus,
  WalletConnectionViewModel,
  SignedPersonalMessage,
  WalletDiagnosticEvent,
} from "./types";

const SUI_CHAIN_IDS = new Set([SUI_MAINNET_CHAIN, SUI_TESTNET_CHAIN, SUI_DEVNET_CHAIN]);

export interface SuiWalletRegistry {
  get(): readonly Wallet[];
  on(event: "register" | "unregister", listener: (...wallets: Wallet[]) => void): () => void;
}

interface SuiWalletAdapterOptions {
  registry?: SuiWalletRegistry;
}

interface ConnectFeature {
  connect(input?: { silent?: boolean }): Promise<{ accounts: readonly WalletAccount[] }>;
}

interface EventsFeature {
  on(event: "change", listener: () => void): () => void;
}

interface SignPersonalMessageFeature {
  signPersonalMessage(input: {
    account: WalletAccount;
    message: Uint8Array;
    chain: string;
  }): Promise<{ bytes: string; signature: string }>;
}

function decodeBase64(value: string): Uint8Array {
  if (
    !value ||
    value.length % 4 !== 0 ||
    !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)
  ) {
    throw new Error("The wallet returned an invalid personal-message response.");
  }
  const decoded = atob(value);
  return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
}

function sameBytes(left: Uint8Array, right: Uint8Array): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function reportDiagnostic(
  onDiagnostic: ((event: WalletDiagnosticEvent) => void) | undefined,
  event: WalletDiagnosticEvent,
): void {
  try {
    onDiagnostic?.(event);
  } catch {
    // Diagnostics must not change wallet signing behavior.
  }
}

function exceptionClass(reason: unknown): string {
  try {
    const name = reason instanceof Error ? reason.name : "UnknownError";
    return /^[A-Za-z][A-Za-z0-9]{0,63}$/.test(name) ? name : "UnknownError";
  } catch {
    return "UnknownError";
  }
}

function safeWalletName(wallet: Wallet): string {
  try {
    const name = wallet.name;
    if (typeof name !== "string") return "Unknown wallet";
    const sanitized = name
      .replace(/[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/gu, "")
      .replace(/\s+/gu, " ")
      .trim()
      .slice(0, 80);
    return sanitized || "Unknown wallet";
  } catch {
    return "Unknown wallet";
  }
}

function isWallet(value: unknown): value is Wallet {
  return typeof value === "object" && value !== null
    && "chains" in value && Array.isArray(value.chains)
    && "features" in value && typeof value.features === "object" && value.features !== null
    && "accounts" in value && Array.isArray(value.accounts)
    && "name" in value && typeof value.name === "string";
}

function isSuiWallet(wallet: Wallet): boolean {
  return wallet.chains.some((chainId) => SUI_CHAIN_IDS.has(chainId));
}

function networkForChain(chainId: string): string {
  if (chainId === SUI_TESTNET_CHAIN) return "testnet";
  if (chainId === SUI_DEVNET_CHAIN) return "devnet";
  return "mainnet";
}

function toWalletRecord(account: WalletAccount): WalletAccountRecord | null {
  const chainId = account.chains.find((candidate) => SUI_CHAIN_IDS.has(candidate));
  if (!chainId || !account.address) return null;

  return {
    chainId,
    network: networkForChain(chainId),
    address: account.address,
    label: account.label,
  };
}

function getConnectFeature(wallet: Wallet): ConnectFeature | null {
  const feature: unknown = wallet.features[StandardConnect];
  if (typeof feature !== "object" || feature === null || !("connect" in feature) || typeof feature.connect !== "function") {
    return null;
  }
  return feature as ConnectFeature;
}

function getEventsFeature(wallet: Wallet): EventsFeature | null {
  const feature: unknown = wallet.features[StandardEvents];
  if (typeof feature !== "object" || feature === null || !("on" in feature) || typeof feature.on !== "function") {
    return null;
  }
  return feature as EventsFeature;
}

function getSignPersonalMessageFeature(wallet: Wallet): SignPersonalMessageFeature | null {
  const feature: unknown = wallet.features[SuiSignPersonalMessage];
  if (
    typeof feature !== "object" ||
    feature === null ||
    !("signPersonalMessage" in feature) ||
    typeof feature.signPersonalMessage !== "function"
  ) {
    return null;
  }
  return feature as SignPersonalMessageFeature;
}

export function createSuiWalletAdapter(
  options: SuiWalletAdapterOptions = {},
): WalletConnectionAdapter {
  const registry = options.registry ?? getWallets();
  let status: WalletConnectionStatus = "disconnected";
  let selectedWallet: Wallet | null = null;
  let account: WalletAccountRecord | null = null;
  let error: string | null = null;
  let locallyDisconnected = false;
  let startCount = 0;
  let lifecycleVersion = 0;
  let disposed = false;
  let registryUnsubscribers: Array<() => void> = [];
  const walletUnsubscribers = new Map<Wallet, () => void>();
  const accountListeners = new Set<(value: WalletAccountRecord | null) => void>();
  const connectionListeners = new Set<(value: WalletConnectionViewModel) => void>();

  const getCompatibleWallets = (): Wallet[] => registry.get().filter(isWallet).filter(isSuiWallet);
  const getViewModel = (): WalletConnectionViewModel => {
    const chainId = account?.chainId
      ?? selectedWallet?.chains.find((candidate) => SUI_CHAIN_IDS.has(candidate))
      ?? SUI_MAINNET_CHAIN;
    return {
      chainId,
      providerId: selectedWallet?.name ?? "not-configured",
      network: account?.network ?? networkForChain(chainId),
      status,
      address: account?.address ?? null,
      capabilities: selectedWallet ? Object.keys(selectedWallet.features).sort() : [],
      error,
    };
  };

  const emit = () => {
    const viewModel = getViewModel();
    for (const listener of accountListeners) listener(account);
    for (const listener of connectionListeners) listener(viewModel);
  };

  const setDisconnected = (nextError: string | null = null) => {
    account = null;
    status = nextError ? "error" : "disconnected";
    error = nextError;
  };

  const chooseWallet = (): Wallet | null => {
    const wallets = getCompatibleWallets();
    if (selectedWallet && wallets.includes(selectedWallet)) return selectedWallet;
    return wallets[0] ?? null;
  };

  const refreshSelectedWallet = () => {
    const nextWallet = chooseWallet();
    if (nextWallet !== selectedWallet) {
      selectedWallet = nextWallet;
      account = null;
      if (!locallyDisconnected) setDisconnected();
    }

    if (!selectedWallet || locallyDisconnected) {
      if (!selectedWallet) setDisconnected();
      return;
    }

    const authorized = selectedWallet.accounts
      .map(toWalletRecord)
      .find((candidate): candidate is WalletAccountRecord => candidate !== null);
    if (authorized) {
      account = authorized;
      status = "connected";
      error = null;
    } else {
      setDisconnected();
    }
  };

  const onWalletChange = (wallet: Wallet) => {
    if (wallet !== selectedWallet) return;
    if (locallyDisconnected) {
      setDisconnected();
    } else {
      refreshSelectedWallet();
    }
    emit();
  };

  const subscribeWallet = (wallet: Wallet) => {
    if (walletUnsubscribers.has(wallet)) return;
    const events = getEventsFeature(wallet);
    if (events) walletUnsubscribers.set(wallet, events.on("change", () => onWalletChange(wallet)));
  };

  const unsubscribeRemovedWallets = () => {
    const current = new Set(getCompatibleWallets());
    for (const [wallet, unsubscribe] of walletUnsubscribers) {
      if (!current.has(wallet)) {
        unsubscribe();
        walletUnsubscribers.delete(wallet);
      }
    }
  };

  const handleRegistryChange = () => {
    for (const wallet of getCompatibleWallets()) subscribeWallet(wallet);
    unsubscribeRemovedWallets();
    const previousWallet = selectedWallet;
    refreshSelectedWallet();
    if (previousWallet !== selectedWallet && locallyDisconnected) setDisconnected();
    emit();
  };

  const start = (): (() => void) => {
    if (startCount === 0) disposed = false;
    startCount += 1;
    const leaseVersion = lifecycleVersion;
    if (startCount === 1) {
      registryUnsubscribers = [
        registry.on("register", handleRegistryChange),
        registry.on("unregister", handleRegistryChange),
      ];
      for (const wallet of getCompatibleWallets()) subscribeWallet(wallet);
      refreshSelectedWallet();
      emit();
    }

    let released = false;
    return () => {
      if (released) return;
      released = true;
      if (leaseVersion !== lifecycleVersion) return;
      startCount = Math.max(0, startCount - 1);
      if (startCount === 0) dispose();
    };
  };

  const stopSubscriptions = () => {
    for (const unsubscribe of registryUnsubscribers) unsubscribe();
    registryUnsubscribers = [];
    for (const unsubscribe of walletUnsubscribers.values()) unsubscribe();
    walletUnsubscribers.clear();
  };

  const dispose = () => {
    if (disposed) return;
    disposed = true;
    lifecycleVersion += 1;
    startCount = 0;
    stopSubscriptions();
    setDisconnected();
    selectedWallet = null;
    locallyDisconnected = false;
    emit();
    accountListeners.clear();
    connectionListeners.clear();
  };

  const connect = async (connectOptions?: { silent?: boolean }): Promise<void> => {
    const wallet = chooseWallet();
    selectedWallet = wallet;
    locallyDisconnected = false;
    if (!wallet) {
      setDisconnected("No compatible Sui wallet is installed in this browser.");
      emit();
      return;
    }
    if (startCount === 0) {
      disposed = false;
      for (const candidate of getCompatibleWallets()) subscribeWallet(candidate);
    }

    const feature = getConnectFeature(wallet);
    if (!feature) {
      setDisconnected("This wallet does not support the standard connect flow.");
      emit();
      return;
    }

    status = "connecting";
    error = null;
    emit();
    try {
      const output = await feature.connect({ silent: connectOptions?.silent ?? false });
      const connectedAccount = output.accounts.map(toWalletRecord)
        .find((candidate): candidate is WalletAccountRecord => candidate !== null);
      if (!connectedAccount) {
        setDisconnected("The wallet returned no supported Sui account.");
      } else {
        account = connectedAccount;
        status = "connected";
        error = null;
      }
    } catch (reason) {
      setDisconnected(reason instanceof Error ? reason.message : "Wallet connection failed.");
    }
    emit();
  };

  const disconnect = async (): Promise<void> => {
    locallyDisconnected = true;
    setDisconnected();
    emit();
  };

  const signPersonalMessage = async (
    message: Uint8Array,
    onDiagnostic?: (event: WalletDiagnosticEvent) => void,
  ): Promise<SignedPersonalMessage> => {
    if (!account || !selectedWallet || status !== "connected") {
      throw new Error("Connect a Sui wallet before verifying ownership.");
    }
    const feature = getSignPersonalMessageFeature(selectedWallet);
    if (!feature) {
      throw new Error("This wallet does not support secure personal-message signing.");
    }
    const activeAccount = selectedWallet.accounts.find((candidate) => {
      const record = toWalletRecord(candidate);
      if (!record || !account) return false;
      return record.address === account.address && record.chainId === account.chainId;
    });
    if (!activeAccount) {
      throw new Error("The connected wallet account changed. Reconnect and try again.");
    }
    const walletName = safeWalletName(selectedWallet);
    reportDiagnostic(onDiagnostic, {
      stage: "signing_started",
      wallet_name: walletName,
      signing_attempted: true,
    });
    let signed: { bytes: string; signature: string };
    try {
      signed = await feature.signPersonalMessage({
        account: activeAccount,
        message,
        chain: account.chainId,
      });
    } catch (reason) {
      reportDiagnostic(onDiagnostic, {
        stage: "signing_rejected",
        exception_class: exceptionClass(reason),
      });
      throw reason;
    }
    if (!signed) {
      reportDiagnostic(onDiagnostic, {
        stage: "signing_resolved",
        wallet_name: walletName,
        bytes_present: false,
        signature_present: false,
      });
      reportDiagnostic(onDiagnostic, {
        stage: "signature_response_invalid",
        reason: "fields_missing_or_invalid",
        bytes_present: false,
        signature_present: false,
      });
      throw new Error("The wallet returned an incomplete personal-message signature.");
    }
    const signedSignature = signed.signature;
    if (typeof signedSignature !== "string") {
      reportDiagnostic(onDiagnostic, {
        stage: "signing_resolved",
        wallet_name: walletName,
        signature_present: false,
      });
      reportDiagnostic(onDiagnostic, {
        stage: "signature_response_invalid",
        reason: "fields_missing_or_invalid",
        signature_present: false,
      });
      throw new Error("The wallet returned an incomplete personal-message signature.");
    }
    const signedBytesValue = signed.bytes;
    reportDiagnostic(onDiagnostic, {
      stage: "signing_resolved",
      wallet_name: walletName,
      bytes_present: typeof signedBytesValue === "string",
      signature_present: true,
    });
    if (typeof signedBytesValue !== "string") {
      reportDiagnostic(onDiagnostic, {
        stage: "signature_response_invalid",
        reason: "fields_missing_or_invalid",
        bytes_present: false,
        signature_present: true,
      });
      throw new Error("The wallet returned an incomplete personal-message signature.");
    }
    let signedBytes: Uint8Array;
    let bytesBase64Valid = true;
    let signatureBase64Valid = true;
    try {
      signedBytes = decodeBase64(signedBytesValue);
    } catch {
      bytesBase64Valid = false;
      signedBytes = new Uint8Array();
    }
    try {
      decodeBase64(signedSignature);
    } catch {
      signatureBase64Valid = false;
    }
    if (!bytesBase64Valid || !signatureBase64Valid) {
      reportDiagnostic(onDiagnostic, {
        stage: "signature_response_invalid",
        reason: "base64_invalid",
        bytes_present: true,
        signature_present: true,
        bytes_base64_valid: bytesBase64Valid,
        signature_base64_valid: signatureBase64Valid,
      });
      throw new Error("The wallet returned an invalid personal-message signature.");
    }
    reportDiagnostic(onDiagnostic, {
      stage: "signature_response_validated",
      bytes_present: true,
      signature_present: true,
      bytes_base64_valid: true,
      signature_base64_valid: true,
    });
    const signedMessageMatches = sameBytes(signedBytes, message);
    reportDiagnostic(onDiagnostic, {
      stage: "signed_message_matches",
      signed_message_matches: signedMessageMatches,
    });
    if (!signedMessageMatches) {
      reportDiagnostic(onDiagnostic, {
        stage: "signature_response_invalid",
        reason: "signed_message_mismatch",
        bytes_present: true,
        signature_present: true,
        bytes_base64_valid: true,
        signature_base64_valid: true,
        signed_message_matches: false,
      });
      throw new Error("The wallet signed a different message than the verification challenge.");
    }
    return { bytes: signedBytesValue, signature: signedSignature };
  };

  const onAccountChange = (listener: (value: WalletAccountRecord | null) => void) => {
    accountListeners.add(listener);
    return () => accountListeners.delete(listener);
  };

  const onConnectionChange = (listener: (value: WalletConnectionViewModel) => void) => {
    connectionListeners.add(listener);
    return () => connectionListeners.delete(listener);
  };

  return {
    get chainId() { return getViewModel().chainId; },
    get providerId() { return getViewModel().providerId; },
    get network() { return getViewModel().network; },
    get status() { return status; },
    get address() { return account?.address ?? null; },
    get capabilities() { return getViewModel().capabilities; },
    get error() { return error; },
    get connected() { return status === "connected" && account !== null; },
    connect,
    disconnect,
    signPersonalMessage,
    getActiveAccount: () => account,
    getViewModel,
    onAccountChange,
    onConnectionChange,
    start,
    dispose,
  };
}

export function isSuiWalletInstalled(): boolean {
  return getWallets().get().some((wallet) => isWallet(wallet) && isSuiWallet(wallet));
}
