import { anthropic } from '@ai-sdk/anthropic';

// 创建 Agent 配置
export const weatherAgent = {
  name: 'weather-agent',
  model: anthropic('claude-3-5-sonnet-20241022'),
  system: '你是一个天气助手，可以调用工具获取天气信息。',
};

// 导出类型
export type WeatherAgent = typeof weatherAgent;