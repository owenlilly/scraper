/** The logo: a sheet of ruled lines with one line highlighted, the same highlighter the workspace uses for "the model used this". */
export function Mark({ size = 24 }: { size?: number }) {
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <rect className="sheet" x="3.5" y="2.5" width="17" height="19" rx="3.5" strokeWidth="1.6" />
      <rect className="swipe" x="6" y="10" width="12" height="4.6" rx="1" />
      <path className="line" d="M7.5 7h6M7.5 12.3h9M7.5 17h5" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
