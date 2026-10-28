import Link from "next/link";

export default function MemoriesPage() {
  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="border border-slate-800 rounded-2xl bg-slate-900/50 p-8 text-center space-y-4">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-xl bg-sky-500/10 text-sky-400 border border-sky-500/20 text-2xl">
          🧠
        </div>
        <h1 className="text-2xl font-bold text-white">Walrus Memory Centre & Consent Manager</h1>
        <p className="text-sm text-slate-400 max-w-lg mx-auto">
          In Milestones 2 & 3, this center manages authenticated memory blobs on Walrus Mainnet, verifiable lifecycle statuses (queued/confirmed), and consent controls.
        </p>
        <div className="pt-4">
          <Link
            href="/"
            className="inline-flex items-center text-xs font-medium px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors"
          >
            &larr; Back to Dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}

