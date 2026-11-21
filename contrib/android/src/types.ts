export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'error';
  text: string;
}

export interface VoiceItem {
  identifier: string;
  name: string;
  language: string;
}
