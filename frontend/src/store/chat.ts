import { create } from 'zustand';

export interface ChatMessage {
  id:        string;
  role:      'user' | 'assistant';
  text:      string;
  payload?:  unknown;
  grounded?: boolean;
  draft?:    boolean; // report-draft pending confirm
  createdAt: string;
}

interface ChatState {
  sessionId:    string | null;
  messages:     ChatMessage[];
  isOpen:       boolean;
  isThinking:   boolean;
  setSessionId: (id: string) => void;
  addMessage:   (msg: ChatMessage) => void;
  setThinking:  (v: boolean) => void;
  toggleChat:   () => void;
  clearChat:    () => void;
}

export const useChatStore = create<ChatState>()((set) => ({
  sessionId:    null,
  messages:     [],
  isOpen:       false,
  isThinking:   false,
  setSessionId: (id) => set({ sessionId: id }),
  addMessage:   (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  setThinking:  (v) => set({ isThinking: v }),
  toggleChat:   () => set((s) => ({ isOpen: !s.isOpen })),
  clearChat:    () => set({ messages: [], sessionId: null }),
}));
