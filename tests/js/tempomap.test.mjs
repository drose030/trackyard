import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_BPM, MIN_BPM, TempoMap } from "../../trackyard/editor/static/tempomap.js";

const close = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} ≉ ${b}`);
const map = () => new TempoMap(480, [[0, 120], [1920, 60]], 0.5, [{ tick: 0, num: 4, den: 4 }]);

test("ticks and audio seconds convert both ways, offset included", () => {
  const m = map();
  close(m.seconds(0), 0.5);
  close(m.seconds(1920), 2.5);
  close(m.seconds(2400), 3.5);
  for (const tick of [0, 100, 1920, 2000, 5000]) close(m.tick(m.seconds(tick)), tick, 1e-6);
});

test("pinning a beat retimes both sides and keeps later anchors in place", () => {
  const m = map(), later = m.seconds(1920);
  const p = m.pin(960, m.seconds(960) + 0.1);
  close(p.seconds(960), m.seconds(960) + 0.1);
  close(p.seconds(1920), later);
  assert.ok(p.isAnchor(960) && !m.isAnchor(960));  // m itself is unchanged
});

test("pinning keeps tempos within the limits", () => {
  const p = map().pin(960, 0.5 + 0.0001);  // far too early
  for (const t of p.tempos) assert.ok(t.bpm <= MAX_BPM + 1e-6 && t.bpm >= MIN_BPM - 1e-6, t.bpm);
});

test("pinning tick 0 moves the offset", () => {
  const p = map().pin(0, 0.75);
  assert.equal(p.offset, 0.75);
  assert.deepEqual(p.pairs(), map().pairs());
});

test("removing an anchor keeps its neighbours' times", () => {
  const m = map().pin(960, 1.6), end = m.seconds(1920);
  const r = m.withoutAnchor(960);
  assert.ok(!r.isAnchor(960));
  close(r.seconds(1920), end);
  const plain = map();
  assert.equal(plain.withoutAnchor(0), plain);  // the first anchor stays
});

test("beats follow the time signatures", () => {
  const m = new TempoMap(480, [[0, 120]], 0, [{ tick: 0, num: 4, den: 4 }, { tick: 1920, num: 3, den: 4 }]);
  const b = m.beats(3360);
  assert.deepEqual(b.map((x) => x.tick), [0, 480, 960, 1440, 1920, 2400, 2880, 3360]);
  assert.deepEqual(b.filter((x) => x.down).map((x) => x.tick), [0, 1920, 3360]);
});

test("equals compares anchors and offset", () => {
  assert.ok(map().equals(map()));
  assert.ok(!map().equals(map().withOffset(0)));
});
