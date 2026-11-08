export function handleChatComposerKeyDown(event, submit) {
  const nativeEvent = event.nativeEvent;
  if (
    event.key !== "Enter" ||
    event.shiftKey ||
    event.isComposing === true ||
    nativeEvent?.isComposing === true ||
    nativeEvent?.keyCode === 229
  ) {
    return false;
  }

  event.preventDefault();
  if (!event.currentTarget.disabled && event.currentTarget.value.trim()) {
    submit();
  }
  return true;
}
