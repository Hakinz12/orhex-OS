# ORHEX Advanced Blockchain Intelligence v1

## Scope

This release adds an authenticated, read-only Sui wallet intelligence snapshot using the existing Tatum GraphQL adapter. It combines a bounded page of token balances, up to 20 recent sent transactions, and the latest verified network checkpoint.

## API

`GET /api/v1/sui/intelligence/{address}`

The endpoint requires the existing ORHEX bearer session and uses the existing Sui rate limiter. It performs three independently verified provider reads concurrently. Successful reads are retained if another read fails; the response reports `data_status` (`complete`, `partial`, or `unavailable`), `unavailable_sections`, and safe provider error codes. Unavailable component values are `null`; a successful empty result remains an empty collection. It returns:

- network, wallet address, fetch time, and latest checkpoint;
- token holdings only where Tatum returned decimal metadata;
- recent sent transaction outcomes (success/failure/unknown);
- deterministic heuristic signals for failed recent transactions;
- explicit limitations.

Provider authentication and transport failures are never represented as zero balances,
empty activity, or empty holdings.

The checkpoint is an independent provider read, not a consistency guarantee for
the balances and activity returned alongside it. Each provider response is
checked against the configured mainnet or testnet chain identifier. Devnet is
rejected until its expected chain identity can be verified.

## Interpretation and safety

- This is currently Sui-only; it does not claim cross-chain coverage.
- Amounts are token quantities, not USD values. Different token units are not economically comparable; they are neither summed nor ranked across token types. Portfolio concentration is not calculated without comparable valuations.
- Activity is a bounded recent sent-transaction sample, not a complete history and not a counterparty risk graph.
- Failed transaction indicators are review signals, not fraud or investment advice.
- No private keys, signing, transaction construction, transfers, or smart-contract writes are involved.
- The endpoint is request-triggered and does not run background monitoring.

## Frontend

The Blockchain Intelligence page is available at `/blockchain` and uses the connected Sui wallet. The Next.js BFF proxies the request through the existing authenticated session and does not cache the response.

## Verification

Backend unit tests cover deterministic signal generation, unknown/empty activity handling, and the absence of invented fiat valuation. Run from `services/api`:

```powershell
python -m pytest tests/test_blockchain_intelligence.py -q
```

Also run the full API suite and from `apps/web` run `npm run test:bff`, `npm run lint`, and `npm run build` before deployment.
