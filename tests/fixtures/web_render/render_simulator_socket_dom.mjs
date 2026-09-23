import fs from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const socketScriptPath = input.socket_script_path;
const decisionsScriptPath = input.decisions_script_path;

if (!socketScriptPath || !decisionsScriptPath) {
  throw new Error('socket_script_path and decisions_script_path are required');
}

class Element {
  constructor(id = '', tag = 'div') {
    this.id = id;
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.textContent = '';
    this.className = '';
    this.style = {};
    this.disabled = false;
    this.listeners = {};
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
    this.parentNode.children = this.parentNode.children.filter(child => child !== this);
    this.parentNode = null;
  }
}

const body = new Element('body', 'body');
for (const id of ['btn-start', 'btn-pause', 'btn-resume', 'status-text', 'status-backend']) {
  body.appendChild(new Element(id, id.startsWith('btn-') ? 'button' : 'div'));
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
    handlers[event].push(handler);
  },
  emit() {},
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
      return findById(body, id);
    },
    createElement(tag) {
      return new Element('', tag);
    },
  },
  io() {
    return socket;
  },
  window: {},
};

vm.createContext(context);
for (const scriptPath of [socketScriptPath, decisionsScriptPath]) {
  vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context, {
    filename: scriptPath,
  });
}

function emitEvent(event, payload) {
  for (const handler of handlers[event] || []) handler(payload);
}

emitEvent('connect');
const start = findById(body, 'btn-start');
const pause = findById(body, 'btn-pause');
const resume = findById(body, 'btn-resume');
start.disabled = true;
pause.disabled = false;
resume.disabled = true;
emitEvent('decision_required', {
  type: 'campaign-path',
  context: 'Choose next campaign path',
  recommendation: 'A',
  options: ['A', 'B'],
});
const decisionModal = findById(body, 'decision-modal');
const decisionPanel = decisionModal.children[0];
const decisionModalStyles = {
  modal: decisionPanel.style.cssText,
  title: decisionPanel.children[0].style.cssText,
  context: decisionPanel.children[1].style.cssText,
  recommendation: decisionPanel.children[2].style.cssText,
  recommendedButton: decisionPanel.children[3].children[0].style.cssText,
  otherButton: decisionPanel.children[3].children[1].style.cssText,
};
emitEvent('disconnect', 'transport close');

const afterDisconnect = {
  startDisabled: start.disabled,
  pauseDisabled: pause.disabled,
  resumeDisabled: resume.disabled,
  decisionModalPresent: Boolean(findById(body, 'decision-modal')),
};

emitEvent('connect');
const afterReconnect = {
  startDisabled: start.disabled,
  pauseDisabled: pause.disabled,
  resumeDisabled: resume.disabled,
  decisionModalPresent: Boolean(findById(body, 'decision-modal')),
};

console.log(JSON.stringify({ afterDisconnect, afterReconnect, decisionModalStyles }));
