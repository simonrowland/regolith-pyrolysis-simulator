import fs from "node:fs";
import vm from "node:vm";

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const source = fs.readFileSync(input.script_path, "utf8");
const taxonomyTerminalSections = input.taxonomy_terminal_sections;
const context = {
  __taxonomyTerminalSections: taxonomyTerminalSections,
  console,
  document: { querySelector: () => ({ innerHTML: "" }) },
  fetch: () => new Promise(() => {}),
  URLSearchParams,
  window: { location: { search: "" } },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(
  `${source}\n` +
  `globalThis.__qaResult = {\n` +
  `  timesteps: [2.5, 7.25].map((good) => renderTimestepLedger({ledger: {\n` +
  `    "process.cleaned_melt": {good, bool_true: true, bool_false: false, array: [], numeric_string: "3", nan: NaN, infinity: Infinity}\n` +
  `  }})),\n` +
  `  terminal: ledgerSection({\n` +
  `    "terminal.products": {good: 4.5, bool_true: true, array: [], numeric_string: "3", nan: NaN, infinity: Infinity}\n` +
  `  }),\n` +
  `  taxonomy: {\n` +
  `    present: ceramicSection(globalThis.__taxonomyTerminalSections.present),\n` +
  `    explicit_null: ceramicSection(globalThis.__taxonomyTerminalSections.explicit_null),\n` +
  `    absent: ceramicSection(globalThis.__taxonomyTerminalSections.absent)\n` +
  `  }\n` +
  `};`,
  context,
);
process.stdout.write(JSON.stringify(context.__qaResult));
