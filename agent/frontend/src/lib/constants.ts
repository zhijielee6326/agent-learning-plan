export const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api'

export const DEFAULT_EDITOR_CONTENT =
  '欢迎使用保险产品精算智能体\n\n' +
  '我是基于大语言模型（GLM-5.1）的保险条款分析专家，可以帮您：\n\n' +
  '- **智能检索**：从备案产品库中检索匹配的保险条款\n' +
  '- **专业解读**：用通俗易懂的语言解释条款含义\n' +
  '- **产品对比**：对比不同产品的条款差异\n' +
  '- **条款组合**：支持多产品条款组合分析\n' +
  '- **创新生成**：基于现有产品库智能生成创新条款\n\n' +
  '请在左侧输入您的需求，开始对话吧！'

export const STORAGE_KEY = 'insurance_agent_sessions'

export const QUICK_QUERIES = [
  { icon: '📝', text: '帮我生成一份人身意外伤害保险的产品条款' },
  { icon: '💡', text: '帮我设计一份创新性的"宠物医疗保险"产品条款' },
  { icon: '📊', text: '基于现有产品库，分析哪些保险产品类型有市场前景' },
  { icon: '🔀', text: '帮我生成一份结合意外伤害和医疗保障的复合保险产品' },
  { icon: '📖', text: '解释一下保险条款中的"等待期"和"免赔额"有什么区别' },
  { icon: '🔍', text: '对比分析产品库中团体意外伤害保险的保障差异' },
]
