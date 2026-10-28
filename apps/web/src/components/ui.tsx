import type { ReactNode } from "react";

export function PageHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
      <div className="max-w-2xl">
        <p className="eyebrow">{eyebrow}</p>
        <h1 className="font-display mt-3 text-[30px] font-semibold tracking-[-0.04em] text-[#f0f3f6] sm:text-[38px]">
          {title}
        </h1>
        <p className="mt-2 max-w-xl text-[13px] leading-6 text-[#93a1b0] sm:text-sm">{description}</p>
      </div>
      {action}
    </div>
  );
}

export function SectionHeading({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <h2 className="font-display text-[15px] font-semibold text-[#e9eef3]">{title}</h2>
        {detail && <p className="mt-1 text-[11px] text-[#778697]">{detail}</p>}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({
  title,
  children,
  icon,
}: {
  title: string;
  children: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex min-h-[190px] flex-col items-center justify-center px-6 py-9 text-center">
      {icon && <div className="mb-4 text-[#8fa6b9]">{icon}</div>}
      <h3 className="text-[13px] font-semibold text-[#dce3e9]">{title}</h3>
      <div className="mt-2 max-w-sm text-[12px] leading-5 text-[#8493a3]">{children}</div>
    </div>
  );
}

export function StatusBadge({ status }: { status?: string }) {
  const label = status?.replaceAll("_", " ") || "not checked";
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-[#293440] bg-[#0e141b] px-2.5 py-1 text-[10px] capitalize text-[#aab6c1]">
      <span className="status-dot" data-status={status || "unknown"} />
      {label}
    </span>
  );
}

export function IntegrationStatus({
  name,
  provider,
  status,
  detail,
  icon,
}: {
  name: string;
  provider: string;
  status?: string;
  detail: string;
  icon: ReactNode;
}) {
  return (
    <article className="surface-card flex min-h-[146px] flex-col justify-between p-5">
      <div className="flex items-start justify-between gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#2b3946] bg-[#17212b] text-[#b8d2e5]">
          {icon}
        </span>
        <StatusBadge status={status} />
      </div>
      <div className="mt-5">
        <p className="text-[13px] font-semibold text-[#e8edf2]">{name}</p>
        <p className="mt-1 text-[11px] text-[#8291a1]">{provider} · {detail}</p>
      </div>
    </article>
  );
}
