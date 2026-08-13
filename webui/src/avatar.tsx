export function hueOf(name: string): number {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) % 360;
  }
  return hash;
}

export function Avatar({
  name,
  size = 20,
  square = false,
}: {
  name: string;
  size?: number;
  square?: boolean;
}) {
  const hue = hueOf(name);
  return (
    <span
      className="avatar"
      style={{
        width: size,
        height: size,
        fontSize: size * (square ? 0.46 : 0.52),
        borderRadius: square ? size * 0.3 : "50%",
        background: square
          ? `linear-gradient(150deg, hsl(${hue}, 46%, 52%), hsl(${hue}, 52%, 40%))`
          : `hsl(${hue}, 48%, 90%)`,
        color: square ? "#fff" : `hsl(${hue}, 45%, 32%)`,
        border: square ? "none" : `1px solid hsl(${hue}, 40%, 80%)`,
        fontWeight: square ? 800 : 600,
      }}
    >
      {(name || "?").slice(0, 1)}
    </span>
  );
}
