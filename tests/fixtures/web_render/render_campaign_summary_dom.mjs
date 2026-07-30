import fs from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(input.script_path, 'utf8');
const start = source.indexOf('// --- Campaign summary handler ---');
const end = source.indexOf('// --- Log/Linear scale toggles ---');
if (start < 0 || end <= start) throw new Error('campaign summary handler not found');

class Element {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this._textContent = '';
    this.className = '';
    this.style = {};
    this.open = false;
    this.classList = {
      add: (...names) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        for (const name of names) classes.add(name);
        this.className = [...classes].join(' ');
      },
    };
  }

  get textContent() {
    return this._textContent + this.children.map(child => child.textContent).join('');
  }

  set textContent(value) {
    this._textContent = String(value ?? '');
    this.children = [];
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }
}

const container = new Element('div');
const handlers = {};
const context = {
  document: {
    getElementById(id) {
      return id === 'campaign-summaries' ? container : null;
    },
    createElement(tag) {
      return new Element(tag);
    },
  },
  socket: {
    on(event, handler) {
      handlers[event] = handler;
    },
  },
};

vm.createContext(context);
vm.runInContext(source.slice(start, end), context, {
  filename: input.script_path,
});
handlers.campaign_complete_summary(input.payload);

const details = container.children[0];
console.log(JSON.stringify({
  text: details.textContent,
  open: details.open,
  className: details.className,
}));
