export type SuiDataStatus = "idle" | "loading" | "loading-more" | "ready" | "error";
export type SuiDataResource = "balances" | "objects";

export interface SuiCoinBalance {
  coin_type: string;
  raw_balance: string;
  amount: string | null;
  decimals: number | null;
  symbol: string | null;
}

export interface SuiOwnedObject {
  object_id: string;
  version: string | null;
  digest: string | null;
  type: string | null;
}

export interface SuiWalletData {
  sui_address: string;
  network: "mainnet" | "testnet" | "devnet" | null;
  balances: SuiCoinBalance[];
  objects: SuiOwnedObject[];
  balanceStatus: SuiDataStatus;
  objectsStatus: SuiDataStatus;
  balanceError: string | null;
  objectsError: string | null;
  balanceCursor: string | null;
  objectsCursor: string | null;
  hasMoreBalances: boolean;
  hasMoreObjects: boolean;
}

const PAGE_LIMIT = 20;
const SUI_COIN_TYPE = "0x2::sui::SUI";

function emptyData(address = ""): SuiWalletData {
  return {
    sui_address: address,
    network: null,
    balances: [],
    objects: [],
    balanceStatus: "idle",
    objectsStatus: "idle",
    balanceError: null,
    objectsError: null,
    balanceCursor: null,
    objectsCursor: null,
    hasMoreBalances: false,
    hasMoreObjects: false,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseBalances(payload: unknown, address: string): {
  network: NonNullable<SuiWalletData["network"]>;
  items: SuiCoinBalance[];
  cursor: string | null;
  hasNextPage: boolean;
} {
  if (!isRecord(payload) || !Array.isArray(payload.balances) || !isRecord(payload.pagination)) {
    throw new Error("The Sui service returned an invalid balances response.");
  }
  if (
    payload.sui_address !== address ||
    !["mainnet", "testnet", "devnet"].includes(String(payload.network))
  ) {
    throw new Error("The Sui service returned balances for an unexpected wallet or network.");
  }
  const { next_cursor: cursor, has_next_page: hasNextPage } = payload.pagination;
  if (
    typeof hasNextPage !== "boolean" ||
    (cursor !== null && typeof cursor !== "string") ||
    (hasNextPage && typeof cursor !== "string")
  ) {
    throw new Error("The Sui service returned invalid balance pagination.");
  }
  const items = payload.balances.map((item): SuiCoinBalance => {
    if (
      !isRecord(item) ||
      typeof item.coin_type !== "string" ||
      typeof item.raw_balance !== "string" ||
      !/^(0|[1-9][0-9]*)$/.test(item.raw_balance) ||
      (item.amount !== null && typeof item.amount !== "string") ||
      (item.decimals !== null &&
        (!Number.isInteger(item.decimals) || (item.decimals as number) < 0 || (item.decimals as number) > 255)) ||
      (item.symbol !== null && typeof item.symbol !== "string")
    ) {
      throw new Error("The Sui service returned an invalid token balance.");
    }
    return {
      coin_type: item.coin_type,
      raw_balance: item.raw_balance,
      amount: item.amount as string | null,
      decimals: item.decimals as number | null,
      symbol: item.symbol as string | null,
    };
  });
  return {
    network: payload.network as NonNullable<SuiWalletData["network"]>,
    items,
    cursor: hasNextPage ? cursor as string : null,
    hasNextPage,
  };
}

function parseObjects(payload: unknown, address: string): {
  network: NonNullable<SuiWalletData["network"]>;
  items: SuiOwnedObject[];
  cursor: string | null;
  hasNextPage: boolean;
} {
  if (!isRecord(payload) || !Array.isArray(payload.objects) || !isRecord(payload.pagination)) {
    throw new Error("The Sui service returned an invalid objects response.");
  }
  if (
    payload.sui_address !== address ||
    !["mainnet", "testnet", "devnet"].includes(String(payload.network))
  ) {
    throw new Error("The Sui service returned objects for an unexpected wallet or network.");
  }
  const { next_cursor: cursor, has_next_page: hasNextPage } = payload.pagination;
  if (
    typeof hasNextPage !== "boolean" ||
    (cursor !== null && typeof cursor !== "string") ||
    (hasNextPage && typeof cursor !== "string")
  ) {
    throw new Error("The Sui service returned invalid object pagination.");
  }
  const items = payload.objects.map((item): SuiOwnedObject => {
    if (
      !isRecord(item) ||
      typeof item.object_id !== "string" ||
      (item.version !== null && typeof item.version !== "string") ||
      (item.digest !== null && typeof item.digest !== "string") ||
      (item.type !== null && typeof item.type !== "string")
    ) {
      throw new Error("The Sui service returned an invalid owned object.");
    }
    return {
      object_id: item.object_id,
      version: item.version as string | null,
      digest: item.digest as string | null,
      type: item.type as string | null,
    };
  });
  return {
    network: payload.network as NonNullable<SuiWalletData["network"]>,
    items,
    cursor: hasNextPage ? cursor as string : null,
    hasNextPage,
  };
}

function safeMessage(payload: unknown, fallback: string): string {
  return isRecord(payload) && typeof payload.message === "string"
    ? payload.message
    : fallback;
}

export class SuiWalletDataController {
  private state = emptyData();
  private readonly listeners = new Set<() => void>();
  private readonly requests: Record<SuiDataResource, AbortController | null> = {
    balances: null,
    objects: null,
  };
  private generation = 0;
  private fetchImpl: typeof fetch;

  constructor(fetchImpl: typeof fetch = fetch) {
    this.fetchImpl = fetchImpl;
  }

  readonly getSnapshot = (): SuiWalletData => this.state;

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private publish(next: SuiWalletData): void {
    this.state = next;
    for (const listener of this.listeners) listener();
  }

  setAddress(address: string | null): void {
    const nextAddress = address ?? "";
    if (this.state.sui_address === nextAddress) return;
    this.generation += 1;
    this.abortRequests();
    this.publish(emptyData(nextAddress));
    if (nextAddress) void this.refresh();
  }

  async refresh(): Promise<void> {
    const address = this.state.sui_address;
    if (!address || !/^0x[0-9a-fA-F]{1,64}$/.test(address)) return;
    this.publish({
      ...emptyData(address),
      balanceStatus: "loading",
      objectsStatus: "loading",
    });
    await Promise.all([
      this.loadResource("balances", null, false),
      this.loadResource("objects", null, false),
    ]);
  }

  async loadMore(resource: SuiDataResource): Promise<void> {
    const cursor = resource === "balances" ? this.state.balanceCursor : this.state.objectsCursor;
    const hasMore = resource === "balances" ? this.state.hasMoreBalances : this.state.hasMoreObjects;
    if (!this.state.sui_address || !cursor || !hasMore) return;
    await this.loadResource(resource, cursor, true);
  }

  dispose(): void {
    this.generation += 1;
    this.abortRequests();
    this.publish(emptyData());
  }

  private abortRequests(): void {
    for (const resource of ["balances", "objects"] as const) {
      this.requests[resource]?.abort();
      this.requests[resource] = null;
    }
  }

  private async loadResource(
    resource: SuiDataResource,
    cursor: string | null,
    append: boolean,
  ): Promise<void> {
    const address = this.state.sui_address;
    if (!address) return;
    this.requests[resource]?.abort();
    const controller = new AbortController();
    this.requests[resource] = controller;
    const generation = this.generation;
    const statusKey = resource === "balances" ? "balanceStatus" : "objectsStatus";
    const errorKey = resource === "balances" ? "balanceError" : "objectsError";
    this.publish({
      ...this.state,
      [statusKey]: append ? "loading-more" : "loading",
      [errorKey]: null,
      ...(append ? {} : resource === "balances"
        ? { balances: [], balanceCursor: null, hasMoreBalances: false }
        : { objects: [], objectsCursor: null, hasMoreObjects: false }),
    });

    try {
      const query = new URLSearchParams({ limit: String(PAGE_LIMIT) });
      if (cursor) query.set("cursor", cursor);
      const path = `/api/sui/wallet/${encodeURIComponent(address)}/${resource}?${query.toString()}`;
      const response = await this.fetchImpl(path, {
        method: "GET",
        cache: "no-store",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        throw new Error("The Sui service returned an invalid response.");
      }
      if (!response.ok) {
        throw new Error(safeMessage(payload, "Sui wallet data is temporarily unavailable."));
      }
      const page = resource === "balances"
        ? parseBalances(payload, address)
        : parseObjects(payload, address);
      if (generation !== this.generation || this.requests[resource] !== controller) return;
      const itemsKey = resource;
      const cursorKey = resource === "balances" ? "balanceCursor" : "objectsCursor";
      const moreKey = resource === "balances" ? "hasMoreBalances" : "hasMoreObjects";
      const currentItems = this.state[itemsKey];
      this.publish({
        ...this.state,
        network: page.network,
        [itemsKey]: append ? [...currentItems, ...page.items] : page.items,
        [statusKey]: "ready",
        [errorKey]: null,
        [cursorKey]: page.cursor,
        [moreKey]: page.hasNextPage,
      });
    } catch (error) {
      if (controller.signal.aborted || generation !== this.generation) return;
      this.publish({
        ...this.state,
        [statusKey]: "error",
        [errorKey]: error instanceof Error ? error.message : "Sui wallet data could not be loaded.",
      });
    } finally {
      if (this.requests[resource] === controller) this.requests[resource] = null;
    }
  }
}

export function getNativeSuiBalance(balances: readonly SuiCoinBalance[]): SuiCoinBalance | null {
  return balances.find((balance) => balance.coin_type === SUI_COIN_TYPE) ?? null;
}

export function getCoinDisplayName(balance: SuiCoinBalance): string {
  if (balance.coin_type === SUI_COIN_TYPE) return "Sui";
  return balance.coin_type.split("::").at(-1) || balance.coin_type;
}

export function formatCoinBalance(balance: SuiCoinBalance): string {
  return balance.amount === null ? `${balance.raw_balance} raw units` : balance.amount;
}

export function formatCoinHolding(balance: SuiCoinBalance): string {
  const amount = formatCoinBalance(balance);
  return balance.amount !== null && balance.symbol ? `${amount} ${balance.symbol}` : amount;
}
