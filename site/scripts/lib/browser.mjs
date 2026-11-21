import { chromium } from 'playwright';
// Playwright's own Chromium when installed, else the local Chrome.
export async function launch(args = []) {
  try { return await chromium.launch({ args }); }
  catch { return await chromium.launch({ channel: 'chrome', args }); }
}
