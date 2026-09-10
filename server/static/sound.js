'use strict';

class AttentionSound {
  constructor(button) {
    this.button = button;
    this.context = null;
    this.previous = null;
    this.enabled = false;
    this.voices = new Set();
    try { this.enabled = localStorage.getItem('slopwatchdeluxe-sound') === 'on'; } catch {}
    this.supported = Boolean(window.AudioContext || window.webkitAudioContext);
    button.addEventListener('click', () => {
      if (this.enabled && this.context?.state === 'running') this.mute();
      else void this.enable();
    });
    // A saved preference still needs a browser gesture after loading the page.
    const resume = event => {
      if (this.enabled && this.context?.state !== 'running' && !button.contains(event.target)) void this.enable(false);
    };
    document.addEventListener('click', resume);
    document.addEventListener('keydown', resume);
    this.render();
  }

  save() {
    try { localStorage.setItem('slopwatchdeluxe-sound', this.enabled ? 'on' : 'off'); } catch {}
  }

  render() {
    const playing = this.enabled && this.context?.state === 'running';
    this.button.disabled = !this.supported;
    this.button.textContent = !this.supported ? 'Sound unavailable' : playing ? 'Sound on' : this.enabled ? 'Resume sound' : 'Sound off';
    this.button.setAttribute('aria-pressed', String(Boolean(playing)));
    this.button.setAttribute('aria-label', playing ? 'Mute attention sounds' : 'Enable attention sounds');
    this.button.title = playing ? 'Chime when a session needs attention' : 'Click to enable attention sounds';
  }

  async enable(preview = true) {
    if (!this.supported) return;
    this.enabled = true;
    try {
      if (!this.context || this.context.state === 'closed') {
        const Audio = window.AudioContext || window.webkitAudioContext;
        this.context = new Audio();
        this.context.addEventListener('statechange', () => this.render());
      }
      if (this.context.state !== 'running') await this.context.resume();
      this.save();
      this.render();
      if (preview) this.chime();
    } catch {
      this.enabled = false;
      this.save();
      this.render();
    }
  }

  mute() {
    this.enabled = false;
    for (const {oscillator, gain} of this.voices) {
      try { oscillator.stop(); } catch {}
      try { oscillator.disconnect(); gain.disconnect(); } catch {}
    }
    this.voices.clear();
    this.save();
    this.render();
  }

  update(sessions) {
    const next = new Set(sessions.filter(s => s.state === 'ATTENTION' && !s.archived_at).map(s => s.id));
    const newlyWaiting = this.previous !== null && [...next].some(id => !this.previous.has(id));
    this.previous = next;
    if (newlyWaiting) this.chime();
  }

  chime() {
    if (!this.enabled || this.context?.state !== 'running') return;
    try {
      const start = this.context.currentTime;
      for (const [index, frequency] of [660, 880].entries()) {
        const oscillator = this.context.createOscillator();
        const gain = this.context.createGain();
        const voice = {oscillator, gain};
        const at = start + index * 0.14;
        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(frequency, at);
        gain.gain.setValueAtTime(0, at);
        gain.gain.linearRampToValueAtTime(0.12, at + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.001, at + 0.28);
        oscillator.connect(gain);
        gain.connect(this.context.destination);
        this.voices.add(voice);
        oscillator.onended = () => {
          oscillator.disconnect(); gain.disconnect(); this.voices.delete(voice);
        };
        oscillator.start(at);
        oscillator.stop(at + 0.3);
      }
    } catch {
      // Audio failure must not interrupt dashboard refreshes.
      this.mute();
    }
  }
}
