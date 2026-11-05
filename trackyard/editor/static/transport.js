// Playback: the song's stems on <audio> elements (they keep the pitch when slowed down),
// kept in step with each other, plus note ticks and a metronome synthesized with Web
// Audio a little ahead of time. What to click comes from the editor: `sounds(from,
// until)` returns [{sec, sound}] for that stretch of audio time, where sound is a chart
// lane (a note tick), "beat" or "downbeat" (the metronome).

const LOOKAHEAD = 0.12;  // seconds of clicks scheduled ahead
const DRIFT = 0.04;  // a stem further off than this is pulled back

export class Transport {
  constructor({ sounds, onState = () => {}, levels = {} }) {
    this.sounds = sounds;
    this.onState = onState;  // (playing) after play, pause and the end of the song
    this.ctx = null;
    this.els = {};  // stem → <audio>
    this.muted = {};
    this.levels = levels;  // "stem:<name>", "ticks", "metronome" → volume
    this.playing = false;
    this.pausedAt = 0;
    this.speed = 1;
    this.scheduledUntil = 0;
    this.timer = null;
  }

  // stems: [{stem, url, muted}]. Returns stem → decoded AudioBuffer (for waveforms and
  // drum-hit detection); stems that fail to load are left out.
  async load(stems) {
    this.stop();
    this.els = {};
    this.muted = {};
    if (!this.ctx) {
      this.ctx = new AudioContext();
      for (const bus of ["ticks", "metronome"]) {
        this[bus] = this.ctx.createGain();
        this[bus].gain.value = this.levels[bus] ?? 1;
        this[bus].connect(this.ctx.destination);
      }
    }
    const buffers = {};
    await Promise.all(stems.map(async ({ stem, url, muted }) => {
      try {
        buffers[stem] = await this.ctx.decodeAudioData(await (await fetch(url)).arrayBuffer());
        const el = new Audio(url);
        el.preload = "auto";
        el.preservesPitch = true;
        el.webkitPreservesPitch = true;
        el.muted = this.muted[stem] = Boolean(muted);
        el.volume = Math.min(1, this.levels[`stem:${stem}`] ?? 1);
        el.addEventListener("ended", () => { if (el === this.reference()) this.pause(); });
        this.els[stem] = el;
      } catch (e) {
        console.warn("could not load", stem, e);
        delete buffers[stem];
      }
    }));
    return buffers;
  }

  // The longest stem sets the time.
  reference() {
    const els = Object.values(this.els);
    return els.reduce((a, b) => ((b.duration || 0) > (a.duration || 0) ? b : a), els[0]);
  }

  position() {
    const ref = this.playing && this.reference();
    return ref ? ref.currentTime : this.pausedAt;
  }

  async play() {
    if (this.playing || !this.ctx) return;
    this.ctx.resume();
    const from = Math.max(0, this.pausedAt);
    for (const el of Object.values(this.els)) {
      el.currentTime = Math.min(from, el.duration || from);
      el.playbackRate = this.speed;
    }
    await Promise.all(Object.values(this.els).map((el) => el.play().catch(() => {})));
    this.scheduledUntil = from;
    this.playing = true;
    // Clicks run on their own timer: animation frames stop in background tabs.
    clearInterval(this.timer);
    this.timer = setInterval(() => this.tick(), 25);
    this.onState(true);
  }

  pause() {
    this.pausedAt = this.position();
    this.stop();
  }

  stop() {
    for (const el of Object.values(this.els)) el.pause();
    clearInterval(this.timer);
    const was = this.playing;
    this.playing = false;
    if (was) this.onState(false);
  }

  seek(sec) {
    const was = this.playing;
    if (was) this.pause();
    this.pausedAt = Math.max(0, sec);
    if (was) this.play();
  }

  setSpeed(speed) {
    this.speed = speed;
    for (const el of Object.values(this.els)) el.playbackRate = speed;
  }

  // key: "stem:<name>", "ticks" or "metronome". Stems go up to 100 % (an <audio> element
  // can't be louder), the synthesized buses higher.
  setVolume(key, value) {
    this.levels[key] = value;
    if (key.startsWith("stem:")) {
      const el = this.els[key.slice(5)];
      if (el) el.volume = Math.min(1, value);
    } else if (this[key]) {
      this[key].gain.value = value;
    }
  }

  toggleMute(stem) {
    this.muted[stem] = !this.muted[stem];
    if (this.els[stem]) this.els[stem].muted = this.muted[stem];
  }

  tick() {
    if (!this.playing) return;
    const ref = this.reference(), pos = this.position();
    for (const el of Object.values(this.els)) {  // keep the stems together
      if (el !== ref && Math.abs(el.currentTime - pos) > DRIFT) el.currentTime = pos;
    }
    // After a seek (or if the audio ever jumps), start scheduling from where it plays now.
    if (Math.abs(this.scheduledUntil - pos) > 0.5) this.scheduledUntil = pos;
    const until = pos + LOOKAHEAD, from = Math.max(this.scheduledUntil, pos - 0.02);
    if (until <= from) return;
    const at = (s) => this.ctx.currentTime + (s - pos) / this.speed;
    for (const { sec, sound } of this.sounds(from, until)) {
      if (sound === "beat" || sound === "downbeat") this.beep(sound === "downbeat" ? 1600 : 1100, at(sec));
      else this.click(sound, at(sec));
    }
    this.scheduledUntil = until;
  }

  click(lane, when) {
    const ctx = this.ctx;
    when = Math.max(when, ctx.currentTime);
    const gain = ctx.createGain();
    gain.connect(this.ticks);
    if (lane === "kick" || lane.endsWith("-tom")) {
      const osc = ctx.createOscillator();
      osc.frequency.setValueAtTime(lane === "kick" ? 110 : { yellow: 330, blue: 240, green: 170 }[lane.split("-")[0]], when);
      osc.frequency.exponentialRampToValueAtTime(lane === "kick" ? 45 : 90, when + 0.08);
      gain.gain.setValueAtTime(0.5, when);
      gain.gain.exponentialRampToValueAtTime(0.001, when + 0.09);
      osc.connect(gain);
      osc.start(when); osc.stop(when + 0.1);
      return;
    }
    const len = lane === "red" ? 0.06 : 0.03;
    const buf = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * len), ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    const filter = ctx.createBiquadFilter();
    filter.type = lane === "red" ? "bandpass" : "highpass";
    filter.frequency.value = lane === "red" ? 1800 : 7000;
    gain.gain.value = lane === "red" ? 0.5 : 0.3;
    src.connect(filter); filter.connect(gain);
    src.start(when);
  }

  beep(freq, when) {
    when = Math.max(when, this.ctx.currentTime);
    const osc = this.ctx.createOscillator(), gain = this.ctx.createGain();
    osc.type = "square";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.08, when);
    gain.gain.exponentialRampToValueAtTime(0.001, when + 0.04);
    osc.connect(gain); gain.connect(this.metronome);
    osc.start(when); osc.stop(when + 0.05);
  }
}
