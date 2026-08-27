export function TguiIcon({
  name,
  size = 28,
  className,
}: {
  name: string;
  size?: 16 | 20 | 24 | 28 | 32 | 36;
  className?: string;
}) {
  return (
    <span
      className={className}
      style={{
        display: "inline-block",
        width: size,
        height: size,
        flexShrink: 0,
        backgroundColor: "currentColor",
        WebkitMask: `url(/tgui-icons/${size}/${name}.svg) center / contain no-repeat`,
        mask: `url(/tgui-icons/${size}/${name}.svg) center / contain no-repeat`,
      }}
      aria-hidden
    />
  );
}
