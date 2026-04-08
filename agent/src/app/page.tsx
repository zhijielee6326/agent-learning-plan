import ChatComponent from '@/components/chat';

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center bg-slate-100 px-4 py-10 sm:px-6 lg:px-8">
      <div className="w-full max-w-4xl rounded-3xl border border-slate-200 bg-white/95 p-6 shadow-xl shadow-slate-200/50 backdrop-blur-md">
        <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-sm text-slate-500">AI Agent Demo</p>
            <h1 className="text-3xl font-semibold tracking-tight text-slate-950">智能天气聊天助手</h1>
          </div>
          <p className="max-w-md text-sm text-slate-500">输入你的问题，Agent 会返回智能回复。你可用 Anthropic APIKey 或其他支持的 AI 密钥。</p>
        </header>

        <ChatComponent />
      </div>
    </main>
  );
}
