export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
}

export interface VoiceItem {
  identifier: string;
  name: string;
  language: string;
}
