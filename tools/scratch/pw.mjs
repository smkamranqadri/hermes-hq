// Shared Playwright helper for phone-size checks against a hermes-hq server.
//   import { open } from '/opt/data/projects/hermes-hq/tools/scratch/pw.mjs'
//   const s = await open('http://127.0.0.1:9013', { shots: '/tmp/claude-0/.../shots' })
//   await s.goto('/brain/library?area=1'); await s.shot('library-work'); s.assertWidth(); ... await s.close()
// Playwright is installed ONCE at /opt/data/hh-scratch/pw (npm i playwright there); browsers are the
// shared ms-playwright cache. Never `npm i playwright` per worktree.
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
const require = createRequire('/opt/data/hh-scratch/pw/package.json')
const { chromium } = require('playwright')

const CHROME = '/opt/data/home/.cache/ms-playwright/chromium-1234/chrome-linux/chrome'

export async function open(base, opts = {}) {
  const { password = 'pw-test', shots = process.cwd(), mobile = true, width = 390, height = 844 } = opts
  fs.mkdirSync(shots, { recursive: true })
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] })
  const ctx = await browser.newContext({ viewport: { width, height }, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: 2 })
  const page = await ctx.newPage()
  const errors = []; const requests = []
  page.on('pageerror', e => errors.push(String(e)))
  page.on('request', r => { const u = r.url(); if (u.includes('/api/')) requests.push(u.replace(base, '')) })
  await page.goto(base + '/')
  await page.fill('input[type=password]', password)
  await page.keyboard.press('Enter')
  await page.waitForSelector('input[type=password]', { state: 'detached', timeout: 10000 })
  let n = 0
  const s = {
    page, errors, requests, base,
    goto: async (p) => { await page.goto(base + p); await page.waitForLoadState('networkidle') },
    shot: async (name) => { const f = path.join(shots, `${String(++n).padStart(2, '0')}-${name}.png`); await page.screenshot({ path: f, fullPage: false }); return f },
    width: async () => page.evaluate(() => document.documentElement.scrollWidth),
    assertWidth: async () => { const w = await s.width(); if (w !== width) throw new Error(`scrollWidth ${w} != ${width}`); return w },
    lastApi: (frag) => [...requests].reverse().find(u => u.includes(frag)),
    // exact text match helpers — plain text= substring-matches ("Work" hits "AI Workflow")
    exact: (t) => page.getByText(new RegExp(`^${t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`)),
    close: async () => { await browser.close(); return { errors: errors.length, requests: requests.length } },
  }
  return s
}
