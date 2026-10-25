// Headless browser check of miniapp/static/miniapp/gallery.js.
// Run: node backend/tests/js/gallery_check.mjs   (needs Node 22+ and a local Chrome; CHROME_BIN to override)
// The fixture mirrors the gallery markup in templates/miniapp/homes_detail.html.
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { launch } from "./cdp.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = resolve(here, "../../miniapp/static/miniapp");
const COUNT = 6;
const COLORS = ["#C8553D", "#2E86AB", "#6A994E", "#F2A541", "#7D5BA6", "#33658A"];

function photo(n) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"><rect width="800" height="600" fill="${COLORS[n]}"/><text x="400" y="340" font-size="160" text-anchor="middle" fill="#fff">${n + 1}</text></svg>`;
  return "data:image/svg+xml," + encodeURIComponent(svg);
}

function fixture(locale) {
  const rtl = locale === "fa";
  const fa = (v) => (rtl ? String(v).replace(/[0-9]/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[d]) : String(v));
  const badge = rtl ? "{i} از {n}" : "{i} of {n}";
  const items = [];
  const slides = [];
  for (let i = 0; i < COUNT; i += 1) {
    items.push(
      `<button class="house-gallery-item" type="button" data-gallery-open="${i}" aria-label="open ${i + 1}" aria-haspopup="dialog"><img src="${photo(i)}" alt="photo ${i + 1}"></button>`
    );
    slides.push(
      `<figure class="gallery-slide" data-gallery-slide="${i}" aria-hidden="true"><img class="gallery-img" data-src="${photo(i)}" alt="photo ${i + 1}" draggable="false"></figure>`
    );
  }
  return `<!DOCTYPE html><html lang="${locale}" dir="${rtl ? "rtl" : "ltr"}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${pathToFileURL(join(staticDir, "app.css"))}"></head><body><div class="wrap">
<div class="house-gallery-wrap" data-gallery-strip data-locale="${locale}" data-badge-format="${badge}">
<div class="house-gallery">${items.join("")}</div>
<span class="house-gallery-badge" data-gallery-badge aria-hidden="true">${badge.replace("{i}", fa(1)).replace("{n}", fa(COUNT))}</span></div>
<p><a href="#" id="outside">outside link</a></p>
<div class="gallery-root" id="house-gallery-viewer" role="dialog" aria-modal="true" aria-label="photos" hidden data-gallery data-locale="${locale}" data-counter-format="{i}/{n}">
<div class="gallery-viewport" data-gallery-viewport><div class="gallery-track" data-gallery-track>${slides.join("")}</div></div>
<div class="gallery-bar"><button class="gallery-btn gallery-close" type="button" data-gallery-close aria-label="close">x</button>
<span class="gallery-counter" data-gallery-counter aria-live="polite">${fa(1)}/${fa(COUNT)}</span></div>
<button class="gallery-btn gallery-nav gallery-prev" type="button" data-gallery-prev aria-label="prev">&lt;</button>
<button class="gallery-btn gallery-nav gallery-next" type="button" data-gallery-next aria-label="next">&gt;</button>
</div></div>
<script src="${pathToFileURL(join(staticDir, "gallery.js"))}"></script></body></html>`;
}

const state = `(() => {
  const root = document.querySelector("[data-gallery]");
  const g = window.chamedoonGallery;
  const imgs = [...root.querySelectorAll(".gallery-img")];
  const prev = root.querySelector("[data-gallery-prev]").getBoundingClientRect();
  const next = root.querySelector("[data-gallery-next]").getBoundingClientRect();
  return {
    open: !root.hidden,
    index: g.index(),
    counter: root.querySelector("[data-gallery-counter]").textContent,
    loaded: imgs.map((img) => Boolean(img.getAttribute("src"))),
    prevDisabled: root.querySelector("[data-gallery-prev]").disabled,
    nextDisabled: root.querySelector("[data-gallery-next]").disabled,
    focus: document.activeElement && (document.activeElement.getAttribute("aria-label") || document.activeElement.id || document.activeElement.tagName),
    locked: document.documentElement.classList.contains("gallery-open"),
    nextIsLeft: next.left < prev.left,
    zoom: g.zoom(),
    visible: imgs.map((img) => { const r = img.getBoundingClientRect(); return r.right > 1 && r.left < innerWidth - 1; }),
  };
})()`;

async function check(browser, locale) {
  const rtl = locale === "fa";
  const dir = mkdtempSync(join(tmpdir(), "gallery-fixture-"));
  const file = join(dir, "index.html");
  writeFileSync(file, fixture(locale), "utf8");
  try {
    await browser.goto(pathToFileURL(file).href);
    const d = (v) => (rtl ? String(v).replace(/[0-9]/g, (x) => "۰۱۲۳۴۵۶۷۸۹"[x]) : String(v));
    const counter = (i) => `${d(i)}/${d(COUNT)}`;
    const get = () => browser.evaluate(state);
    let s = await get();
    assert.equal(s.open, false, "starts closed");

    // Tapping the second photo of the strip opens the overlay on it.
    await browser.evaluate(`document.querySelector('[data-gallery-open="1"]').click()`);
    await browser.sleep(50);
    s = await get();
    assert.equal(s.open, true, "opens");
    assert.equal(s.locked, true, "page scroll locked");
    assert.equal(s.index, 1);
    assert.equal(s.counter, counter(2));
    assert.equal(s.focus, "close", "focus moves to close");
    assert.deepEqual(s.loaded, [true, true, true, false, false, false], "current photo and neighbours preloaded");
    assert.equal(s.nextIsLeft, rtl, rtl ? "RTL: next button on the left" : "LTR: next button on the right");
    await browser.sleep(50);
    s = await get();
    assert.deepEqual(s.visible, [false, true, false, false, false, false], "only the current slide is on screen");

    // Buttons.
    await browser.evaluate(`document.querySelector('[data-gallery-next]').click()`);
    s = await get();
    assert.equal(s.counter, counter(3));
    assert.equal(s.loaded[3], true, "next neighbour preloaded");
    await browser.evaluate(`document.querySelector('[data-gallery-prev]').click()`);
    s = await get();
    assert.equal(s.counter, counter(2));

    // Stops at the ends (no wrap).
    await browser.evaluate(`document.querySelector('[data-gallery-prev]').click()`);
    s = await get();
    assert.equal(s.index, 0);
    assert.equal(s.prevDisabled, true);
    await browser.evaluate(`document.querySelector('[data-gallery-prev]').click()`);
    s = await get();
    assert.equal(s.index, 0, "stays on the first photo");
    for (let i = 0; i < COUNT + 2; i += 1) await browser.evaluate(`window.chamedoonGallery.next()`);
    s = await get();
    assert.equal(s.index, COUNT - 1, "stays on the last photo");
    assert.equal(s.nextDisabled, true);
    assert.equal(s.counter, counter(COUNT));

    // Arrow keys follow the reading direction: in RTL ArrowLeft goes forward.
    await browser.evaluate(`window.chamedoonGallery.go(2, false)`);
    const forward = rtl ? ["ArrowLeft", 37] : ["ArrowRight", 39];
    const backward = rtl ? ["ArrowRight", 39] : ["ArrowLeft", 37];
    await browser.key(forward[0], forward[0], forward[1]);
    s = await get();
    assert.equal(s.index, 3, "arrow forward");
    await browser.key(backward[0], backward[0], backward[1]);
    s = await get();
    assert.equal(s.index, 2, "arrow back");

    // Swipe: in RTL the finger moves right to go forward, in LTR left.
    await browser.sleep(350);
    const forwardSwipe = rtl ? [80, 320] : [320, 80];
    await browser.swipe(forwardSwipe[0], 420, forwardSwipe[1]);
    s = await get();
    assert.equal(s.index, 3, "swipe forward");
    assert.equal(s.counter, counter(4));
    await browser.swipe(forwardSwipe[1], 420, forwardSwipe[0]);
    s = await get();
    assert.equal(s.index, 2, "swipe back");
    await browser.swipe(200, 420, 215);
    s = await get();
    assert.equal(s.index, 2, "a short drag snaps back");

    // Double tap zooms, a swipe while zoomed pans instead of changing photo, double tap again resets.
    await browser.tap(195, 420);
    await browser.sleep(60);
    await browser.tap(195, 420);
    await browser.sleep(300);
    s = await get();
    assert.ok(s.zoom > 1, "double tap zooms");
    await browser.swipe(forwardSwipe[0], 420, forwardSwipe[1]);
    s = await get();
    assert.equal(s.index, 2, "zoomed drag pans, does not change photo");
    await browser.tap(195, 420);
    await browser.sleep(60);
    await browser.tap(195, 420);
    await browser.sleep(300);
    s = await get();
    assert.equal(s.zoom, 1, "second double tap resets zoom");

    // Focus trap: Tab cycles inside the dialog.
    await browser.evaluate(`document.querySelector('[data-gallery-close]').focus()`);
    for (let i = 0; i < 4; i += 1) {
      await browser.key("Tab", "Tab", 9);
      s = await get();
      assert.ok(["close", "prev", "next"].includes(s.focus), `focus stays inside (${s.focus})`);
    }
    await browser.key("Tab", "Tab", 9, 8);
    s = await get();
    assert.ok(["close", "prev", "next"].includes(s.focus), "shift+tab stays inside");

    // Esc closes and focus returns to the photo the person stopped at.
    await browser.key("Escape", "Escape", 27);
    s = await get();
    assert.equal(s.open, false, "Esc closes");
    assert.equal(s.locked, false, "scroll unlocked");
    assert.equal(s.focus, "open 3", "focus back on the strip photo");

    // Close button and the Telegram back button.
    await browser.evaluate(`
      window.__tgBack = null;
      window.Telegram = { WebApp: { BackButton: {
        show() { this.shown = true; }, hide() { this.shown = false; },
        onClick(fn) { window.__tgBack = fn; }, offClick() { window.__tgBack = null; },
      } } };
      window.chamedoonGallery.open(4);
    `);
    s = await get();
    assert.equal(s.counter, counter(5));
    assert.equal(await browser.evaluate(`window.Telegram.WebApp.BackButton.shown`), true, "Telegram back button shown");
    await browser.evaluate(`window.__tgBack()`);
    s = await get();
    assert.equal(s.open, false, "Telegram back closes");
    assert.equal(await browser.evaluate(`window.Telegram.WebApp.BackButton.shown`), false);
    await browser.evaluate(`window.chamedoonGallery.open(0); document.querySelector('[data-gallery-close]').click()`);
    s = await get();
    assert.equal(s.open, false, "close button closes");

    // nav.js back guard takes precedence when present.
    await browser.evaluate(`
      window.__guards = [];
      window.chamedoonRegisterBack = (g) => { window.__guards.push(g); return () => window.__guards.splice(window.__guards.indexOf(g), 1); };
      window.chamedoonGallery.open(2);
    `);
    assert.equal(await browser.evaluate(`window.__guards.length === 1 && window.__guards[0].active()`), true);
    await browser.evaluate(`window.__guards[0].back()`);
    s = await get();
    assert.equal(s.open, false, "back guard closes");
    assert.equal(await browser.evaluate(`window.__guards.length`), 0, "guard unregistered");

    assert.deepEqual(browser.errors, [], "no page errors");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const browser = await launch();
let failed = false;
try {
  for (const locale of ["fa", "en"]) {
    await check(browser, locale);
    console.log(`ok - gallery ${locale} (${locale === "fa" ? "rtl" : "ltr"})`);
  }
} catch (error) {
  failed = true;
  console.error(error);
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
