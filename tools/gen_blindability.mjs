/*
 * Build-time generator: dump the site's per-champion "blindability" (blind-pick
 * field-safety) for every role x rank using machineloling's own WASM engine as
 * a ground-truth oracle, and write it to data/raw/blindability.json.
 *
 * This is the ONE metric that cannot be reproduced by direct decode of
 * matrices.bin (it is not a simple transform of the stored Δpp z). The core
 * matchup/synergy z-scores remain pure direct-decode and need no Node/WASM.
 *
 * Usage:  node tools/gen_blindability.mjs
 * Requires: tools/engine.wasm, tools/engine_glue.mjs, and the data files in
 *           data/raw/ (matrices.bin, index.json, champions.json).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Engine, initSync } from './engine_glue.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const raw = path.join(root, 'data', 'raw');

initSync({ module: fs.readFileSync(path.join(here, 'engine.wasm')) });
const engine = new Engine(
  new Uint8Array(fs.readFileSync(path.join(raw, 'matrices.bin'))),
  fs.readFileSync(path.join(raw, 'index.json'), 'utf8'),
  fs.readFileSync(path.join(raw, 'champions.json'), 'utf8'),
);

const ROLES = ['TOP', 'JUNGLE', 'MID', 'ADC', 'SUP'];
const RANKS = ['silver', 'gold', 'platinum', 'emerald', 'diamond', 'master_plus'];

const out = {};
for (const rank of RANKS) {
  out[rank] = {};
  for (const role of ROLES) {
    const res = engine.blindability({
      my_role: role, patch: rank, pool: [],
      pr_floor: 0.001, pr_weighted: false, top_x: 3, shrink_alpha: 0,
    });
    out[rank][role] = res.rows.map((r) => ({
      champion: r.champion,
      lane_matchup: r.lane_matchup,
      out_of_lane_matchup: r.out_of_lane_matchup,
      out_of_lane_synergy: r.out_of_lane_synergy,
      aggregate: r.aggregate,
    }));
  }
}

const dest = path.join(raw, 'blindability.json');
fs.writeFileSync(dest, JSON.stringify(out));
let n = 0;
for (const rank of RANKS) for (const role of ROLES) n += out[rank][role].length;
console.log(`wrote ${dest}: ${RANKS.length} ranks x ${ROLES.length} roles, ${n} champion rows`);
// quick provenance: confirm rank actually changes the numbers
const a = out.silver.TOP.find((r) => r.champion === 'Aatrox')?.aggregate;
const b = out.master_plus.TOP.find((r) => r.champion === 'Aatrox')?.aggregate;
console.log(`sanity: Aatrox TOP aggregate silver=${a?.toFixed(4)} master_plus=${b?.toFixed(4)} (differ => rank-sensitive)`);
