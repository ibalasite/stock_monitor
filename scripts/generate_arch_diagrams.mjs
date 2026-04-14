#!/usr/bin/env node
/**
 * scripts/generate_arch_diagrams.mjs
 *
 * 從 docs/architecture/diagrams/*.md 擷取所有 mermaid 區塊，
 * 用 @mermaid-js/mermaid-cli (mmdc) 產生對應 PNG，
 * 輸出到 docs/architecture/images/
 *
 * 執行：node scripts/generate_arch_diagrams.mjs
 */

import { execSync } from 'child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync, rmSync } from 'fs';
import { join, basename, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT      = join(__dirname, '..');
const DIAG_DIR  = join(ROOT, 'docs', 'architecture', 'diagrams');
const IMG_DIR   = join(ROOT, 'docs', 'architecture', 'images');
const TMP_DIR   = join(ROOT, 'docs', 'architecture', '_tmp_mmd');

// Ensure output & tmp directories exist
for (const dir of [IMG_DIR, TMP_DIR]) {
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

// Mermaid config: white background, PNG, width 1800px
const MMDC_CFG = JSON.stringify({ theme: 'default' });
const CFG_FILE = join(TMP_DIR, 'mmdc_config.json');
writeFileSync(CFG_FILE, MMDC_CFG, 'utf8');

// Regex to extract ```mermaid ... ``` blocks
const RE_MERMAID = /```mermaid\r?\n([\s\S]*?)```/g;

let total = 0;
let success = 0;
let failed  = [];

const mdFiles = readdirSync(DIAG_DIR).filter(f => f.endsWith('.md')).sort();

for (const mdFile of mdFiles) {
  const mdPath    = join(DIAG_DIR, mdFile);
  const mdContent = readFileSync(mdPath, 'utf8');
  const stem      = basename(mdFile, '.md');     // e.g. "01-system-context"

  let blockIndex = 0;
  let match;
  RE_MERMAID.lastIndex = 0;

  while ((match = RE_MERMAID.exec(mdContent)) !== null) {
    total++;
    blockIndex++;
    const diagramDef = match[1];

    // Suffix only if multiple diagrams per file
    const suffix  = blockIndex === 1 ? '' : `-${blockIndex}`;
    const mmdFile = join(TMP_DIR, `${stem}${suffix}.mmd`);
    const pngFile = join(IMG_DIR, `${stem}${suffix}.png`);

    writeFileSync(mmdFile, diagramDef, 'utf8');

    try {
      execSync(
        `npx --yes @mermaid-js/mermaid-cli -i "${mmdFile}" -o "${pngFile}" -c "${CFG_FILE}" -b white --width 1800 --quiet`,
        { stdio: 'pipe', cwd: ROOT }
      );
      console.log(`  ✔  ${basename(pngFile)}`);
      success++;
    } catch (err) {
      const errMsg = err.stderr?.toString() || err.message;
      console.error(`  ✘  ${basename(pngFile)}\n     ${errMsg.split('\n')[0]}`);
      failed.push(basename(pngFile));
    }
  }
}

// Cleanup tmp
try { rmSync(TMP_DIR, { recursive: true, force: true }); } catch {}

console.log(`\n=== Done: ${success}/${total} diagrams generated to docs/architecture/images/ ===`);
if (failed.length) {
  console.error(`Failed: ${failed.join(', ')}`);
  process.exit(1);
}
