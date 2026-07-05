import fs from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = String(input.html || '');
const payload = input.payload || {};
const scriptPath = input.script_path;
const requestedIds = input.ids || [];

if (!scriptPath) {
  throw new Error('script_path is required');
}

class ClassList {
  constructor() {
    this.values = new Set();
  }

  add(...classes) {
    for (const cls of classes) this.values.add(cls);
  }

  remove(...classes) {
    for (const cls of classes) this.values.delete(cls);
  }

  toggle(cls, force) {
    if (force === undefined) {
      if (this.values.has(cls)) {
        this.values.delete(cls);
        return false;
      }
      this.values.add(cls);
      return true;
    }
    if (force) this.values.add(cls);
    else this.values.delete(cls);
    return Boolean(force);
  }

  contains(cls) {
    return this.values.has(cls);
  }

  toArray() {
    return Array.from(this.values).sort();
  }
}

class Element {
  constructor(id, tag) {
    this.id = id;
    this.tagName = tag.toUpperCase();
    this.textContent = '';
    this.value = '';
    this.dataset = {};
    this.style = {};
    this.layout = {};
    this.classList = new ClassList();
  }
}

function decodeHtmlAttr(value) {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>');
}

function elementsFromHtml(source) {
  const elements = new Map();
  const idPattern = /<([a-zA-Z0-9:-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g;
  let match;
  while ((match = idPattern.exec(source)) !== null) {
    const [, tag, id] = match;
    const element = new Element(id, tag);
    const value = /\bvalue="([^"]*)"/.exec(match[0]);
    if (value) element.value = decodeHtmlAttr(value[1]);
    elements.set(id, element);
  }

  for (const id of requestedIds) {
    if (!elements.has(id)) elements.set(id, new Element(id, 'div'));
  }
  return elements;
}

const elements = elementsFromHtml(html);
const handlers = {};
const plotlyCalls = [];

function plotlyCall(method, args) {
  plotlyCalls.push({
    method,
    target: args[0],
    traceCount: Array.isArray(args[1]) ? args[1].length : undefined,
    indices: Array.isArray(args[2]) ? args[2] : undefined,
  });
}

const context = {
  console,
  document: {
    getElementById(id) {
      return elements.get(id) || null;
    },
  },
  socket: {
    on(event, handler) {
      handlers[event] = handler;
    },
  },
  Plotly: {
    extendTraces(...args) {
      plotlyCall('extendTraces', args);
    },
    newPlot(...args) {
      plotlyCall('newPlot', args);
    },
    addTraces(...args) {
      plotlyCall('addTraces', args);
    },
    relayout(...args) {
      plotlyCall('relayout', args);
    },
  },
  chartConfig: {},
  chartLayout: { yaxis: {} },
  oxideColors: {
    SiO2: '#6366f1',
    Al2O3: '#dc2626',
    FeO: '#22c55e',
    MgO: '#eab308',
    CaO: '#06b6d4',
    TiO2: '#f97316',
  },
  compInitialized: false,
  compTraces: {},
  absInitialized: false,
  absOxideTraces: {},
  absMetalTraces: {},
  flowInitialized: false,
  flowTraces: {},
  o2BudgetInitialized: false,
  meltInvInitialized: false,
  lastCampaignForInv: '',
};

context.initCompositionChart = () => {
  context.compInitialized = true;
  context.compTraces = {};
  let idx = 0;
  for (const oxide of Object.keys(context.oxideColors)) {
    context.compTraces[oxide] = idx++;
  }
  context.compTraces._melt_mass = idx;
};
context.initAbsoluteChart = () => {
  context.absInitialized = true;
  context.absOxideTraces = {};
  context.absMetalTraces = {};
  let idx = 0;
  for (const oxide of Object.keys(context.oxideColors)) {
    context.absOxideTraces[oxide] = idx++;
  }
  for (const metal of ['Fe', 'Si', 'Mg', 'Na', 'K', 'Ti', 'Cr', 'Mn', 'Al', 'Ca', 'O2', 'SiO2']) {
    context.absMetalTraces[metal] = idx++;
  }
};
context.initFlowChart = (keys) => {
  context.flowInitialized = true;
  context.flowTraces = {};
  keys.forEach((key, idx) => {
    context.flowTraces[key] = idx;
  });
};
context.initO2BudgetChart = () => {
  context.o2BudgetInitialized = true;
};
context.initMeltInventoryChart = () => {
  context.meltInvInitialized = true;
};

vm.createContext(context);
vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context, {
  filename: scriptPath,
});

if (typeof handlers.simulation_tick !== 'function') {
  throw new Error('simulation_tick handler was not registered');
}

handlers.simulation_tick(payload);

const output = {
  text: {},
  dataset: {},
  style: {},
  classes: {},
  plotlyCalls,
};

for (const id of requestedIds) {
  const element = elements.get(id);
  output.text[id] = element ? element.textContent : null;
  output.dataset[id] = element ? element.dataset : null;
  output.style[id] = element ? element.style : null;
  output.classes[id] = element ? element.classList.toArray() : null;
}

console.log(JSON.stringify(output));
