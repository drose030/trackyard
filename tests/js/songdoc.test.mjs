import assert from "node:assert/strict";
import { test } from "node:test";

import { SongDoc } from "../../trackyard/editor/static/songdoc.js";
import { TempoMap } from "../../trackyard/editor/static/tempomap.js";

const tempo = new TempoMap(480, [[0, 120]], 0);
const doc = (levels = {}) => new SongDoc({ levels: { expert: [[0, "kick"], [480, "red"]], ...levels },
                                           settings: { hard: { preset: "mpc" } }, tempo });
const pairs = (notes) => notes.map((n) => [n.tick, n.lane]);

test("edits, undo and redo", () => {
  const d = doc();
  assert.ok(!d.dirty && !d.canUndo);
  d.edit((x) => x.levels.expert.push({ tick: 960, lane: "yellow-cym" }));
  assert.deepEqual(pairs(d.levels.expert), [[0, "kick"], [480, "red"], [960, "yellow-cym"]]);
  assert.ok(d.dirty && d.canUndo);
  d.undo();
  assert.equal(d.levels.expert.length, 2);
  d.redo();
  assert.equal(d.levels.expert.length, 3);
});

test("old states stay as they were", () => {
  const d = doc(), before = d.levels;
  d.edit((x) => { x.levels.expert[0].tick = 240; });
  assert.equal(before.expert[0].tick, 0);
  assert.throws(() => { d.levels.expert[0].tick = 5; });
});

test("one note per colour and tick, sorted top row first", () => {
  const d = doc();
  d.edit((x) => x.levels.expert.push({ tick: 480, lane: "kick" }, { tick: 480, lane: "red" }, { tick: 480, lane: "blue-cym" }, { tick: 480, lane: "blue-tom" }));
  assert.deepEqual(pairs(d.levels.expert).filter(([t]) => t === 480), [[480, "red"], [480, "blue-tom"], [480, "kick"]]);
});

test("the edited level decides cymbal or tom on every level", () => {
  const d = doc({ hard: [[960, "blue-cym"]], medium: [[960, "blue-cym"], [1440, "green-cym"]] });
  d.edit((x) => x.levels.medium.push({ tick: 960, lane: "blue-tom" }), { level: "medium" });
  assert.deepEqual(pairs(d.levels.hard), [[960, "blue-tom"]]);
  assert.deepEqual(pairs(d.levels.medium), [[960, "blue-tom"], [1440, "green-cym"]]);
});

test("coalesced edits are one undo step", () => {
  const d = doc();
  for (const bpm of [100, 110, 130]) d.edit((x) => { x.tempo = new TempoMap(480, [[0, bpm]]); }, { coalesce: "drag" });
  assert.equal(d.tempo.tempos[0].bpm, 130);
  d.undo();
  assert.equal(d.tempo.tempos[0].bpm, 120);
  assert.ok(!d.canUndo);
});

test("coalescing stops after the window or another edit", () => {
  const d = doc();
  d.edit((x) => { x.settings.hard = { preset: "custom" }; }, { coalesce: "tune", within: 1000 });
  d.edit((x) => x.levels.expert.pop());
  d.edit((x) => { x.settings.hard = { preset: "mpc" }; }, { coalesce: "tune", within: 1000 });
  assert.equal(d.undoStack.length, 3);
});

test("the save body has the tempo map only when it changed", () => {
  const d = doc();
  assert.deepEqual(Object.keys(d.saveBody()), ["levels", "settings"]);
  d.edit((x) => { x.tempo = x.tempo.withOffset(0.25); });
  assert.deepEqual(d.saveBody().tempos, [[0, 120]]);
  assert.equal(d.saveBody().offset, 0.25);
  d.markSaved();
  assert.ok(!d.dirty && !("tempos" in d.saveBody()));
});

test("listeners hear edits, undo and saves", () => {
  const d = doc(), heard = [];
  d.onChange((what) => heard.push(Object.keys(what)[0]));
  d.edit((x) => x.levels.expert.pop());
  d.undo();
  d.markSaved();
  assert.deepEqual(heard, ["edit", "undo", "saved"]);
});

test("pad sounds: undoable, and only changed voices are saved", () => {
  const d = new SongDoc({ levels: {}, settings: {}, tempo, sounds: { red: { file: "red.ogg" }, kick: { start: 1, length: 0.3 } } });
  assert.ok(!("sounds" in d.saveBody()));
  d.edit((x) => { x.sounds.kick = { start: 2, length: 0.4 }; x.sounds.orange = { start: 5, length: 1.2 }; });
  assert.deepEqual(d.saveBody().sounds, { kick: { start: 2, length: 0.4 }, orange: { start: 5, length: 1.2 } });
  d.edit((x) => { x.sounds.red = null; });
  assert.deepEqual(d.saveBody().sounds.red, null);
  d.undo();
  assert.deepEqual(d.sounds.red, { file: "red.ogg" });
  d.markSaved();
  assert.ok(!("sounds" in d.saveBody()));
});
