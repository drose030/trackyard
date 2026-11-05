// The chart's tempo map in the editor: tempo anchors (tick → BPM), the offset (seconds
// added to chart time to get audio time) and the time signatures. Immutable: every
// change returns a new map, so undo can keep the old one as it is.
//
// Every tempo event is an anchor, a tick pinned to a time. Pinning a beat to a new time
// recomputes the tempo on both sides; notes keep their ticks, so they move with the grid.
// trackyard/tempomap.py is the same math on the Python side (tests/test_editor_js.py
// checks they agree).

export const MIN_BPM = 20;
export const MAX_BPM = 400;

export class TempoMap {
  constructor(ppq, tempos, offset = 0, signatures = []) {
    this.ppq = ppq;
    this.offset = offset;
    this.signatures = signatures.length && signatures[0].tick === 0 ? signatures : [{ tick: 0, num: 4, den: 4 }, ...signatures];
    const anchors = tempos.map((t) => (Array.isArray(t) ? { tick: t[0], bpm: t[1] } : { tick: t.tick, bpm: t.bpm }));
    if (!anchors.length || anchors[0].tick !== 0) anchors.unshift({ tick: 0, bpm: anchors[0]?.bpm ?? 120 });
    let sec = 0;
    this.tempos = anchors.map((t, i) => {
      if (i > 0) sec += (t.tick - anchors[i - 1].tick) / ppq * 60 / anchors[i - 1].bpm;
      return Object.freeze({ tick: t.tick, bpm: t.bpm, sec });
    });
    Object.freeze(this.tempos);
    Object.freeze(this);
  }

  // The anchor in force at `tick`.
  at(tick) {
    const t = this.tempos;
    let i = 0;
    while (i + 1 < t.length && t[i + 1].tick <= tick) i++;
    return t[i];
  }

  // Audio seconds of a tick.
  seconds(tick) {
    const t = this.at(tick);
    return t.sec + (tick - t.tick) / this.ppq * 60 / t.bpm + this.offset;
  }

  // Fractional tick at audio seconds.
  tick(seconds) {
    const s = seconds - this.offset, t = this.tempos;
    let i = 0;
    while (i + 1 < t.length && t[i + 1].sec <= s) i++;
    return t[i].tick + (s - t[i].sec) * t[i].bpm / 60 * this.ppq;
  }

  isAnchor(tick) {
    return this.tempos.some((t) => t.tick === tick);
  }

  // Pin `tick` to audio second `seconds`. Tick 0 carries the whole chart: that moves the
  // offset. Elsewhere the tempos on both sides change, kept between MIN_BPM and MAX_BPM.
  pin(tick, seconds) {
    if (tick === 0) return this.withOffset(seconds);
    const sec = seconds - this.offset, ppq = this.ppq;
    const t = this.tempos.map((x) => ({ ...x }));
    let a = t.findIndex((x) => x.tick === tick);
    if (a < 0) {
      a = t.findLastIndex((x) => x.tick < tick) + 1;
      t.splice(a, 0, { tick, bpm: t[a - 1].bpm, sec: this.seconds(tick) - this.offset });
    }
    const prev = t[a - 1], next = t[a + 1];
    const span = (from, to, bpm) => (to - from) / ppq * 60 / bpm;
    let lo = prev.sec + span(prev.tick, tick, MAX_BPM), hi = prev.sec + span(prev.tick, tick, MIN_BPM);
    if (next) {
      lo = Math.max(lo, next.sec - span(tick, next.tick, MIN_BPM));
      hi = Math.min(hi, next.sec - span(tick, next.tick, MAX_BPM));
    }
    const s = Math.min(hi, Math.max(lo, sec));
    prev.bpm = (tick - prev.tick) / ppq * 60 / (s - prev.sec);
    if (next) t[a].bpm = (next.tick - tick) / ppq * 60 / (next.sec - s);
    return new TempoMap(ppq, t, this.offset, this.signatures);
  }

  // Remove an anchor; the anchors around it keep their times.
  withoutAnchor(tick) {
    const t = this.tempos.map((x) => ({ ...x })), a = t.findIndex((x) => x.tick === tick);
    if (a <= 0) return this;
    const prev = t[a - 1], next = t[a + 1];
    if (next) prev.bpm = (next.tick - prev.tick) / this.ppq * 60 / (next.sec - prev.sec);
    t.splice(a, 1);
    return new TempoMap(this.ppq, t, this.offset, this.signatures);
  }

  withOffset(offset) {
    return new TempoMap(this.ppq, this.tempos, offset, this.signatures);
  }

  // [[tick, bpm], …] as the server takes them.
  pairs() {
    return this.tempos.map((t) => [t.tick, t.bpm]);
  }

  equals(other) {
    return JSON.stringify([this.pairs(), this.offset]) === JSON.stringify([other.pairs(), other.offset]);
  }

  // Beats up to `endTick`, each {tick, down}; one beat is one note of the signature's
  // denominator (a quarter in x/4).
  beats(endTick) {
    const out = [];
    const sigs = [...this.signatures, { tick: endTick + 1 }];
    for (let i = 0; i + 1 < sigs.length; i++) {
      const { tick: start, num, den } = sigs[i];
      const step = this.ppq * 4 / den, until = Math.min(sigs[i + 1].tick, endTick + 1);
      for (let k = 0; start + k * step < until; k++) out.push({ tick: Math.round(start + k * step), down: k % num === 0 });
    }
    return out;
  }

  signatureAt(tick) {
    return [...this.signatures].reverse().find((s) => s.tick <= tick);
  }
}
