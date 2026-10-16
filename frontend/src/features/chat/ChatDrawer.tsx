import React, { useState, useRef, useEffect } from 'react';
import {
  Box, TextField, IconButton, Typography, Paper, Button,
  Divider, Drawer,
} from '@mui/material';
import SendIcon from '@mui/icons-material/Send';
import CloseIcon from '@mui/icons-material/Close';
import ShieldIcon from '@mui/icons-material/Shield';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '../../store/chat';
import { useUIStore } from '../../store/ui';
import { sendChat } from '../../api/endpoints';
import { GroundingBadge } from '../../components/Feedback';
import type { ChatMessage } from '../../store/chat';

const MessageBubble: React.FC<{ msg: ChatMessage }> = ({ msg }) => {
  const { t } = useTranslation();
  const isUser = msg.role === 'user';

  return (
    <Box sx={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', mb: 1.5 }}>
      <Box
        sx={{
          maxWidth: '80%', px: 1.75, py: 1.25, borderRadius: 2,
          backgroundColor: isUser ? 'primary.main' : 'background.default',
          color:           isUser ? 'primary.contrastText' : 'text.primary',
          border:          isUser ? 'none' : '1px solid',
          borderColor:     'divider',
        }}
      >
        <Typography variant="body2" sx={{ lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
          {msg.text}
        </Typography>
        {!isUser && msg.grounded !== undefined && (
          <Box sx={{ mt: 0.75 }}>
            <GroundingBadge ok={msg.grounded!} />
          </Box>
        )}
        {!isUser && msg.draft && (
          <Box sx={{ mt: 1, display: 'flex', gap: 1 }}>
            <Button size="small" variant="contained" color="error" sx={{ height: 28, fontSize: '0.75rem' }}>
              {t('chat.confirm')}
            </Button>
            <Button size="small" variant="outlined" sx={{ height: 28, fontSize: '0.75rem' }}>
              {t('chat.notNow')}
            </Button>
          </Box>
        )}
      </Box>
    </Box>
  );
};

const ChatPanel: React.FC = () => {
  const { t } = useTranslation();
  const { lang } = useUIStore();
  const { sessionId, messages, isThinking, setSessionId, addMessage, setThinking } = useChatStore();
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'auto' });
  }, [messages, isThinking]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text) return;
    setInput('');
    addMessage({ id: crypto.randomUUID(), role: 'user', text, createdAt: new Date().toISOString() });
    setThinking(true);
    try {
      const res = await sendChat(sessionId, text, lang as 'en' | 'bn');
      if (!sessionId) setSessionId(res.session_id);
      addMessage({
        id:        crypto.randomUUID(),
        role:      'assistant',
        text:      res.reply,
        grounded:  res.grounded,
        payload:   res.payload,
        createdAt: new Date().toISOString(),
      });
    } catch (e) {
      addMessage({ id: crypto.randomUUID(), role: 'assistant', text: t('common.error'), createdAt: new Date().toISOString() });
    } finally {
      setThinking(false);
    }
  };

  const SUGGESTIONS = [
    'Mirpur 10 theke Motijheel jete chai, budget 80 taka',
    'Shahbagh e ki kono traffic ache?',
    'Student bus fare ki always half?',
    'Alternative route dao',
  ];

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <Box sx={{ p: 2, borderBottom: '1px solid', borderColor: 'divider', display: 'flex', alignItems: 'center', gap: 1.5 }}>
        <Box sx={{ width: 36, height: 36, borderRadius: 1, background: '#E6B800', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, border: '2px solid #BF9A00' }}>🤖</Box>
        <Box>
          <Typography variant="subtitle2" fontWeight={700} sx={{ fontFamily: 'var(--font-heading)' }}>
            {t('chat.title')}
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <ShieldIcon sx={{ fontSize: 12, color: 'success.main' }} />
            <Typography variant="caption" color="success.main">{t('chat.grounded')}</Typography>
          </Box>
        </Box>
      </Box>

      {/* Messages */}
      <Box sx={{ flex: 1, overflowY: 'auto', p: 2, display: 'flex', flexDirection: 'column' }}>
        {messages.length === 0 && (
          <Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {t('chat.suggestions')}:
            </Typography>
            {SUGGESTIONS.map((s, i) => (
              <Button
                key={i} variant="outlined" size="small" fullWidth
                onClick={() => setInput(s)}
                sx={{ mb: 1, justifyContent: 'flex-start', textAlign: 'left', borderRadius: 1.5, fontSize: '0.8125rem', color: 'text.secondary', borderColor: 'divider', '&:hover': { borderColor: 'primary.main', color: 'primary.main' } }}
              >
                {s}
              </Button>
            ))}
          </Box>
        )}

        {messages.map((msg) => <MessageBubble key={msg.id} msg={msg} />)}

        {isThinking && (
          <Box sx={{ display: 'flex', justifyContent: 'flex-start', mb: 1.5 }}>
            <Box sx={{ px: 1.75, py: 1.25, borderRadius: 2, backgroundColor: 'background.default', border: '1px solid', borderColor: 'divider' }}>
              <Typography variant="body2" color="text.secondary">{t('chat.thinking')}</Typography>
            </Box>
          </Box>
        )}
        <div ref={bottomRef} />
      </Box>

      {/* Input */}
      <Box sx={{ p: 2, borderTop: '1px solid', borderColor: 'divider', display: 'flex', gap: 1 }}>
        <TextField
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
          placeholder={t('chat.placeholder')}
          size="small"
          fullWidth
          multiline
          maxRows={3}
        />
        <IconButton onClick={handleSend} disabled={!input.trim() || isThinking}
          sx={{ backgroundColor: 'primary.main', color: '#1A1600', borderRadius: 1.5, width: 40, height: 40, flexShrink: 0, '&:hover': { backgroundColor: 'primary.dark' }, '&:disabled': { backgroundColor: 'divider' } }}>
          <SendIcon fontSize="small" />
        </IconButton>
      </Box>
    </Box>
  );
};

const ChatDrawer: React.FC = () => {
  const { isOpen, toggleChat } = useChatStore();

  return (
    <Drawer
      anchor="right"
      open={isOpen}
      onClose={toggleChat}
      PaperProps={{ sx: { width: { xs: '100vw', sm: 380 }, height: '100%' } }}
    >
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', p: 1 }}>
        <IconButton onClick={toggleChat} size="small"><CloseIcon /></IconButton>
      </Box>
      <Box sx={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <ChatPanel />
      </Box>
    </Drawer>
  );
};

export default ChatDrawer;
