import { percentile } from './score.mjs';

export function summarize(report) {
  const anchored = new Set(report.anchors.filter(a => a.anchor).map(a => a.id));
  return {
    highlights: report.anchors.length, located: anchored.size, correct: report.anchors.filter(a => a.correct).length,
    unlocated: report.anchors.filter(a => !a.anchor).length,
    meanRecall: report.anchors.reduce((s, a) => s + a.recall, 0) / report.anchors.length,
    meanPrecision: report.anchors.reduce((s, a) => s + a.precision, 0) / report.anchors.length,
    scenarios: [...new Set(report.jumps.map(j => j.scenario))].map(scenario => {
      const rows = report.jumps.filter(j => j.scenario === scenario), locatable = rows.filter(j => anchored.has(j.id));
      return { scenario, trials: rows.length, successes: rows.filter(j => j.success).length, locatable: locatable.length,
        locatableSuccesses: locatable.filter(j => j.success).length,
        p50Ms: percentile(rows.filter(j => j.success).map(j => j.latencyMs), .5),
        p95Ms: percentile(rows.filter(j => j.success).map(j => j.latencyMs), .95),
        unresolvedAtClick: rows.filter(j => j.unresolvedAtClick).length };
    }),
  };
}
