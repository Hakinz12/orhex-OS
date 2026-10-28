import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function IconBase({ children, ...props }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.7"
      viewBox="0 0 24 24"
      {...props}
    >
      {children}
    </svg>
  );
}

export function MarkIcon(props: IconProps) {
  return (
    <IconBase {...props}>
      <path d="M5 18V6l7 8 7-8v12" />
      <path d="M9 18l3-4 3 4" />
    </IconBase>
  );
}

export function HomeIcon(props: IconProps) {
  return <IconBase {...props}><path d="m3 10 9-7 9 7" /><path d="M5 9v11h14V9M9 20v-6h6v6" /></IconBase>;
}

export function SparkIcon(props: IconProps) {
  return <IconBase {...props}><path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7L12 3Z" /><path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z" /></IconBase>;
}

export function MemoryIcon(props: IconProps) {
  return <IconBase {...props}><path d="M9 4.5a3 3 0 0 0-5 2.2v.6a3 3 0 0 0 0 5.4v.6a3 3 0 0 0 5 2.2M15 4.5a3 3 0 0 1 5 2.2v.6a3 3 0 0 1 0 5.4v.6a3 3 0 0 1-5 2.2M9 4v16M15 4v16M9 8h3m-3 4h3m0 4h3m0-8h-3m3 4h-3" /></IconBase>;
}

export function TimelineIcon(props: IconProps) {
  return <IconBase {...props}><path d="M5 5h14M5 12h14M5 19h14" /><circle cx="3" cy="5" r="1" /><circle cx="3" cy="12" r="1" /><circle cx="3" cy="19" r="1" /></IconBase>;
}

export function InsightsIcon(props: IconProps) {
  return <IconBase {...props}><path d="M4 19V5m0 14h16" /><path d="m7 15 4-4 3 2 5-6" /><path d="M16 7h3v3" /></IconBase>;
}

export function SettingsIcon(props: IconProps) {
  return <IconBase {...props}><path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z" /><path d="m19.4 15 .1.1 1.2 1-1.2 2-1.5-.6a8 8 0 0 1-1.6.9l-.3 1.6h-2.4l-.3-1.6a8 8 0 0 1-1.6-.9l-1.5.6-1.2-2 1.2-1a7 7 0 0 1 0-1.9l-1.2-1 1.2-2 1.5.6a8 8 0 0 1 1.6-.9l.3-1.6h2.4l.3 1.6a8 8 0 0 1 1.6.9l1.5-.6 1.2 2-1.2 1a7 7 0 0 1 0 1.9Z" /></IconBase>;
}

export function SearchIcon(props: IconProps) {
  return <IconBase {...props}><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 5 5" /></IconBase>;
}

export function BellIcon(props: IconProps) {
  return <IconBase {...props}><path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4" /></IconBase>;
}

export function ArrowUpRightIcon(props: IconProps) {
  return <IconBase {...props}><path d="M7 17 17 7M8 7h9v9" /></IconBase>;
}

export function ArrowRightIcon(props: IconProps) {
  return <IconBase {...props}><path d="M4 12h15m-6-6 6 6-6 6" /></IconBase>;
}

export function PlusIcon(props: IconProps) {
  return <IconBase {...props}><path d="M12 5v14M5 12h14" /></IconBase>;
}

export function MenuIcon(props: IconProps) {
  return <IconBase {...props}><path d="M4 7h16M4 12h16M4 17h16" /></IconBase>;
}

export function CloseIcon(props: IconProps) {
  return <IconBase {...props}><path d="m6 6 12 12M18 6 6 18" /></IconBase>;
}

export function ClockIcon(props: IconProps) {
  return <IconBase {...props}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></IconBase>;
}

export function ShieldIcon(props: IconProps) {
  return <IconBase {...props}><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z" /><path d="m9 12 2 2 4-4" /></IconBase>;
}

export function CompassIcon(props: IconProps) {
  return <IconBase {...props}><circle cx="12" cy="12" r="9" /><path d="m15.5 8.5-2.3 4.7-4.7 2.3 2.3-4.7 4.7-2.3Z" /></IconBase>;
}

export function MessageIcon(props: IconProps) {
  return <IconBase {...props}><path d="M20 11.5a7.5 7.5 0 0 1-8 7.5 9 9 0 0 1-4-.9L4 20l1.2-3.1A7.2 7.2 0 0 1 4 12a7.5 7.5 0 0 1 8-7.5 7.5 7.5 0 0 1 8 7Z" /><path d="M8 12h8m-8 3h5" /></IconBase>;
}

export function WalrusIcon(props: IconProps) {
  return <IconBase {...props}><path d="M3 7c3 0 3 10 6 10S12 7 15 7s3 10 6 10" /><path d="M3 12c3 0 3-5 6-5s3 10 6 10 3-5 6-5" /></IconBase>;
}

export function ChainIcon(props: IconProps) {
  return <IconBase {...props}><path d="M10 13.5a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.7 1.7" /><path d="M14 10.5a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 19.2l1.7-1.7" /></IconBase>;
}
