const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(`${__dirname}/../server/static/sound.js`, 'utf8');

function setup({saved = 'off', unsupported = false, failResume = false, blockedStorage = false} = {}) {
  const values = new Map([['slopwatchdeluxe-sound', saved]]), oscillators = [], gestures = {};
  const button = {textContent: '', attrs: {}, disabled: false, addEventListener() {}, contains: target => target === button,
    setAttribute(name, value) { this.attrs[name] = value; }};
  class Audio {
    constructor() { this.state = 'suspended'; this.currentTime = 0; this.destination = {}; }
    addEventListener() {}
    async resume() { if (failResume) throw Error('Audio blocked'); this.state = 'running'; }
    createOscillator() {
      const oscillator = {frequency: {setValueAtTime() {}}, connect() {}, start() {this.started = true;},
        stop() {this.stopped = true;}, disconnect() {this.disconnected = true;}};
      oscillators.push(oscillator); return oscillator;
    }
    createGain() { return {gain: {setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {}}, connect() {}, disconnect() {}}; }
  }
  const context = vm.createContext({window: unsupported ? {} : {AudioContext: Audio},
    document: {addEventListener(name, callback) { gestures[name] = callback; }},
    localStorage: {getItem(key) {if (blockedStorage) throw Error('Storage blocked'); return values.get(key);},
      setItem(key, value) {if (blockedStorage) throw Error('Storage blocked'); values.set(key, value);}}});
  vm.runInContext(source + '\nglobalThis.Sound = AttentionSound;', context);
  const sound = new context.Sound(button);
  return {sound, button, values, oscillators, gestures};
}
const session = (id, state = 'ATTENTION', archived_at = null) => ({id, state, archived_at});

test('initial attention cards are quiet; only newly waiting sessions chime', async () => {
  const {sound, oscillators} = setup(); await sound.enable(false);
  sound.update([session('a')]); assert.equal(oscillators.length, 0);
  sound.update([session('a')]); assert.equal(oscillators.length, 0);
  sound.update([session('a'), session('b')]); assert.equal(oscillators.length, 2);
  sound.update([session('a'), session('b')]); assert.equal(oscillators.length, 2);
});

test('detects a new waiting session even when the attention count stays the same', async () => {
  const {sound, oscillators} = setup(); await sound.enable(false);
  sound.update([session('a')]); sound.update([session('a', 'IDLE'), session('b')]);
  assert.equal(oscillators.length, 2);
});

test('a session can alert again after returning to work; simultaneous arrivals share a chime', async () => {
  const {sound, oscillators} = setup(); await sound.enable(false);
  sound.update([session('a')]); sound.update([session('a', 'WORKING')]);
  sound.update([session('a'), session('b')]); assert.equal(oscillators.length, 2);
});

test('archived, closed, idle, and working sessions stay quiet', async () => {
  const {sound, oscillators} = setup(); await sound.enable(false);
  sound.update([]);
  sound.update([session('a', 'ATTENTION', 'now'), session('b', 'CLOSED'), session('c', 'IDLE'), session('d', 'WORKING')]);
  assert.equal(oscillators.length, 0);
});

test('mute persists, stops current notes, and does not queue missed alerts', async () => {
  const {sound, oscillators, values, button} = setup();
  sound.update([]); await sound.enable(); assert.equal(oscillators.length, 2);
  assert.equal(values.get('slopwatchdeluxe-sound'), 'on');
  sound.mute(); assert.ok(oscillators.every(o => o.stopped && o.disconnected));
  assert.equal(values.get('slopwatchdeluxe-sound'), 'off'); assert.equal(button.attrs['aria-pressed'], 'false');
  sound.update([session('a')]); assert.equal(oscillators.length, 2);
  await sound.enable(false); sound.update([session('a')]); assert.equal(oscillators.length, 2);
  sound.update([session('b')]); assert.equal(oscillators.length, 4);
});

test('remembered preference requests a gesture and resumes without an unsolicited preview', async () => {
  const {sound, button, oscillators, gestures} = setup({saved: 'on'});
  assert.equal(button.textContent, 'Resume sound'); assert.equal(button.attrs['aria-pressed'], 'false');
  sound.update([]); sound.update([session('a')]); assert.equal(oscillators.length, 0);
  gestures.click({target: {}}); await new Promise(setImmediate);
  assert.equal(button.textContent, 'Sound on'); assert.equal(oscillators.length, 0);
  sound.update([session('a'), session('b')]); assert.equal(oscillators.length, 2);
});

test('audio rejection and unavailable audio leave a usable, quiet dashboard', async () => {
  const {sound, button, values} = setup({failResume: true}); await sound.enable();
  assert.equal(button.textContent, 'Sound off'); assert.equal(values.get('slopwatchdeluxe-sound'), 'off');
  assert.doesNotThrow(() => {sound.update([]); sound.update([session('a')]);});
  const unsupported = setup({unsupported: true}); assert.equal(unsupported.button.disabled, true);
  await unsupported.sound.enable();
});

test('storage restrictions do not break the toggle or session updates', async () => {
  const {sound, oscillators} = setup({blockedStorage: true});
  sound.update([]); await sound.enable(false); sound.update([session('a')]);
  assert.equal(oscillators.length, 2); assert.doesNotThrow(() => sound.mute());
});
