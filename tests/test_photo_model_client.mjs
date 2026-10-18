import assert from 'node:assert/strict';
import {createPhotoModelClient, isDirectCapturePhrase} from '../web/photo_booth/model_client.mjs';

for (const phrase of ['사진 찍어줘', '사진찍어줘.', '사진 찍어 주세요!', '촬영해줘', '지금 사진 찍어줘']) {
  assert.equal(isDirectCapturePhrase(phrase), true, phrase);
}
for (const phrase of ['촬영 화면 바꿔줘', '사진 찍어줘 말고 화면 바꿔줘', '네 컷으로 찍어줘', '다시 찍어줘']) {
  assert.equal(isDirectCapturePhrase(phrase), false, phrase);
}

const response = (result) => async () => ({ok: true, json: async () => result});
const capture = {action: 'capture_photo', args: {}, confirmation_required: true};
let shots = 0;
const handlers = {capture_photo: () => { shots += 1; }};
const client = createPhotoModelClient({fetchImpl: response(capture)});
assert.equal((await client('사진 찍어줘', handlers)).executed, false);
assert.equal(shots, 0);
assert.equal((await client('사진 찍어줘', handlers, {confirmCapture: async () => false})).executed, false);
assert.equal(shots, 0);
assert.equal((await client('사진 찍어줘', handlers, {confirmCapture: async () => true})).executed, true);
assert.equal(shots, 1);

let reason;
const hold = createPhotoModelClient({fetchImpl: response({action: 'no_action', args: {reason: 'ambiguous'}})});
assert.equal((await hold('기본 사진틀을 선택해 줘', {no_action: (args) => { reason = args.reason; }})).executed, false);
assert.equal(reason, 'ambiguous');

let seconds;
const timer = createPhotoModelClient({fetchImpl: response({action: 'set_timer', args: {seconds: 3}})});
assert.equal((await timer('타이머 삼 초', {set_timer: (args) => { seconds = args.seconds; }})).executed, true);
assert.equal(seconds, 3);

const failure = createPhotoModelClient({fetchImpl: async () => ({ok: false, status: 503})});
await assert.rejects(failure('사진 찍어줘', handlers), /503/);
assert.equal(shots, 1);
const unknown = createPhotoModelClient({fetchImpl: response({action: 'delete_files', args: {}})});
await assert.rejects(unknown('명령', {}), /허용되지/);

let resolveOlder;
let count = 0;
const race = createPhotoModelClient({fetchImpl: () => {
  count += 1;
  return count === 1 ? new Promise((resolve) => { resolveOlder = resolve; })
    : Promise.resolve({ok: true, json: async () => ({action: 'set_timer', args: {seconds: 10}})});
}});
const older = race('삼 초', {set_timer: () => { throw new Error('stale command executed'); }});
await race('십 초', {set_timer: (args) => { seconds = args.seconds; }});
resolveOlder({ok: true, json: async () => ({action: 'set_timer', args: {seconds: 3}})});
await assert.rejects(older, /이전 결과/);
assert.equal(seconds, 10);
console.log('Photo model client: capture confirmation, hold, settings, failure, invalid action, stale-result tests passed.');
