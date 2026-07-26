export type ConversationRole = "system" | "user" | "assistant";

export interface ConversationMessage {
  role: ConversationRole;
  content: string;
}

export interface CodexTextRequest {
  requestId: string;
  messages: ConversationMessage[];
}

export interface CodexTextResponse {
  requestId: string;
  text: string;
  latencyMillis: number;
}
