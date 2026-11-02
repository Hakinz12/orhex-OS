"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { createSuiWalletAdapter } from "./sui";
import type { WalletConnectionAdapter, WalletConnectionViewModel } from "./types";

export function useWalletConnection(adapter?: WalletConnectionAdapter) {
  const instance = useMemo(() => adapter ?? createSuiWalletAdapter(), [adapter]);
  const ownsAdapter = adapter === undefined;
  const [viewModel, setViewModel] = useState<WalletConnectionViewModel>(() => instance.getViewModel());

  useEffect(() => {
    const unsubscribeAccount = instance.onAccountChange(() => {
      setViewModel(instance.getViewModel());
    });
    const unsubscribeConnection = instance.onConnectionChange((next) => {
      setViewModel(next);
    });
    const stop = instance.start();
    setViewModel(instance.getViewModel());

    return () => {
      unsubscribeAccount();
      unsubscribeConnection();
      stop();
      if (ownsAdapter) instance.dispose();
    };
  }, [instance, ownsAdapter]);

  const connect = useCallback(async () => {
    await instance.connect({ silent: false });
    setViewModel(instance.getViewModel());
  }, [instance]);

  const disconnect = useCallback(async () => {
    await instance.disconnect();
    setViewModel(instance.getViewModel());
  }, [instance]);

  return {
    ...viewModel,
    shortAddress: viewModel.address ? formatAddress(viewModel.address) : null,
    connect,
    disconnect,
  };
}

export function formatAddress(address: string, visible = 4): string {
  if (!address) return "";
  if (address.length <= visible * 2 + 2) return address;
  const prefix = address.slice(0, visible);
  const suffix = address.slice(-visible);
  return `${prefix}…${suffix}`;
}
