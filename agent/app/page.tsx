import ChatComponent from '@/components/chat';

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-between p-24">
      <div className="z-10 w-full max-w-5xl items-center justify-between font-mono text-sm lg:flex">
        <h1 className="text-2xl font-bold">AI Agent 聊天</h1>
      </div>

      <div className="relative flex place-items-center">
        <ChatComponent />
      </div>
    </main>
  );
}
