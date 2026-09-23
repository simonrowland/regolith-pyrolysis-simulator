import fs from 'node:fs';
import vm from 'node:vm';

const scriptPath = process.argv[2];
const traces = {};
const context = {
  console,
  window: { matchMedia: () => ({ matches: false }) },
  Plotly: {
    newPlot(id, entries) {
      traces[id] = entries;
    },
    addTraces(id, entries) {
      traces[id].push(...entries);
    },
  },
};

vm.createContext(context);
vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context, {
  filename: scriptPath,
});
vm.runInContext(
  "ensureFlowChartSpecies(['Fe', 'SiO']); flowInitialized = false; ensureFlowChartSpecies(['Fe']); ensureFlowChartSpecies(['Fe', 'SiO']);",
  context,
);

console.log(JSON.stringify({
  traceNames: traces['chart-massflow'].map(trace => trace.name),
  flowTraces: vm.runInContext('flowTraces', context),
}));
