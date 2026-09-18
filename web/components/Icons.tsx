interface IconProps {
  size?: number;
}

function svgProps(size = 16) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
}

export function MailIcon({ size }: IconProps) {
  return (
    <svg {...svgProps(size)}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3.5 6.5 8.5 6.5 8.5-6.5" />
    </svg>
  );
}

export function PaperclipIcon({ size }: IconProps) {
  return (
    <svg {...svgProps(size)}>
      <path d="M21 11.5 12.5 20a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7L10.2 17.7a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8" />
    </svg>
  );
}

export function ReplyIcon({ size }: IconProps) {
  return (
    <svg {...svgProps(size)}>
      <path d="M9 14 4 9l5-5" />
      <path d="M4 9h11a5 5 0 0 1 5 5v6" />
    </svg>
  );
}

const FILE_KINDS: [string[], string, string][] = [
  [["xlsx", "xlsm", "xls", "csv"], "XLS", "#1d6f42"],
  [["pdf"], "PDF", "#c0392b"],
  [["png", "jpg", "jpeg", "webp", "gif", "heic"], "IMG", "#7b4fb8"],
  [["docx", "doc"], "DOC", "#2b579a"],
];

export function FileTypeIcon({ name }: { name: string }) {
  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  const [, label, color] = FILE_KINDS.find(([extensions]) => extensions.includes(extension)) ?? [[], "FILE", "#5f6b7a"];
  return (
    <span className="file-type" style={{ background: color }}>
      {label}
    </span>
  );
}
