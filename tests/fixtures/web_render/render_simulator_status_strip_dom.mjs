/**
 * DOM harness for the operator status strip (b-088).
 *
 * Loads the production lifecycle modules in template order under a minimal
 * vm, then plays a scripted Socket.IO/control sequence and reports the strip.
 *
 * Input JSON (stdin):
 *   {
 *     "socket_script_path": ".../simulator-socket.js",
 *     "charts_script_path": ".../simulator-charts.js",
 *     "ticks_script_path": ".../simulator-ticks.js",
 *     "advisory_script_path": ".../simulator-advisory.js",
 *     "decisions_script_path": ".../simulator-decisions.js",
 *     "controls_script_path": ".../simulator-controls.js",
 *     "sequence": [
 *       {"event": "simulation_status", "payload": {...}},
 *       {"event": "simulation_tick", "payload": {...}},
 *       ...
 *     ]
 *   }
 *
 * Optional mutation flags (for falsifiable proof):
 *   "mutate_badge_clobber": true  — force old unknown-default badge path
 *   "mutate_no_tick_recovery": true — skip noteLiveSimulationTick on ticks
 */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const socketScriptPath = input.socket_script_path;
const chartsScriptPath = input.charts_script_path;
const ticksScriptPath = input.ticks_script_path;
const advisoryScriptPath = input.advisory_script_path;
const decisionsScriptPath = input.decisions_script_path;
const controlsScriptPath = input.controls_script_path;
const sequence = Array.isArray(input.sequence) ? input.sequence : [];
const mutateBadgeClobber = Boolean(input.mutate_badge_clobber);
const mutateNoTickRecovery = Boolean(input.mutate_no_tick_recovery);

if (
  !socketScriptPath
  || !chartsScriptPath
  || !ticksScriptPath
  || !advisoryScriptPath
  || !decisionsScriptPath
  || !controlsScriptPath
) {
  throw new Error(
    'all simulator module paths are required',
  );
}

class Element {
  constructor(id = '', tag = 'div') {
    this.id = id;
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.textContent = '';
    this.className = '';
    this.title = '';
    this.style = {};
    this.dataset = {};
    this.layout = {};
    this.disabled = false;
    this.checked = false;
    this.hidden = false;
    this.value = '';
    this.defaultValue = '';
    this.options = [];
    this.selectedIndex = 0;
    this.listeners = {};
    const classValues = new Set();
    this.classList = {
      values: classValues,
      add: (...classes) => {
        for (const cls of classes) classValues.add(cls);
      },
      remove: (...classes) => {
        for (const cls of classes) classValues.delete(cls);
      },
      toggle: (cls, force) => {
        if (force === undefined) {
          if (classValues.has(cls)) {
            classValues.delete(cls);
            return false;
          }
          classValues.add(cls);
          return true;
        }
        if (force) classValues.add(cls);
        else classValues.delete(cls);
        return Boolean(force);
      },
      contains: (cls) => classValues.has(cls),
    };
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  addEventListener(event, handler) {
    this.listeners[event] = handler;
  }

  click() {
    const handler = this.listeners.click;
    if (typeof handler !== 'function') {
      throw new Error(`click handler missing for ${this.id || this.tagName}`);
    }
    handler({
      target: this,
      currentTarget: this,
      preventDefault() {},
    });
  }

  querySelector() {
    return null;
  }

  querySelectorAll(selector) {
    if (selector !== 'details') return [];
    return this.children.filter((child) => child.tagName === 'DETAILS');
  }

  replaceChildren(...children) {
    this.children = [];
    for (const child of children) this.appendChild(child);
  }

  getAttribute() {
    return null;
  }

  focus() {}

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }

  removeAttribute(name) {
    if (name === 'title') this.title = '';
  }
}

const body = new Element('body', 'body');
const seed = {
  'btn-start': new Element('btn-start', 'button'),
  'btn-pause': new Element('btn-pause', 'button'),
  'btn-resume': new Element('btn-resume', 'button'),
  'status-text': new Element('status-text', 'span'),
  'status-backend': new Element('status-backend', 'span'),
  'status-hour': new Element('status-hour', 'span'),
  'status-temp': new Element('status-temp', 'span'),
  'status-campaign': new Element('status-campaign', 'span'),
  'status-mass': new Element('status-mass', 'span'),
  'status-atmosphere': new Element('status-atmosphere', 'span'),
  'status-ramp': new Element('status-ramp', 'span'),
  'status-vent': new Element('status-vent', 'span'),
  'energy-cumulative': new Element('energy-cumulative', 'span'),
  'energy-hour': new Element('energy-hour', 'span'),
  'energy-electrical': new Element('energy-electrical', 'span'),
  'energy-evaporation': new Element('energy-evaporation', 'span'),
  'energy-scope': new Element('energy-scope', 'span'),
  'furnace-heat-status': new Element('furnace-heat-status', 'span'),
  'oxygen-total': new Element('oxygen-total', 'span'),
  'mass-error': new Element('mass-error', 'span'),
  'gt-ramp-actual': new Element('gt-ramp-actual', 'span'),
  'gt-ramp-nominal': new Element('gt-ramp-nominal', 'span'),
  'gt-pipe-sat': new Element('gt-pipe-sat', 'span'),
  'gt-turbine-load': new Element('gt-turbine-load', 'span'),
  'gt-o2-stored': new Element('gt-o2-stored', 'span'),
  'gt-o2-vented': new Element('gt-o2-vented', 'span'),
  'gt-vent-rate': new Element('gt-vent-rate', 'span'),
  'debug-inventory-json': new Element('debug-inventory-json', 'pre'),
  'chart-temperature': new Element('chart-temperature', 'div'),
  'chart-pressure': new Element('chart-pressure', 'div'),
  'chart-composition': new Element('chart-composition', 'div'),
  'chart-absolute': new Element('chart-absolute', 'div'),
  'chart-massflow': new Element('chart-massflow', 'div'),
  'chart-o2-budget': new Element('chart-o2-budget', 'div'),
  'chart-melt-inventory': new Element('chart-melt-inventory', 'div'),
  'chart-pot-composition': new Element('chart-pot-composition', 'div'),
  'chart-flue-composition': new Element('chart-flue-composition', 'div'),
};

seed['status-text'].textContent = 'Ready';
seed['status-backend'].className = 'backend-badge backend-badge-unknown';
seed['status-backend'].textContent = 'Backend: —';
seed['status-backend'].title = 'Backend status not selected';
seed['status-hour'].textContent = 'Hour: 0';
seed['status-temp'].textContent = 'T: — °C';
seed['status-campaign'].textContent = '—';
seed['status-mass'].textContent = 'Melt: — kg';
seed['feedstock-select'] = new Element('feedstock-select', 'select');
seed['feedstock-select'].value = 'lunar_mare_low_ti';
seed['engine-select'] = new Element('engine-select', 'select');
seed['engine-select'].value = 'internal-analytical';
seed['batch-mass'] = new Element('batch-mass', 'input');
seed['batch-mass'].value = '1000';
for (const id of ['add-na', 'add-k', 'add-mg', 'add-ca', 'add-c']) {
  seed[id] = new Element(id, 'input');
  seed[id].value = '0';
}

for (const el of Object.values(seed)) {
  body.appendChild(el);
}

function findById(root, id) {
  if (root.id === id) return root;
  for (const child of root.children) {
    const match = findById(child, id);
    if (match) return match;
  }
  return null;
}

const emitted = [];
class FakeSocket {
  constructor() {
    this.connected = true;
    this.handlers = new Map();
  }

  on(event, handler) {
    const eventHandlers = this.handlers.get(event) || [];
    eventHandlers.push(handler);
    this.handlers.set(event, eventHandlers);
  }

  emit(event, payload) {
    emitted.push({ event, payload });
  }

  serverEmit(event, payload) {
    for (const handler of this.handlers.get(event) || []) {
      handler(payload);
    }
  }
}

const socket = new FakeSocket();
const plotlyNoop = {
  extendTraces() {},
  newPlot() {},
  addTraces() {},
  relayout() {},
};

const context = {
  console: {
    log() {},
    warn() {},
    error() {},
  },
  document: {
    body,
    readyState: 'loading',
    getElementById(id) {
      const existing = findById(body, id) || seed[id];
      if (existing) return existing;
      const created = new Element(id);
      seed[id] = created;
      body.appendChild(created);
      return created;
    },
    createElement(tag) {
      return new Element('', tag);
    },
    querySelector(selector) {
      const radio = new Element('', 'input');
      if (selector === 'input[name="track"]:checked') {
        radio.value = 'pyrolysis';
        return radio;
      }
      if (selector === 'input[name="speed"]:checked') {
        radio.value = '1000';
        return radio;
      }
      return null;
    },
    querySelectorAll() {
      return [];
    },
    addEventListener() {},
  },
  io() {
    return socket;
  },
  window: {},
  Plotly: plotlyNoop,
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
  alert() {},
  htmx: { ajax() {} },
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
context.ensureFlowChartSpecies = () => {};
context.initO2BudgetChart = () => {
  context.o2BudgetInitialized = true;
};
context.initMeltInventoryChart = () => {
  context.meltInvInitialized = true;
};
context.updateLiveCompositionChart = () => {};
context.updateBar = () => {};
context.window = context;
context.matchMedia = () => ({ matches: false });

vm.createContext(context);
vm.runInContext(fs.readFileSync(socketScriptPath, 'utf8'), context, {
  filename: socketScriptPath,
});

vm.runInContext(fs.readFileSync(chartsScriptPath, 'utf8'), context, {
  filename: chartsScriptPath,
});

// Optional mutation: restore the pre-fix badge clobber path.
if (mutateBadgeClobber) {
  const clobber = function updateBackendBadgeClobber(data) {
    const badge = context.document.getElementById('status-backend');
    if (!badge || !data) return;
    const active = data.backend_active || 'unknown';
    const status = data.backend_status || 'unknown';
    const authoritative = data.backend_authoritative === true;
    badge.textContent = `Backend: ${active} / ${status}`;
    badge.className = 'backend-badge '
      + (authoritative ? 'backend-badge-ok' : 'backend-badge-internal-analytical');
    badge.title = data.backend_status_message || data.backend_message || '';
  };
  context.updateBackendBadge = clobber;
}

// ticks.js registers socket.on('simulation_tick', ...) — must see same socket.
vm.runInContext(fs.readFileSync(ticksScriptPath, 'utf8'), context, {
  filename: ticksScriptPath,
});

if (mutateNoTickRecovery) {
  // Drop tick recovery while keeping chart/status hour updates.
  context.noteLiveSimulationTick = function noopNoteLive() {};
}

vm.runInContext(fs.readFileSync(advisoryScriptPath, 'utf8'), context, {
  filename: advisoryScriptPath,
});

vm.runInContext(fs.readFileSync(decisionsScriptPath, 'utf8'), context, {
  filename: decisionsScriptPath,
});

vm.runInContext(fs.readFileSync(controlsScriptPath, 'utf8'), context, {
  filename: controlsScriptPath,
});

function snapshot() {
  const text = context.document.getElementById('status-text');
  const badge = context.document.getElementById('status-backend');
  const hour = context.document.getElementById('status-hour');
  return {
    statusText: text ? text.textContent : null,
    backendText: badge ? badge.textContent : null,
    backendClass: badge ? badge.className : null,
    backendTitle: badge ? badge.title : null,
    hourText: hour ? hour.textContent : null,
    startDisabled: context.document.getElementById('btn-start').disabled,
    pauseDisabled: context.document.getElementById('btn-pause').disabled,
    resumeDisabled: context.document.getElementById('btn-resume').disabled,
    emittedCount: emitted.length,
    lifecycle: context.simulatorLifecycleSnapshot(),
  };
}

const steps = [];
for (const step of sequence) {
  const event = step.event;
  const payload = step.payload || {};
  if (event === 'start_click') {
    context.document.getElementById('btn-start').click();
  } else {
    socket.serverEmit(event, payload);
  }
  steps.push({ event, ...snapshot() });
}

console.log(JSON.stringify({
  steps,
  final: snapshot(),
  emittedEvents: emitted.map(({ event }) => event),
  emittedPayloads: emitted.map(({ payload }) => payload),
  startPayloads: emitted
    .filter(({ event }) => event === 'start_simulation')
    .map(({ payload }) => payload),
  loadedModules: [
    socketScriptPath,
    chartsScriptPath,
    ticksScriptPath,
    advisoryScriptPath,
    decisionsScriptPath,
    controlsScriptPath,
  ].map((scriptPath) => path.basename(scriptPath)),
}));
