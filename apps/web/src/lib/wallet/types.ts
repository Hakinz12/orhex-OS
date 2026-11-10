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

export interface SignedPersonalMessage {
  readonly bytes: string;
  readonly signature: string;
}

export interface WalletDiagnosticEvent {
  readonly stage: string;
  readonly [key: string]: string | boolean | number | null | undefined;
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
  signPersonalMessage(
    message: Uint8Array,
    onDiagnostic?: (event: WalletDiagnosticEvent) => void,
  ): Promise<SignedPersonalMessage>;
  getActiveAccount(): WalletAccountRecord | null;
  getViewModel(): WalletConnectionViewModel;
  onAccountChange(listener: (account: WalletAccountRecord | null) => void): () => void;
  onConnectionChange(listener: (viewModel: WalletConnectionViewModel) => void): () => void;
  start(): () => void;
  dispose(): void;
}
