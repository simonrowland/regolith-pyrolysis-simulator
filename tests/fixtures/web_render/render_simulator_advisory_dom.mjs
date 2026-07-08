import fs from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = String(input.html || '');
const payload = input.payload || {};
const eventName = String(input.event || '');
const scriptPath = input.script_path;
const requestedIds = input.ids || [];

if (!eventName) throw new Error('event is required');
if (!scriptPath) throw new Error('script_path is required');

class Element {
  constructor(id, tag) {
    this.id = id || '';
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this._textContent = '';
    this.className = '';
    this.dataset = {};
    this.style = {};
  }

  get textContent() {
    return this._textContent + this.children.map(child => child.textContent).join('');
  }

  set textContent(value) {
    this._textContent = String(value ?? '');
    this.children = [];
  }

  get firstChild() {
    return this.children[0] || null;
  }

  get childElementCount() {
    return this.children.length;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
      child.parentNode = null;
    }
    return child;
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
    const className = /\bclass="([^"]*)"/.exec(match[0]);
    if (className) element.className = decodeHtmlAttr(className[1]);
    elements.set(id, element);
  }
  for (const id of requestedIds) {
    if (!elements.has(id)) elements.set(id, new Element(id, 'div'));
  }
  return elements;
}

const elements = elementsFromHtml(html);
const handlers = {};

const context = {
  console,
  document: {
    getElementById(id) {
      return elements.get(id) || null;
    },
    createElement(tag) {
      return new Element('', tag);
    },
  },
  socket: {
    on(event, handler) {
      if (!handlers[event]) handlers[event] = [];
      handlers[event].push(handler);
    },
  },
  window: {},
};

vm.createContext(context);
vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context, {
  filename: scriptPath,
});

if (!handlers[eventName] || !handlers[eventName].length) {
  throw new Error(`${eventName} handler was not registered`);
}

for (const handler of handlers[eventName]) {
  handler(payload);
}

const output = {
  text: {},
  className: {},
};

for (const id of requestedIds) {
  const element = elements.get(id);
  output.text[id] = element ? element.textContent : null;
  output.className[id] = element ? element.className : null;
}

console.log(JSON.stringify(output));
