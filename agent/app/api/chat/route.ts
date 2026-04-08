import { weatherAgent, type WeatherAgent } from '@/lib/agents/weather-agent';
import { streamText } from 'ai';

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

  const result = streamText({
    model: weatherAgent.model,
    system: weatherAgent.system,
    messages,
  });

  return result.toTextStreamResponse();
}
