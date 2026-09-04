/**
 * Minimal ``` fence splitting. Deliberately not a markdown parser: agent
 * output is prose with the occasional fenced block, and nothing here ever
 * produces HTML.
 */

export type Block =
  | { kind: "text"; body: string }
  | { kind: "code"; lang: string; body: string };

const FENCE = /^\s*```(.*)$/;

export function splitFences(text: string): Block[] {
  const blocks: Block[] = [];
  const lines = text.split("\n");

  let buffer: string[] = [];
  let inCode = false;
  let lang = "";

  const flush = () => {
    const body = buffer.join("\n");
    buffer = [];
    if (inCode) {
      blocks.push({ kind: "code", lang, body });
    } else if (body.trim().length > 0) {
      blocks.push({ kind: "text", body: body.replace(/^\n+|\n+$/g, "") });
    }
  };

  for (const line of lines) {
    const match = FENCE.exec(line);
    if (match) {
      flush();
      inCode = !inCode;
      lang = inCode ? match[1].trim() : "";
      continue;
    }
    buffer.push(line);
  }
  // An unterminated fence still renders as code — better than losing it.
  flush();

  return blocks;
}

/**
 * The prose of a message with fenced blocks removed. Used by the Plan tab,
 * where the agent's thought is wanted but the bash block it emitted is not.
 */
export function stripFences(text: string): string {
  return splitFences(text)
    .filter((b): b is { kind: "text"; body: string } => b.kind === "text")
    .map((b) => b.body.trim())
    .filter(Boolean)
    .join("\n\n")
    .trim();
}
