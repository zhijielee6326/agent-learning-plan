'use client';

import { useState } from 'react';

type ChatMessage = {
  role: 'user' | 'assistant';
  content: string;
};

export default function ChatComponent() {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInput(e.target.value);
  };

  const onSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage: ChatMessage = { role: 'user', content: input.trim() };
    const nextMessages = [...messages, userMessage];
    setMessages(nextMessages);
    setInput('');
    setIsLoading(true);

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: nextMessages }),
      });

      if (!response.ok) {
        throw new Error('聊天请求失败');
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let assistantContent = '';

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          assistantContent += decoder.decode(value, { stream: true });
        }
      }

      setMessages((prev) => [...prev, { role: 'assistant', content: assistantContent || '抱歉，未收到回复。' }]);
    } catch (error) {
      console.error(error);
      setMessages((prev) => [...prev, { role: 'assistant', content: '请求出错，请稍后重试。' }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="min-h-[320px] space-y-4 rounded-3xl border border-slate-200 bg-slate-50 p-4 shadow-sm">
        {messages.length === 0 ? (
          <div className="rounded-3xl border border-dashed border-slate-300 bg-white/80 p-6 text-center text-slate-500">
            这里显示问答记录，输入问题开始对话。
          </div>
        ) : (
          messages.map((message, index) => (
            <div
              key={index}
              className={`rounded-3xl p-4 shadow-sm ${
                message.role === 'user'
                  ? 'self-end bg-sky-600 text-white'
                  : 'self-start bg-white text-slate-900'
              }`}
            >
              <div className="text-xs uppercase tracking-[0.24em] opacity-70">
                {message.role === 'user' ? '用户' : '助手'}
              </div>
              <div className="mt-2 whitespace-pre-wrap text-sm leading-6">{message.content}</div>
            </div>
          ))
        )}
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-3 rounded-3xl border border-slate-200 bg-white p-4 shadow-sm sm:flex-row sm:items-center">
        <input
          className="flex-1 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-slate-900 outline-none transition focus:border-sky-500 focus:ring-2 focus:ring-sky-100 disabled:cursor-not-allowed disabled:opacity-60"
          value={input}
          onChange={handleInputChange}
          placeholder="请输入你的问题，例如：明天天气如何？"
          disabled={isLoading}
        />
        <button
          type="submit"
          disabled={isLoading}
          className="inline-flex h-12 items-center justify-center rounded-2xl bg-sky-600 px-6 text-sm font-semibold text-white transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isLoading ? '正在发送...' : '发送'}
        </button>
      </form>
    </div>
  );
}
