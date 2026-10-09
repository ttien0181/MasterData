export default function MoMark({ size = 38 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 200 240" role="img" aria-label="Mo, linh vật MasterOne" fill="none">
    <ellipse cx="100" cy="229" rx="42" ry="5" fill="currentColor" opacity=".1" />
    <ellipse cx="82" cy="217" rx="16" ry="7" fill="var(--color-mo-dark)" /><ellipse cx="118" cy="217" rx="16" ry="7" fill="var(--color-mo-dark)" />
    <ellipse cx="46" cy="178" rx="9" ry="15" fill="var(--color-mo-dark)" transform="rotate(12 46 178)" /><ellipse cx="154" cy="178" rx="9" ry="15" fill="var(--color-mo-dark)" transform="rotate(-12 154 178)" />
    <rect x="58" y="148" width="84" height="64" rx="28" fill="var(--color-mo-light)" stroke="var(--color-mo-cyan)" strokeWidth="5" />
    <circle cx="92" cy="180" r="11" stroke="var(--color-mo-cyan)" strokeWidth="3.5" /><circle cx="108" cy="180" r="11" stroke="var(--color-mo-cyan)" strokeWidth="3.5" /><circle cx="100" cy="180" r="4.5" fill="var(--color-mo-gold)" />
    <rect x="88" y="138" width="24" height="14" rx="5" fill="var(--color-mo-dark)" />
    <rect x="29" y="84" width="14" height="30" rx="7" fill="var(--color-mo-dark)" /><rect x="157" y="84" width="14" height="30" rx="7" fill="var(--color-mo-dark)" />
    <path d="M100 52V32" stroke="var(--color-mo-cyan)" strokeWidth="5" strokeLinecap="round" /><circle cx="100" cy="24" r="10" fill="var(--color-mo-gold)" /><circle cx="96.5" cy="20.5" r="3" fill="white" opacity=".7" />
    <rect x="38" y="50" width="124" height="94" rx="36" fill="var(--color-mo-cyan)" /><rect x="51" y="64" width="98" height="64" rx="26" fill="var(--color-mo-screen)" />
    <ellipse cx="80" cy="94" rx="8" ry="11" fill="var(--color-mo-eye)" /><ellipse cx="120" cy="94" rx="8" ry="11" fill="var(--color-mo-eye)" />
    <circle cx="83" cy="89" r="3" fill="white" /><circle cx="123" cy="89" r="3" fill="white" />
    <path d="M89 113q11 9 22 0" stroke="var(--color-mo-eye)" strokeWidth="3.5" strokeLinecap="round" />
  </svg>;
}
