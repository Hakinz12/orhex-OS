export interface ChatComposerKeyEvent {
  key: string;
  shiftKey: boolean;
  isComposing?: boolean;
  nativeEvent?: { isComposing?: boolean; keyCode?: number };
  currentTarget: { disabled: boolean; value: string };
  preventDefault(): void;
}

export function handleChatComposerKeyDown(
  event: ChatComposerKeyEvent,
  submit: () => void,
): boolean;
