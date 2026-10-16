import { create } from 'zustand';

export interface ChatMessage {
  id:        string;
  role:      'user' | 'assistant';
  text:      string;
  payload?:  unknown;
  grounded?: boolean;
  draft?:    boolean; // report-draft pending confirm
  draftId?:  string;
  createdAt: string;
}

interface ChatState {
  messages:     ChatMessage[];
  isOpen:       boolean;
  isThinking:   boolean;
  addMessage:   (msg: ChatMessage) => void;
  setThinking:  (v: boolean) => void;
  toggleChat:   () => void;
  clearChat:    () => void;
}

export const useChatStore = create<ChatState>()((set) => ({
  messages:     [],
  isOpen:       false,
  isThinking:   false,
  addMessage:   (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  setThinking:  (v) => set({ isThinking: v }),
  toggleChat:   () => set((s) => ({ isOpen: !s.isOpen })),
  clearChat:    () => set({ messages: [] }),
}));
