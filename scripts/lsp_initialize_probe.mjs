#!/usr/bin/env node
/**
 * Bounded LSP liveness probe for staged language servers.
 *
 * The paid workflows stage language servers into a tree uploaded to the task
 * container, where a shim like
 *
 *   exec "<runtime>/bin/node" "<runtime>/servers/node_modules/<pkg>" "$@"
 *
 * is the only launch surface. File-existence checks cannot see a server that
 * starts and immediately exits: typescript-language-server@6.0.0 exits 1 on
 * the --tsserver-path flag an earlier shim injected (run 34801009507's LSP
 * receipt: `error: unknown option '--tsserver-path'`), leaving JS/TS legs
 * unserviceable while staging looked green. This probe renders the exact
 * shim with host paths substituted, launches it with the real server
 * arguments, speaks one LSP `initialize` request over stdio, and requires a
 * well-formed response inside a bounded time. A server that cannot
 * initialize fails the staging step, before a paid task discovers it.
 *
 * Usage:
 *   node lsp_initialize_probe.mjs --shim PATH [--prefix-map FROM=TO]...
 *                                 [--timeout-ms N] [--probe-name NAME]
 *                                 -- [server args...]
 *   node lsp_initialize_probe.mjs --command PATH -- [server args...]
 *
 * --shim renders the given shim file, rewriting every --prefix-map FROM
 * string to TO (literal, in order), writes the result to a temp file, and
 * executes it. --command executes the given file directly (no render); used
 * for local verification where /bin/sh shims cannot run.
 *
 * Exit 0 = the server answered initialize with a capabilities object.
 * Exit 1 = spawn failure, early exit, timeout, protocol violation, or an
 *         initialize error response. Exit 2 = bad arguments.
 */
import { spawn } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const DEFAULT_TIMEOUT_MS = 30000;

function die_usage(message) {
  console.error(`lsp_initialize_probe: ${message}`);
  console.error(
    "usage: node lsp_initialize_probe.mjs (--shim PATH [--prefix-map FROM=TO]... | --command PATH) [--timeout-ms N] [--probe-name NAME] -- [server args...]"
  );
  process.exit(2);
}

const argv = process.argv.slice(2);
const separator = argv.indexOf("--");
const probeArgs = separator === -1 ? argv : argv.slice(0, separator);
const serverArgs = separator === -1 ? [] : argv.slice(separator + 1);

let shimPath = "";
let commandPath = "";
let timeoutMs = DEFAULT_TIMEOUT_MS;
let probeName = "";
const prefixMaps = [];

for (let i = 0; i < probeArgs.length; i += 1) {
  const flag = probeArgs[i];
  const next = () => {
    i += 1;
    if (i >= probeArgs.length) die_usage(`${flag} requires a value`);
    return probeArgs[i];
  };
  if (flag === "--shim") shimPath = next();
  else if (flag === "--command") commandPath = next();
  else if (flag === "--timeout-ms") timeoutMs = Number(next());
  else if (flag === "--probe-name") probeName = next();
  else if (flag === "--prefix-map") {
    const spec = next();
    const eq = spec.indexOf("=");
    if (eq <= 0) die_usage(`--prefix-map expects FROM=TO, got ${spec}`);
    prefixMaps.push([spec.slice(0, eq), spec.slice(eq + 1)]);
  } else die_usage(`unknown argument ${flag}`);
}

if (shimPath && commandPath) die_usage("--shim and --command are exclusive");
if (!shimPath && !commandPath) die_usage("one of --shim or --command is required");
if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
  die_usage(`--timeout-ms must be a positive number, got ${timeoutMs}`);
}

let launchPath = commandPath;
let workDir = "";
if (shimPath) {
  let rendered;
  try {
    rendered = readFileSync(shimPath, "utf8");
  } catch (error) {
    console.error(`lsp_initialize_probe: cannot read shim ${shimPath}: ${error.message}`);
    process.exit(1);
  }
  for (const [from, to] of prefixMaps) {
    rendered = rendered.split(from).join(to);
  }
  workDir = mkdtempSync(join(tmpdir(), "lsp-probe-"));
  launchPath = join(workDir, "rendered-shim");
  writeFileSync(launchPath, rendered);
  chmodSync(launchPath, 0o755);
}

const label = probeName || shimPath || commandPath;
let stdoutBuf = Buffer.alloc(0);
const stderrTail = [];
let stderrBytes = 0;
let childExitInfo = null;
let settled = false;
let deadline = null;

// Rendered shims run through sh so the check does not depend on the exec
// bit surviving to a temp file; --command paths ending in .js/.mjs run
// through this same node binary, exactly as the staged shims invoke them.
let childExe = launchPath;
let childArgv = serverArgs;
if (shimPath) {
  childExe = "sh";
  childArgv = [launchPath, ...serverArgs];
} else if (/\.m?js$/i.test(launchPath)) {
  childExe = process.execPath;
  childArgv = [launchPath, ...serverArgs];
}
const child = spawn(childExe, childArgv, {
  stdio: ["pipe", "pipe", "pipe"],
  cwd: workDir || undefined,
});

function stderrText() {
  const text = Buffer.concat(stderrTail).toString("utf8");
  return text.length > 2000 ? `...${text.slice(-2000)}` : text;
}

function finish(code, message) {
  if (settled) return;
  settled = true;
  if (deadline !== null) clearTimeout(deadline);
  try {
    child.kill("SIGKILL");
  } catch {
    // Already gone.
  }
  console.error(`lsp_initialize_probe[${label}]: ${message}`);
  process.exit(code);
}

function succeed(serverInfo) {
  if (settled) return;
  // The handshake is proven; shut the server down gracefully but never let a
  // stubborn server hold the staging step open.
  try {
    sendFrame({ jsonrpc: "2.0", method: "initialized", params: {} });
    sendFrame({ jsonrpc: "2.0", id: 2, method: "shutdown" });
    sendFrame({ jsonrpc: "2.0", method: "exit" });
    child.stdin.end();
  } catch {
    // Teardown is best-effort; liveness is already established.
  }
  const info = serverInfo
    ? ` (${serverInfo.name || "?"} ${serverInfo.version || ""})`.trimEnd()
    : "";
  console.log(`lsp_initialize_probe[${label}]: initialize answered${info}`);
  finish(0, `initialize answered${info}`);
}

function sendFrame(message) {
  const body = JSON.stringify(message);
  child.stdin.write(`Content-Length: ${Buffer.byteLength(body, "utf8")}\r\n\r\n${body}`);
}

function onMessage(message) {
  // Anything carrying a method is a server->client request or notification,
  // never our initialize response - a server request can reuse numeric id 1.
  if (message.method) return;
  if (message.id !== 1) return;
  if (message.error) {
    const detail = message.error.message || JSON.stringify(message.error);
    finish(1, `initialize returned error ${message.error.code}: ${detail}`);
    return;
  }
  const result = message.result;
  if (!result || typeof result !== "object" || typeof result.capabilities !== "object" || result.capabilities === null) {
    finish(1, `initialize response lacks a capabilities object: ${JSON.stringify(result).slice(0, 300)}`);
    return;
  }
  succeed(result.serverInfo);
}

function drainFrames() {
  for (;;) {
    const headerEnd = stdoutBuf.indexOf("\r\n\r\n");
    if (headerEnd === -1) return;
    const header = stdoutBuf.subarray(0, headerEnd).toString("utf8");
    const match = /Content-Length:\s*(\d+)/i.exec(header);
    if (!match) {
      finish(1, `malformed LSP frame header: ${header.slice(0, 120)}`);
      return;
    }
    const length = Number(match[1]);
    const frameEnd = headerEnd + 4 + length;
    if (stdoutBuf.length < frameEnd) return;
    const body = stdoutBuf.subarray(headerEnd + 4, frameEnd).toString("utf8");
    stdoutBuf = stdoutBuf.subarray(frameEnd);
    let message;
    try {
      message = JSON.parse(body);
    } catch {
      finish(1, `invalid JSON-RPC body: ${body.slice(0, 200)}`);
      return;
    }
    onMessage(message);
    if (settled) return;
  }
}

child.stdout.on("data", (chunk) => {
  stdoutBuf = Buffer.concat([stdoutBuf, chunk]);
  drainFrames();
});
child.stderr.on("data", (chunk) => {
  stderrBytes += chunk.length;
  stderrTail.push(chunk);
  if (stderrBytes > 65536) stderrTail.shift();
});
child.on("error", (error) => {
  finish(1, `spawn failed: ${error.message}`);
});
child.on("exit", (code, signal) => {
  childExitInfo = { code, signal };
  if (!settled) {
    const tail = stderrText();
    finish(
      1,
      `server exited before answering initialize (code=${code} signal=${signal})` +
        (tail ? ` stderr: ${tail}` : "")
    );
  }
});

deadline = setTimeout(() => {
  const tail = stderrText();
  finish(
    1,
    `timed out after ${timeoutMs}ms waiting for an initialize response` +
      (tail ? ` stderr: ${tail}` : "") +
      (childExitInfo ? ` exit=${JSON.stringify(childExitInfo)}` : "")
  );
}, timeoutMs);

sendFrame({
  jsonrpc: "2.0",
  id: 1,
  method: "initialize",
  params: {
    processId: null,
    rootUri: null,
    workspaceFolders: null,
    capabilities: {},
    initializationOptions: {},
  },
});
