export type ConversationRole = "system" | "user" | "assistant";

export interface ConversationMessage {
  role: ConversationRole;
  content: string;
}

export interface GrokTextRequest {
  requestId: string;
  messages: ConversationMessage[];
}

export interface GrokTextResponse {
  requestId: string;
  text: string;
  latencyMillis: number;
}
