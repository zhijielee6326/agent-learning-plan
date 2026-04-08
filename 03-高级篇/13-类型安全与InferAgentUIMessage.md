# 知识点 13: 类型安全与 InferAgentUIMessage

**难度**: ⭐⭐⭐ | **预计学时**: 4h | **分类**: 高级特性

## 📚 学习目标

完成本知识点学习后，你将能够：

- 理解类型安全在 Agent 开发中的重要性
- 使用 `InferAgentUIMessage` 实现端到端类型安全
- 正确定义 Agent 的工具和输出类型
- 构建类型安全的 AI 应用

## 🎯 核心概念

### 为什么需要类型安全?

```
没有类型安全:
const toolResult = toolCall.result;
console.log(toolResult.name); // ❌ 运行时错误: undefined

有类型安全:
const toolResult = toolCall.result;
console.log(toolResult.name); // ✅ TypeScript 提示错误
// 类型: { temperature: number; condition: string }
```

### 类型安全的价值

```
┌─────────────────────────────────────────────────────────┐
│                    类型安全的好处                        │
├─────────────────────────────────────────────────────────┤
│  1. 开发时发现错误 (而不是运行时)                        │
│  2. IDE 自动补全和提示                                   │
│  3. 重构更安全                                           │
│  4. 代码文档化 (类型即文档)                              │
│  5. 减少运行时检查                                       │
└─────────────────────────────────────────────────────────┘
```

## 💻 实战演练

### 基础示例: 类型安全的工具定义

```typescript
import { tool } from 'ai';
import { z } from 'zod';

// ✅ 好的做法: Zod schema 自动推断类型
const getWeather = tool({
  description: '获取天气信息',
  parameters: z.object({
    city: z.string(),
    unit: z.enum(['celsius', 'fahrenheit']).default('celsius'),
  }),
  execute: async ({ city, unit }) => {
    // city 和 unit 的类型自动推断!
    return {
      city,
      temperature: 25,
      condition: '晴天',
      unit,
    };
  },
});

// ❌ 不好的做法: 使用 any
const badTool = {
  execute: async (args: any) => {
    // args 的类型未知，容易出错
  },
};
```

### 示例 1: 定义 Agent 并推断类型

```typescript
// src/lib/agents/weather-agent.ts
import { createAgent } from '@mastra/core';
import { openai } from '@ai-sdk/openai';
import { tool } from 'ai';
import { z } from 'zod';

// 定义工具 (严格的 Zod schema)
const getWeather = tool({
  description: '获取指定城市的天气',
  parameters: z.object({
    city: z.string().describe('城市名称'),
    unit: z.enum(['celsius', 'fahrenheit']),
  }),
  execute: async ({ city, unit }) => {
    return {
      city,
      temperature: 25,
      unit,
      condition: '晴天',
    };
  },
});

// 创建 Agent
export const weatherAgent = createAgent({
  name: 'weather-agent',
  model: openai('gpt-4o'),
  tools: {
    getWeather,
  },
});

// ✅ 导出推断的类型
export type WeatherAgent = typeof weatherAgent;
```

### 示例 2: 使用 InferAgentUIMessage

```typescript
// src/app/api/chat/route.ts
import { weatherAgent, type WeatherAgent } from '@/lib/agents/weather-agent';
import { streamText } from 'ai';
import { InferAgentUIMessage } from '@ai-sdk/react';

// ✅ 推断 UI 消息类型
export type WeatherMessage = InferAgentUIMessage<WeatherAgent>;

export async function POST(req: Request) {
  const { messages } = await req.json();

  // ✅ messages 现在有完整的类型!
  // messages: WeatherMessage[]

  const result = streamText({
    model: weatherAgent.model,
    tools: weatherAgent.tools,
    messages,
  });

  return result.toDataStreamResponse();
}
```

### 示例 3: 客户端类型安全

```typescript
// src/components/chat.tsx
'use client';

import { useChat } from '@ai-sdk/react';
import type { WeatherMessage } from '@/app/api/chat/route';

export default function ChatComponent() {
  // ✅ 完全类型化的聊天!
  const { messages, input, handleInputChange, handleSubmit } = useChat<
    WeatherMessage // 传入类型参数
  >({
    api: '/api/chat',
  });

  return (
    <div>
      {messages.map((message) => {
        // ✅ message 有完整的类型!
        switch (message.role) {
          case 'user':
            return <div>用户: {message.content}</div>;

          case 'assistant':
            // ✅ toolResults 有类型!
            if (message.toolResults) {
              message.toolResults.forEach((result) => {
                // result.toolName 的类型是 'getWeather' | ...
                if (result.toolName === 'getWeather') {
                  // ✅ result.result 有正确的类型!
                  console.log(result.result.city); // string
                  console.log(result.result.temperature); // number
                }
              });
            }
            return <div>助手: {message.content}</div>;

          case 'tool':
            // message.toolCallId 有类型
            return <div>工具调用: {message.toolCallId}</div>;
        }
      })}

      <form onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={handleInputChange}
          placeholder="询问天气..."
        />
        <button type="submit">发送</button>
      </form>
    </div>
  );
}
```

### 示例 4: 自定义消息渲染组件

```typescript
// src/components/message-renderer.tsx
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
      // TypeScript 会确保处理所有情况
      const exhaustive: never = message;
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
```

### 示例 5: 复杂工具的类型推断

```typescript
// src/lib/tools/comprehensive.ts
import { tool } from 'ai';
import { z } from 'zod';

// 复杂的嵌套 schema
const databaseQuery = tool({
  description: '查询数据库',
  parameters: z.object({
    table: z.enum(['users', 'products', 'orders']),
    filters: z.object({
      id: z.string().optional(),
      name: z.string().optional(),
      status: z.enum(['active', 'inactive', 'deleted']).optional(),
    }),
    pagination: z.object({
      page: z.number().min(1),
      limit: z.number().min(1).max(100),
    }),
  }),
  execute: async ({ table, filters, pagination }) => {
    // ✅ 所有参数都有正确的类型
    const query = buildQuery({ table, filters, pagination });
    const results = await db.query(query);

    return {
      table,
      results,
      page: pagination.page,
      limit: pagination.limit,
      total: results.length,
    };
  },
});

// ✅ 返回类型被正确推断
type DatabaseQueryResult = ReturnType<typeof databaseQuery.execute>;
// Promise<{
//   table: 'users' | 'products' | 'orders'
//   results: any[]
//   page: number
//   limit: number
//   total: number
// }>
```

## 🔑 关键类型

### InferAgentUIMessage

```typescript
import { InferAgentUIMessage } from '@ai-sdk/react';
import { createAgent } from '@mastra/core';

const agent = createAgent({
  name: 'my-agent',
  model: openai('gpt-4o'),
  tools: { getWeather, searchWeb },
});

// ✅ 推断完整的消息类型
type Message = InferAgentUIMessage<typeof agent>;

// Message 的结构:
// {
//   role: 'user' | 'assistant' | 'tool' | 'system'
//   content: string
//   toolCalls?: Array<{
//     toolName: 'getWeather' | 'searchWeb'
//     toolCallId: string
//     args: {...}
//   }>
//   toolResults?: Array<{
//     toolName: 'getWeather' | 'searchWeb'
//     toolCallId: string
//     result: {...}
//   }>
// }
```

### 工具结果类型

```typescript
// 从 tool 定义推断结果类型
type GetWeatherResult = Awaited<ReturnType<typeof getWeather.execute>>;
// { city: string, temperature: number, unit: 'celsius' | 'fahrenheit', condition: string }
```

## 🎨 最佳实践

### DO ✅

```typescript
// ✅ 导出 Agent 类型供其他文件使用
export const agent = createAgent({...});
export type Agent = typeof agent;

// ✅ 在组件中使用推断的类型
useChat<InferAgentUIMessage<typeof agent>>({
  api: '/api/chat',
});

// ✅ 使用字面量类型而非字符串
parameters: z.object({
  status: z.enum(['active', 'inactive']), // ✅
  status: z.string(), // ❌ 不够精确
});
```

### DON'T ❌

```typescript
// ❌ 使用 any 失去类型安全
const handleToolResult = (result: any) => {...}

// ✅ 使用泛型或联合类型
const handleToolResult = <T extends ToolName>(result: ToolResult<T>) => {...}

// ❌ 重复定义类型
interface GetWeatherResult {
  city: string;
  temperature: number;
}

// ✅ 从 Zod schema 推断
type GetWeatherResult = z.infer<typeof getWeather.parameters>;
```

## 🐛 常见问题

### Q1: 类型推断失败

**原因**: 循环依赖或复杂的工具定义

**解决**:

```typescript
// ✅ 分别定义和导出
const tools = { getWeather, searchWeb };
export type Tools = typeof tools;

const agent = createAgent({
  tools,
});

export type AgentType = typeof agent;
```

### Q2: toolResults 类型为 any

**原因**: 没有使用 `InferAgentUIMessage`

**解决**:

```typescript
// ❌ 错误
useChat({
  api: '/api/chat',
});
// messages 的 toolResults 是 any

// ✅ 正确
useChat<InferAgentUIMessage<typeof agent>>({
  api: '/api/chat',
});
// toolResults 有正确的类型
```

### Q3: 工具参数类型不匹配

**原因**: Zod schema 和 execute 参数不一致

**解决**:

```typescript
// ✅ 确保 execute 的参数与 schema 匹配
parameters: z.object({
  city: z.string(),
  unit: z.enum(['celsius', 'fahrenheit']),
}),
execute: async ({ city, unit }) => {  // ✅ 类型自动匹配
  // ...
},
```

## 📝 练习任务

### 基础练习

1. 定义一个带类型安全的工具
2. 使用 `InferAgentUIMessage` 推断消息类型
3. 在组件中使用类型化的消息

### 进阶练习

1. 创建多个工具并正确推断联合类型
2. 实现类型安全的工具结果渲染
3. 添加自定义的类型验证

### 挑战任务

1. 创建一个完整的类型安全聊天应用
2. 实现类型错误边界处理
3. 编写类型测试确保类型正确性

## 🔗 延伸阅读

- [TypeScript 高级类型](https://www.typescriptlang.org/docs/handbook/2/types-from-types.html)
- [Zod 类型推断](https://zod.dev/?id=inferring-schema-types)
- [AI SDK 类型安全文档](https://ai-sdk.dev/docs/typescript)

## ✅ 检查清单

- [ ] 理解 `InferAgentUIMessage` 的作用
- [ ] 能定义类型安全的工具
- [ ] 能在组件中使用类型化的消息
- [ ] 知道如何导出和复用类型
- [ ] 理解 Zod schema 的类型推断

## 🎤 分享建议

### 演示重点

- 对比有/无类型安全的开发体验
- 展示 IDE 的类型提示和补全
- 演示类型检查如何提前发现错误

### 互动环节

- 一起编写类型安全的工具
- 测试类型推断的效果
- 讨论类型安全的最佳实践
