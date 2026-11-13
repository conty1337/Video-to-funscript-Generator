const { chromium } = require('playwright');
(async () => {
  const [port, out] = process.argv.slice(2);
  const b = await chromium.launch();
  for (const [tab, name] of [['2', 'remote.png'], ['v', 'remote-video.png']]) {
    const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
    await ctx.addInitScript(([t]) => { localStorage.setItem('vtf-pin', '123456'); localStorage.setItem('vtf-tab', t); }, [tab]);
    const p = await ctx.newPage();
    await p.goto(`http://127.0.0.1:${port}/`);
    await p.waitForTimeout(1500);
    await p.screenshot({ path: `${out}/${name}`, fullPage: false });
    await ctx.close();
  }
  await b.close();
})();
