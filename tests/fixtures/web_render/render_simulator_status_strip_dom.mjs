/**
 * DOM harness for the operator status strip (b-088).
 *
 * Loads simulator-socket.js + simulator-ticks.js under a minimal vm, then
 * plays a scripted event sequence and reports status-text / status-backend.
 *
 * Input JSON (stdin):
 *   {
 *     "socket_script_path": ".../simulator-socket.js",
 *     "ticks_script_path": ".../simulator-ticks.js",
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
import vm from 'node:vm';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const socketScriptPath = input.socket_script_path;
const ticksScriptPath = input.ticks_script_path;
const sequence = Array.isArray(input.sequence) ? input.sequence : [];
const mutateBadgeClobber = Boolean(input.mutate_badge_clobber);
const mutateNoTickRecovery = Boolean(input.mutate_no_tick_recovery);

if (!socketScriptPath || !ticksScriptPath) {
  throw new Error('socket_script_path and ticks_script_path are required');
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
    this.listeners = {};
    this.classList = {
      values: new Set(),
      add: (...classes) => {
        for (const cls of classes) this.values.add(cls);
      },
      remove: (...classes) => {
        for (const cls of classes) this.values.delete(cls);
      },
      toggle: (cls, force) => {
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
      },
      contains: (cls) => this.values.has(cls),
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

const handlers = {};
const socket = {
  on(event, handler) {
    if (!handlers[event]) handlers[event] = [];
    // ticks.js assigns handlers[event] = handler (single); socket.js pushes.
    // Support both patterns: store as array always, and also allow overwrite
    // via a Proxy-like dual path below.
    handlers[event].push(handler);
  },
  emit() {},
};

// ticks.js does `socket.on(event, handler)` expecting the global socket;
// some files overwrite with a single handler. Collect all.
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
    getElementById(id) {
      return findById(body, id) || seed[id] || null;
    },
    createElement(tag) {
      return new Element('', tag);
    },
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

vm.createContext(context);
// In a vm sandbox, globalThis is the context object. Cross-script helpers
// publish onto it from simulator-socket.js.
vm.runInContext(fs.readFileSync(socketScriptPath, 'utf8'), context, {
  filename: socketScriptPath,
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

function emitEvent(event, payload) {
  for (const handler of handlers[event] || []) {
    handler(payload);
  }
}

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
  };
}

const steps = [];
for (const step of sequence) {
  const event = step.event;
  const payload = step.payload || {};
  emitEvent(event, payload);
  steps.push({ event, ...snapshot() });
}

console.log(JSON.stringify({ steps, final: snapshot() }));
