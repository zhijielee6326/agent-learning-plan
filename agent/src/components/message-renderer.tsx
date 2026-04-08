import type { WeatherMessage } from '@/app/api/chat/route';

interface MessageRendererProps {
  message: WeatherMessage;
}

export function MessageRenderer({ message }: MessageRendererProps) {
  // ✅ 类型安全地处理不同类型的消息
  switch (message.role) {
    case 'user':
      return <UserMessage content={message.content} />;

    case 'assistant':
      return <AssistantMessage message={message} />;

    case 'tool':
      return <ToolResult message={message} />;

    default:
      return null;
  }
}

function AssistantMessage({ message }: { message: WeatherMessage }) {
  // ✅ 安全地访问 toolResults
  if (!message.toolResults || message.toolResults.length === 0) {
    return <div>{message.content}</div>;
  }

  return (
    <div>
      <div>{message.content}</div>
      <div className="tool-results">
        {message.toolResults.map((result) => {
          // ✅ result.toolName 是字面量类型
          switch (result.toolName) {
            case 'getWeather':
              // ✅ result.result 的类型正确推断
              const weather = result.result; // { city, temperature, unit, condition }
              return (
                <div key={result.toolCallId}>
                  <h4>{weather.city}天气</h4>
                  <p>{weather.temperature}°{weather.unit === 'celsius' ? 'C' : 'F'}</p>
                  <p>{weather.condition}</p>
                </div>
              );

            default:
              // 处理其他工具
              return null;
          }
        })}
      </div>
    </div>
  );
}

function UserMessage({ content }: { content: string }) {
  return <div>用户: {content}</div>;
}

function ToolResult({ message }: { message: WeatherMessage }) {
  return <div>工具调用: {message.toolCallId}</div>;
}