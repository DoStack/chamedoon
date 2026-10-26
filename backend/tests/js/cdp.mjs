// Tiny Chrome DevTools Protocol driver: no npm packages, Node 22+ (global WebSocket and fetch).
// Starts a headless Chrome, opens one page and exposes send/evaluate plus a few input helpers.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CANDIDATES = [
  process.env.CHROME_BIN,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);

export function findChrome() {
  return CANDIDATES.find((path) => existsSync(path)) || null;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function launch({ width = 390, height = 844, mobile = true } = {}) {
  const bin = findChrome();
  if (!bin) throw new Error("Chrome not found; set CHROME_BIN");
  if (typeof WebSocket !== "function") throw new Error("Needs a global WebSocket: Node 22+, or node --experimental-websocket");
  const profile = mkdtempSync(join(tmpdir(), "gallery-chrome-"));
  const proc = spawn(
    bin,
    [
      "--headless=new",
      "--remote-debugging-port=0",
      `--user-data-dir=${profile}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-gpu",
      "--allow-file-access-from-files",
      // CI runners (Ubuntu 24.04) block the user namespaces Chrome's sandbox needs.
      ...(process.env.CI ? ["--no-sandbox"] : []),
      "about:blank",
    ],
    { stdio: "ignore" }
  );
  let port = null;
  for (let i = 0; i < 100 && !port; i += 1) {
    await sleep(100);
    try {
      port = readFileSync(join(profile, "DevToolsActivePort"), "utf8").split(/\r?\n/)[0];
    } catch {}
  }
  if (!port) throw new Error("Chrome did not start");
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = targets.find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
  });
  let nextId = 0;
  const waiting = new Map();
  const listeners = [];
  ws.onmessage = (message) => {
    const data = JSON.parse(message.data);
    if (data.id && waiting.has(data.id)) {
      const { resolve, reject } = waiting.get(data.id);
      waiting.delete(data.id);
      if (data.error) reject(new Error(data.error.message));
      else resolve(data.result);
    } else if (data.method) {
      listeners.forEach((fn) => fn(data));
    }
  };
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      nextId += 1;
      waiting.set(nextId, { resolve, reject });
      ws.send(JSON.stringify({ id: nextId, method, params }));
    });
  const errors = [];
  listeners.push((event) => {
    if (event.method === "Runtime.exceptionThrown") errors.push(event.params.exceptionDetails);
  });
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 2, mobile });

  async function goto(url) {
    const loaded = new Promise((resolve) => {
      const fn = (event) => {
        if (event.method === "Page.loadEventFired") {
          listeners.splice(listeners.indexOf(fn), 1);
          resolve();
        }
      };
      listeners.push(fn);
    });
    await send("Page.navigate", { url });
    await loaded;
    await sleep(150);
  }

  async function evaluate(expression) {
    const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || "evaluate failed");
    return result.result.value;
  }

  async function key(name, code = name, keyCode = 0, modifiers = 0) {
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: name, code, windowsVirtualKeyCode: keyCode, modifiers });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: name, code, windowsVirtualKeyCode: keyCode, modifiers });
  }

  // A one finger horizontal drag made of real touch events (pointerType "touch").
  async function swipe(x1, y, x2, steps = 8) {
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: x1, y }] });
    for (let i = 1; i <= steps; i += 1) {
      const x = x1 + ((x2 - x1) * i) / steps;
      await send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y }] });
      await sleep(16);
    }
    await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await sleep(350);
  }

  async function tap(x, y) {
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  }

  async function screenshot() {
    const { data } = await send("Page.captureScreenshot", { format: "png" });
    return Buffer.from(data, "base64");
  }

  async function close() {
    try {
      ws.close();
    } catch {}
    proc.kill();
    await sleep(300);
    try {
      rmSync(profile, { recursive: true, force: true });
    } catch {}
  }

  return { send, goto, evaluate, key, swipe, tap, screenshot, close, errors, sleep };
}
