// Copies frontend/dist into desktop/renderer so packaging is self-contained.
// Run `npm run build` in frontend/ first.

const fs = require('node:fs')
const path = require('node:path')

const source = path.join(__dirname, '..', 'frontend', 'dist')
const target = path.join(__dirname, 'renderer')

if (!fs.existsSync(path.join(source, 'index.html'))) {
  console.error('frontend/dist not found - run "npm run build" in frontend/ first')
  process.exit(1)
}

fs.rmSync(target, { recursive: true, force: true })
fs.cpSync(source, target, { recursive: true })
console.log(`Copied ${source} -> ${target}`)
