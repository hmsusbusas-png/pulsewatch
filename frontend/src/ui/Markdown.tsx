import type { ReactNode } from "react";

function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**"))
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`"))
      return (
        <code key={index} className="mono rounded bg-panelHover px-1">
          {part.slice(1, -1)}
        </code>
      );
    return part;
  });
}

type Block =
  | { kind: "heading"; level: 3 | 4; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "paragraph"; text: string };

function parse(source: string): Block[] {
  const blocks: Block[] = [];
  for (const line of source.replace(/\r/g, "").split("\n")) {
    const trimmed = line.trim();
    const last = blocks[blocks.length - 1];
    if (!trimmed) {
      blocks.push({ kind: "paragraph", text: "" });
      continue;
    }
    const heading = trimmed.match(/^(#{2,3})\s+(.*)$/);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length === 2 ? 3 : 4, text: heading[2] });
      continue;
    }
    const bullet = trimmed.match(/^[-*]\s+(.*)$/);
    const numbered = trimmed.match(/^\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      const ordered = Boolean(numbered);
      const text = (bullet ?? numbered)?.[1] ?? "";
      if (last?.kind === "list" && last.ordered === ordered) last.items.push(text);
      else blocks.push({ kind: "list", ordered, items: [text] });
      continue;
    }
    if (last?.kind === "paragraph" && last.text) last.text = `${last.text} ${trimmed}`;
    else blocks.push({ kind: "paragraph", text: trimmed });
  }
  return blocks.filter((block) => block.kind !== "paragraph" || block.text);
}

export function Markdown({ source }: { source: string }) {
  return (
    <div className="grid gap-2.5 text-[13.5px] leading-relaxed">
      {parse(source).map((block, index) => {
        if (block.kind === "heading")
          return block.level === 3 ? (
            <h3 key={index} className="mt-2 text-[14px] font-semibold first:mt-0">
              {inline(block.text)}
            </h3>
          ) : (
            <h4 key={index} className="mt-1 text-[13.5px] font-semibold">
              {inline(block.text)}
            </h4>
          );
        if (block.kind === "list") {
          const Tag = block.ordered ? "ol" : "ul";
          return (
            <Tag
              key={index}
              className={`grid gap-1 pl-5 ${block.ordered ? "list-decimal" : "list-disc"}`}
            >
              {block.items.map((item, position) => (
                <li key={position}>{inline(item)}</li>
              ))}
            </Tag>
          );
        }
        return (
          <p key={index} className="text-inkMuted">
            {inline(block.text)}
          </p>
        );
      })}
    </div>
  );
}
