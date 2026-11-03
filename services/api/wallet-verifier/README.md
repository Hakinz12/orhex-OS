# Sui signature verifier

The API invokes this isolated Node.js module for server-side Sui personal-message
verification. It uses the official `@mysten/sui` verifier and is intentionally
separate from the frontend dependency tree.

API deployment must provide Node.js 20 or newer and install this package before
starting FastAPI:

```powershell
cd services/api/wallet-verifier
npm ci --omit=dev
```

The package lock pins the SDK and its transitive dependencies. The API fails
closed with a service-unavailable response if the verifier runtime is absent.
