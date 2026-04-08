import { weatherAgent, type WeatherAgent } from '@/lib/agents/weather-agent';
import { streamText } from 'ai';

// 定义消息类型 (由于 InferAgentUIMessage 可能不可用，使用标准类型)
export type WeatherMessage = {
  role: 'user' | 'assistant' | 'tool';
  content: string;
  toolResults?: Array<{
    toolCallId: string;
    toolName: string;
    result: any;
  }>;
  toolCallId?: string;
};

export async function POST(req: Request) {
  const { messages } = await req.json();

  // ✅ messages 现在有完整的类型!
  // messages: WeatherMessage[]

  const result = streamText({
    model: weatherAgent.model,
    system: weatherAgent.system,
    messages,
  });

  return result.toTextStreamResponse();
}