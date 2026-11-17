// The site test's browser pass, in Node because the cloud sessions ship
// Playwright for Node (require('playwright') works from any directory).
// Reads BASE, AGENT_TOKEN and PAGES (comma-separated) from the
// environment; prints one JSON line per page: {path, ok, note}.
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const base = process.env.BASE, token = process.env.AGENT_TOKEN;
  const pages = (process.env.PAGES || '').split(',').filter(Boolean);
  const exe = fs.existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined;
  let browser;
  try {
    browser = await chromium.launch(exe ? { executablePath: exe } : {});
  } catch (e) {
    browser = await chromium.launch();
  }
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 },
    extraHTTPHeaders: { Authorization: `Bearer ${token}` } });
  for (const path of pages) {
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e).slice(0, 200)));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
    try {
      await page.goto(base + path, { waitUntil: 'load', timeout: 120000 });
      await page.waitForTimeout(1500);
      const sparks = await page.locator('svg.spark').count();
      let popup = 'no sparklines on the page';
      if (sparks) {
        await page.locator('svg.spark').first().click();
        await page.waitForTimeout(500);
        const opened = await page.evaluate(() => !!(document.getElementById('chartPop') || {}).open);
        popup = opened ? "the first sparkline's pop-up opened" : 'POP-UP DID NOT OPEN';
        if (!opened) errors.push('sparkline pop-up did not open');
      }
      const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 4);
      let note = `${sparks} sparklines; ${popup}` + (wide ? '; page scrolls sideways' : '');
      if (errors.length) note += ' | errors: ' + errors.slice(0, 3).join(' / ');
      console.log(JSON.stringify({ path, ok: !errors.length, note }));
    } catch (e) {
      console.log(JSON.stringify({ path, ok: false, note: ('did not load: ' + e).slice(0, 300) }));
    } finally {
      await page.close();
    }
  }
  await browser.close();
})().catch(e => { console.log(JSON.stringify({ path: 'browser', ok: false, note: String(e).slice(0, 300) })); process.exit(0); });
