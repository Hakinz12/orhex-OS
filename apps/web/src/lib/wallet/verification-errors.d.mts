export function mapWalletVerificationError(
  code: string | null,
  status: number,
): { error: string; status: number; message: string } | null;
