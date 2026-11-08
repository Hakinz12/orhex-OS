export type WalletConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

export interface WalletAccountRecord {
  readonly chainId: string;
  readonly network: string;
  readonly address: string;
  readonly label?: string;
}

export interface WalletConnectionViewModel {
  readonly chainId: string;
  readonly providerId: string;
  readonly network: string;
  readonly status: WalletConnectionStatus;
  readonly address: string | null;
  readonly capabilities: readonly string[];
  readonly error: string | null;
}

export interface WalletConnectionAdapter {
  readonly chainId: string;
  readonly providerId: string;
  readonly network: string;
  readonly status: WalletConnectionStatus;
  readonly address: string | null;
  readonly capabilities: readonly string[];
  readonly error: string | null;
  readonly connected: boolean;

  connect(options?: { silent?: boolean }): Promise<void>;
  disconnect(): Promise<void>;
  signPersonalMessage(message: Uint8Array): Promise<string>;
  getActiveAccount(): WalletAccountRecord | null;
  getViewModel(): WalletConnectionViewModel;
  onAccountChange(listener: (account: WalletAccountRecord | null) => void): () => void;
  onConnectionChange(listener: (viewModel: WalletConnectionViewModel) => void): () => void;
  start(): () => void;
  dispose(): void;
}
