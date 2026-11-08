import assert from "node:assert/strict";
import test from "node:test";
import { handleChatComposerKeyDown } from "../src/lib/chat-composer.mjs";

function keyEvent({
  key = "Enter",
  shiftKey = false,
  isComposing = false,
  nativeEvent,
  value = "Hello",
  disabled = false,
} = {}) {
  let prevented = false;
  return {
    event: {
      key,
      shiftKey,
      isComposing,
      nativeEvent,
      currentTarget: { value, disabled },
      preventDefault() {
        prevented = true;
      },
    },
    wasPrevented: () => prevented,
  };
}

test("Enter prevents the newline and submits a non-empty message", () => {
  const input = keyEvent();
  let submissions = 0;

  assert.equal(handleChatComposerKeyDown(input.event, () => submissions++), true);
  assert.equal(input.wasPrevented(), true);
  assert.equal(submissions, 1);
});

test("Shift+Enter is left to the textarea to insert a newline", () => {
  const input = keyEvent({ shiftKey: true });
  let submissions = 0;

  assert.equal(handleChatComposerKeyDown(input.event, () => submissions++), false);
  assert.equal(input.wasPrevented(), false);
  assert.equal(submissions, 0);
});

test("empty or disabled composers do not submit on Enter", () => {
  for (const options of [{ value: " \n " }, { disabled: true }]) {
    const input = keyEvent(options);
    let submissions = 0;

    assert.equal(handleChatComposerKeyDown(input.event, () => submissions++), true);
    assert.equal(input.wasPrevented(), true);
    assert.equal(submissions, 0);
  }
});

test("IME composition Enter is not intercepted", () => {
  for (const options of [
    { isComposing: true },
    { nativeEvent: { isComposing: true } },
    { nativeEvent: { keyCode: 229 } },
  ]) {
    const input = keyEvent(options);
    let submissions = 0;

    assert.equal(handleChatComposerKeyDown(input.event, () => submissions++), false);
    assert.equal(input.wasPrevented(), false);
    assert.equal(submissions, 0);
  }
});
