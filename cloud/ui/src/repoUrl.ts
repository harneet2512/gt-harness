/* ------------------------------------------------------------------ *
 * Reading a repository out of what the reader typed.
 *
 * "Fix the flaky test in https://github.com/pallets/click" is a complete
 * instruction — it names the work and the repository — and the page must
 * not answer it with a form. Nothing is removed from the message: the URL
 * is a fact the agent may want too.
 * ------------------------------------------------------------------ */

export interface RepoRef {
  /** Canonical clone URL: `https://github.com/owner/name`. */
  repo: string;
  /** Branch, tag or sha the message named, or null when it named none. */
  ref: string | null;
}

/* `https://github.com/owner/name` plus whatever follows it, up to the first
   character that cannot be part of a URL. The tail is parsed separately:
   `/tree/<ref>` and `@<ref>` are the two ways a ref travels in a link. */
const GITHUB = /https?:\/\/(?:www\.)?github\.com\/([A-Za-z0-9._-]+)\/([A-Za-z0-9._-]+)([^\s)>\]"'`]*)/;

/** Trailing sentence punctuation is not part of a branch name. */
const TRAILING = /[.,;:!?)\]}>'"`]+$/;

function cleanRef(value: string): string | null {
  const ref = value.replace(/\/+$/, "").replace(TRAILING, "").trim();
  return ref === "" ? null : ref;
}

/**
 * The first GitHub repository named anywhere in `text`, or null.
 *
 * Accepts the plain repo URL, a `/tree/<ref>` deep link (including refs with
 * slashes, like `cloud/internal-harness`), and the `owner/name@ref` shorthand
 * people paste when they mean a branch.
 */
export function parseRepoRef(text: string): RepoRef | null {
  const match = GITHUB.exec(text ?? "");
  if (!match) return null;

  const owner = match[1];
  /* The name pattern accepts dots, so it swallows both the `.git` suffix
     and the full stop that ended the sentence. Sentence first, then git. */
  const name = match[2].replace(TRAILING, "").replace(/\.git$/i, "");
  if (!owner || !name) return null;

  const tail = match[3] ?? "";
  let ref: string | null = null;
  if (tail.startsWith("@")) {
    ref = cleanRef(tail.slice(1));
  } else if (tail.startsWith("/tree/")) {
    ref = cleanRef(tail.slice("/tree/".length));
  } else if (tail.startsWith("/")) {
    /* /pull/12, /issues/3, /blob/main/... — a repository we know, at a
       location we are not going to guess a ref from. */
    const blob = /^\/blob\/([^/]+)\//.exec(tail);
    ref = blob ? cleanRef(blob[1]) : null;
  }

  return { repo: `https://github.com/${owner}/${name}`, ref };
}

/** True when this looks like a repository URL and nothing else. */
export function isRepoUrl(text: string): boolean {
  const trimmed = text.trim();
  if (trimmed.includes(" ") || trimmed.includes("\n")) return false;
  return parseRepoRef(trimmed) !== null;
}

/**
 * The chip's label: `owner/name @ ref`, with the ref left off where there
 * is none to show.
 */
export function repoChipLabel(repo: string, ref: string | null): string {
  const short = repo
    .replace(/^git@[^:]+:/, "")
    .replace(/^https?:\/\/[^/]+\//, "")
    .replace(/\.git$/, "");
  return ref ? `${short} @ ${ref}` : short;
}
