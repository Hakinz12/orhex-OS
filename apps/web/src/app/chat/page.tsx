import Link from "next/link";

export default function ChatPage() {
  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="border border-slate-800 rounded-2xl bg-slate-900/50 p-8 text-center space-y-4">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 text-2xl">
          💬
        </div>
        <h1 className="text-2xl font-bold text-white">Contextual Chat & Memory Orchestrator</h1>
        <p className="text-sm text-slate-400 max-w-lg mx-auto">
          In Milestone 4, this interface connects conversational input with real-time semantic memory recall from Walrus Memory and proactive recommendations from Google Gemini.
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

