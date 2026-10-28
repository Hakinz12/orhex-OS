import { ArrowRightIcon, ShieldIcon } from "@/components/icons";
import { PageHeading } from "@/components/ui";
import { authIntegrationNotice } from "@/lib/api";

const settingsSections = [
  {
    title: "Account",
    detail: "No signed-in account is available in this preview.",
    status: "Not connected",
  },
  {
    title: "Preferences",
    detail: "Personal display and reminder preferences will be managed here.",
    status: "Not configured",
  },
  {
    title: "Connected services",
    detail: "Integration diagnostics are visible on the Home screen; account linking is not available here.",
    status: "Read-only preview",
  },
];

export default function SettingsPage() {
  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Settings"
        title="Your space, your rules."
        description="Account, preferences, and security settings will live here as those capabilities become available."
      />
      <section className="surface-card divide-y divide-[#202a34]">
        {settingsSections.map((section) => (
          <div key={section.title} className="flex flex-col gap-3 px-5 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-7">
            <div>
              <h2 className="text-[13px] font-semibold text-[#e2e8ee]">{section.title}</h2>
              <p className="mt-1 text-[11px] leading-5 text-[#8493a3]">{section.detail}</p>
            </div>
            <span className="w-fit rounded-full border border-[#2e3944] px-2.5 py-1 text-[10px] text-[#9ca9b5]">{section.status}</span>
          </div>
        ))}
      </section>
      <section className="surface-card p-5 sm:p-7">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><ShieldIcon className="h-[18px] w-[18px]" /></span>
          <div>
            <h2 className="text-[13px] font-semibold text-[#e4eaf0]">Security and browser access</h2>
            <p className="mt-2 max-w-3xl text-[11px] leading-5 text-[#8997a5]">{authIntegrationNotice}</p>
            <p className="mt-2 max-w-3xl text-[11px] leading-5 text-[#8997a5]">The browser must never receive operator tokens, store bearer credentials in local or session storage, or call protected memory routes without the authenticated server boundary.</p>
          </div>
        </div>
        <div className="mt-5 border-t border-[#202a34] pt-4">
          <p className="text-[10px] text-[#718092]">This preview does not provision users, issue credentials, or change account security.</p>
        </div>
      </section>
      <a href="https://github.com/Hakinz12/orhex-OS" target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 text-[11px] text-[#9aabba] transition-colors hover:text-[#d4e5f1]">
        Project repository <ArrowRightIcon className="h-3.5 w-3.5" />
      </a>
    </div>
  );
}
