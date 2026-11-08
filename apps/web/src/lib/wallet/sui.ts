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
  }): Promise<{ signature: string }>;
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

  const signPersonalMessage = async (message: Uint8Array): Promise<string> => {
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
    const signed = await feature.signPersonalMessage({
      account: activeAccount,
      message,
      chain: account.chainId,
    });
    if (!signed || typeof signed.signature !== "string" || !signed.signature) {
      throw new Error("The wallet did not return a personal-message signature.");
    }
    return signed.signature;
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
