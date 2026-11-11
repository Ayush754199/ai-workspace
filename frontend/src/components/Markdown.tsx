import React from "react";

/**
 * Minimal, safe Markdown renderer for assistant answers.
 * No HTML injection: everything is built from text nodes and React elements.
 */
function safeHref(url: string): string | null {
  // Only http(s) links are rendered as anchors: javascript:, data: and vbscript:
  // URLs in model output must never become clickable.
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
  } catch {
    return null;
  }
  return null;
}

function inline(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*|\[[^\]]+\]\((?:https?:\/\/|\.?\/)[^\s)]+\))/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${index++}`;
    if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("*")) {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    } else {
      const link = /\[([^\]]+)\]\(((?:https?:\/\/|\.?\/)[^\s)]+)\)/.exec(token);
      const href = link ? safeHref(link[2]) : null;
      if (link && href) {
        nodes.push(
          <a key={key} href={href} target="_blank" rel="noreferrer noopener nofollow">
            {link[1]}
          </a>,
        );
      } else nodes.push(token);
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function tableCells(line: string): string[] {
  const value = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return value.split("|").map((cell) => cell.trim());
}

function isTableSeparator(line: string): boolean {
  const cells = tableCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

export function renderMarkdown(source: string): React.ReactNode {
  const lines = (source || "").split("\n");
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  let ordered = false;
  let code: string[] | null = null;
  let skipUntil = -1;

  const flushList = () => {
    if (!list.length) return;
    const items = list.map((item, i) => <li key={`li-${blocks.length}-${i}`}>{inline(item, `li-${blocks.length}-${i}`)}</li>);
    blocks.push(ordered ? <ol key={`ol-${blocks.length}`}>{items}</ol> : <ul key={`ul-${blocks.length}`}>{items}</ul>);
    list = [];
  };

  lines.forEach((raw, lineIndex) => {
    if (lineIndex <= skipUntil) return;
    const line = raw.replace(/\s+$/, "");
    if (line.trim().startsWith("```")) {
      flushList();
      if (code === null) {
        code = [];
      } else {
        blocks.push(
          <pre key={`code-${lineIndex}`}>
            <code>{code.join("\n")}</code>
          </pre>,
        );
        code = null;
      }
      return;
    }
    if (code !== null) {
      code.push(raw);
      return;
    }
    if (line.includes("|") && lineIndex + 1 < lines.length && isTableSeparator(lines[lineIndex + 1])) {
      flushList();
      const headers = tableCells(line);
      const rows: string[][] = [];
      let end = lineIndex + 2;
      while (end < lines.length && lines[end].includes("|") && lines[end].trim()) {
        rows.push(tableCells(lines[end]));
        end += 1;
      }
      skipUntil = end - 1;
      blocks.push(
        <div className="cw-md-table-wrap" key={`table-${lineIndex}`}>
          <table>
            <thead><tr>{headers.map((cell, index) => <th key={`th-${index}`}>{inline(cell, `th-${lineIndex}-${index}`)}</th>)}</tr></thead>
            <tbody>{rows.map((row, rowIndex) => <tr key={`tr-${rowIndex}`}>{headers.map((_, cellIndex) => <td key={`td-${rowIndex}-${cellIndex}`}>{inline(row[cellIndex] || "", `td-${lineIndex}-${rowIndex}-${cellIndex}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      return;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushList();
      const level = Math.min(heading[1].length + 2, 6);
      const Tag = (level === 3 ? "h3" : level === 4 ? "h4" : level === 5 ? "h5" : "h6") as React.ElementType;
      blocks.push(<Tag key={`h-${lineIndex}`}>{inline(heading[2], `h-${lineIndex}`)}</Tag>);
      return;
    }
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      const isOrdered = Boolean(numbered);
      if (ordered !== isOrdered && list.length) flushList();
      ordered = isOrdered;
      list.push((bullet ?? numbered)![1]);
      return;
    }
    if (!line.trim()) {
      flushList();
      return;
    }
    flushList();
    blocks.push(<p key={`p-${lineIndex}`}>{inline(line, `p-${lineIndex}`)}</p>);
  });
  flushList();
  if (code !== null) {
    blocks.push(
      <pre key="code-tail">
        <code>{(code as string[]).join("\n")}</code>
      </pre>,
    );
  }
  return blocks;
}

export default renderMarkdown;
