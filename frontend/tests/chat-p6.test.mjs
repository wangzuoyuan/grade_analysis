// P6-FE 契约测试：AI 对话助手对接 /api/v1/chat（契约 docs/contracts/p6-ai-mcp.md §0.1/§1/§6）。
// 风格沿用 tests/homework-p5.test.mjs：直接读源码断言关键结构，不启动浏览器。

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiV1 = readFileSync(new URL('../src/lib/api-v1.ts', import.meta.url), 'utf8')
const drawer = readFileSync(new URL('../src/components/ChatDrawer.tsx', import.meta.url), 'utf8')
const toolCard = readFileSync(new URL('../src/components/ToolCallCard.tsx', import.meta.url), 'utf8')

test('api-v1 导出 P6 会话四函数与类型（契约 §1/§0.1 Q03）', () => {
  for (const fn of ['createChatSession', 'getChatSession', 'closeChatSession', 'getChatMessages']) {
    assert.match(apiV1, new RegExp(`export function ${fn}\\(`), `应导出 ${fn}`)
  }
  for (const t of ['ChatSessionCreateRequest', 'ChatScopeInfo', 'ChatSessionResponse', 'ChatCloseResponse', 'ChatMessageItem']) {
    assert.match(apiV1, new RegExp(`export interface ${t}\\b`), `应导出类型 ${t}`)
  }
  // 端点路径抽查（契约 §1 + Q03 服务端历史）
  assert.match(apiV1, /\$\{API_V1_BASE\}\/chat\/sessions/, '会话创建端点')
  assert.match(apiV1, /chat\/sessions\/\$\{encodeURIComponent/, '会话核对/关闭端点')
  assert.match(apiV1, /\/close/, '显式关闭端点')
  assert.match(apiV1, /\/messages`/, '服务端历史端点（GET messages）')
})

test('ChatMessageItem 结构：role 限 user/assistant，含 content/created_at（契约 §0.1 Q03）', () => {
  const msgBlock = apiV1.match(/export interface ChatMessageItem \{[\s\S]*?\n\}/)?.[0] ?? ''
  assert.match(msgBlock, /\bid: number/, '应含消息 id')
  assert.match(msgBlock, /role: 'user' \| 'assistant'/, 'role 限用户/助手两态')
  assert.match(msgBlock, /content: string/, '应含正文')
  assert.match(msgBlock, /created_at: string/, '应含服务端时间戳')
  assert.doesNotMatch(msgBlock, /tool_events|member_person_ids/, '对外投影不含工具事件原文/成员明单')
  // getChatMessages 返回 { messages: ChatMessageItem[] }
  const fnBlock = apiV1.match(/export function getChatMessages\([\s\S]*?\n\}/)?.[0] ?? ''
  assert.match(fnBlock, /Promise<\{ messages: ChatMessageItem\[\] \}>/, '返回 messages 数组包装')
  assert.match(fnBlock, /\/messages`/, '端点指向会话历史')
})

test('ChatScopeInfo 公开投影：含 cohort_size，绝不含成员明单（契约 §0/§1）', () => {
  const scopeBlock = apiV1.match(/export interface ChatScopeInfo \{[\s\S]*?\n\}/)?.[0] ?? ''
  assert.match(scopeBlock, /cohort_size: number/, '必须含 cohort_size 人数摘要')
  assert.doesNotMatch(scopeBlock, /member_person_ids/, '对外投影绝不含 member_person_ids 成员明单')
  for (const field of ['mode', 'data_domain', 'academic_year_id', 'class_ids', 'subject', 'link_id', 'link_version', 'as_of']) {
    assert.match(scopeBlock, new RegExp(`\\b${field}\\b`), `快照投影应含字段 ${field}`)
  }
  // 创建请求只允许 mode + 资源 ID（学年/班/学科），不允许成员清单字段
  const reqBlock = apiV1.match(/export interface ChatSessionCreateRequest \{[\s\S]*?\n\}/)?.[0] ?? ''
  assert.doesNotMatch(reqBlock, /member|person_ids/, '创建请求绝不携带成员清单（服务端重新解析）')
})

test('抽屉按当前工作台 mode 创建会话并展示 scope 摘要（契约 §6）', () => {
  assert.match(drawer, /useWorkspace\(\)/, '抽屉必须读工作台上下文（mode 事实源）')
  assert.match(drawer, /createChatSession\(/, '创建会话必须走 api-v1 封装')
  assert.match(drawer, /buildCreateRequest/, '创建请求须经统一构造（只带 mode + 资源 ID）')
  assert.match(drawer, /academic_year_id/, '须携带学年筛选')
  assert.match(drawer, /class_id/, 'homeroom 须携带行政班筛选')
  assert.match(drawer, /teaching_class_id/, 'teaching 须携带教学班筛选')
  assert.doesNotMatch(drawer, /member_person_ids/, '抽屉绝无成员明单渲染')
  // scope 摘要来自响应 ChatScopeInfo：人数 + 班级数/学科，无名单
  assert.match(drawer, /describeScope/, '须有 scope 摘要函数')
  assert.match(drawer, /cohort_size/, '摘要须含人数')
  assert.match(drawer, /class_ids\.length/, '摘要须含班级数')
  assert.match(drawer, /subject/, '摘要须含学科')
  // 打开抽屉且无会话时自动创建
  assert.match(drawer, /startNewSession/, '须有新建会话入口')
})

test('发消息走 SSE：POST messages + getReader，六种帧分支齐备（契约 §1/§3）', () => {
  assert.match(drawer, /chat\/sessions\/\$\{encodeURIComponent\(String\(currentSession\.session_id\)\)\}\/messages/, '消息端点路径正确')
  assert.match(drawer, /getReader\(\)/, '须用 getReader 流式读取')
  for (const frame of ["'text'", "'tool_call'", "'tool_result'", "'tool_error'", "'error'", "'done'"]) {
    assert.match(drawer, new RegExp(`frame\\.type === ${frame}`), `SSE 须处理 ${frame} 帧`)
  }
  // text 增量渲染 + react-markdown 沿用
  assert.match(drawer, /appendText/, 'text 帧须增量累积')
  assert.match(drawer, /ReactMarkdown/, '助手文本须 markdown 渲染')
  assert.match(drawer, /remarkGfm/, '沿用 gfm 插件')
  // error 帧：模型 Key 未配置给配置引导文案
  assert.match(drawer, /未配置/, '须识别 Key 未配置错误')
  assert.match(drawer, /ANTHROPIC_API_KEY 或 OPENAI_API_KEY/, '须给配置引导（变量名）')
})

test('工具卡消费 tool_call/tool_result/tool_error：loading → 结果/错误回填', () => {
  assert.match(drawer, /import ToolCallCard from '@\/components\/ToolCallCard'/, '须复用 ToolCallCard')
  assert.match(drawer, /call_id: String\(frame\.call_id \?\? ''\)/, 'tool_call 帧须记录 call_id')
  assert.match(drawer, /call\.output = frame\.output/, 'tool_result 帧按 call_id 回填结果')
  assert.match(drawer, /call\.error = typeof frame\.error === 'string' \? frame\.error : '工具执行失败'/, 'tool_error 帧按 call_id 回填错误态')
  // ToolCallCard 自身三态：running（无 output 无 error）/ success / error
  assert.match(toolCard, /running/, '须有运行中态')
  assert.match(toolCard, /getStatus/, '状态由 output/error 推导')
})

test('409 快照漂移（A02）：link_version_conflict → 提示重建会话', () => {
  assert.match(drawer, /link_version_conflict/, '须识别漂移错误码')
  assert.match(drawer, /setScopeConflict\(true\)/, '409 须置冲突态')
  assert.match(drawer, /会话范围已变化（关联撤销\/成员调整）/, '须显示漂移提示文案')
  assert.match(drawer, /旧会话不能继续作答/, '须声明旧上下文不可继续作答')
  // 冲突态只给重建出口，输入框同步禁用
  assert.match(drawer, /scopeConflict && \(\s*<div role="alert"/, '冲突横幅常驻抽屉')
  assert.match(drawer, /scopeConflict \|\| !input\.trim\(\)/, '冲突态须禁用发送')
  assert.match(drawer, /scopeConflict \? '会话已失效，请新建会话'/, '冲突态输入框占位引导重建')
})

test('会话绑定范围关键集（Q01）：mode+学年+班 任一变化即「新会话/继续旧会话」二选一', () => {
  // 关键集三要素（非仅 mode）：mode + academic_year_id + class_id/teaching_class_id
  assert.match(drawer, /interface SessionScopeKey/, '须有范围关键集类型')
  assert.match(drawer, /scopeKeyOfSelection\(/, '当前选择须经关键集构造（与创建请求同构）')
  assert.match(drawer, /sameScopeKey\(/, '须整体比对关键集而非只比 mode')
  assert.match(drawer, /academic_year_id: typeof filter\.academic_year_id === 'number' \? filter\.academic_year_id : null/, '关键集含学年')
  assert.match(drawer, /typeof filter\.class_id === 'number'/, '关键集含行政班')
  assert.match(drawer, /typeof filter\.teaching_class_id === 'number'/, '关键集含教学班')
  assert.match(drawer, /a\.mode === b\.mode && a\.academic_year_id === b\.academic_year_id && a\.class_ref === b\.class_ref/, '三要素逐一比对')
  // 创建时刻缓存关键集，供后续比对
  assert.match(drawer, /scope_key: scopeKeyOfSelection\(m, f\)/, '创建会话时缓存请求范围关键集')
  assert.match(drawer, /scope_key: parseScopeKey\(parsed\.scope_key, scope\)/, '旧存储无关键集时由 scope 反推兜底')
  // 不一致横幅：旧范围明细 + 二选一
  assert.match(drawer, /scopeMismatch/, '须有范围不一致判定态')
  assert.match(drawer, /当前会话属于\{MODE_LABEL\[session\.scope_key\.mode\]\}工作台范围/, '须标注旧会话归属工作台')
  assert.match(drawer, /describeSessionRange\(session\.scope_key\)/, '横幅须含学年/班明细')
  assert.match(drawer, /与当前选择不一致/, '须声明与当前选择不一致')
  assert.match(drawer, /新会话（当前范围）/, '须提供新会话按钮')
  assert.match(drawer, /继续旧会话/, '须提供继续旧会话按钮（仅提醒不阻断）')
  assert.match(drawer, /setScopeMismatch\(false\)/, '继续旧会话只关闭提醒，不阻断')
  assert.match(drawer, /后端会拒绝本次提问并要求新建/, '继续旧会话须说明后端漂移检测兜底')
  // 进入不一致即中止在途流（旧范围流不得继续渲染）
  assert.match(drawer, /if \(mismatched && !scopeMismatchRef\.current\) abortStream\(\)/, '进入范围不一致即中止在途流')
})

test('服务端历史为恢复事实源（Q03）：messages 端点拉取渲染，本地缓存仅离线兜底', () => {
  assert.match(drawer, /getChatMessages\(/, '须调用服务端历史封装')
  assert.match(drawer, /loadServerHistory/, '须有统一的历史拉取入口')
  // 恢复来源是 messages 端点：挂载恢复与打开抽屉都拉取
  assert.match(drawer, /void loadServerHistory\(stored\.session_id\)/, '刷新恢复须拉取服务端历史')
  assert.match(drawer, /if \(cur\) void loadServerHistory\(cur\.session_id\)/, '打开抽屉须以服务端历史刷新')
  // 服务端消息映射为渲染条目（role → user/assistant 气泡）
  assert.match(drawer, /history\.push\(\{ kind: m\.role, content: m\.content \}\)/, '服务端消息映射为渲染条目')
  // 404/409 → 提示重建
  assert.match(drawer, /err\.status === 409\s*\)\s*\{\s*setScopeConflict\(true\)/, '历史端点 409（范围漂移）须给重建入口')
  assert.match(drawer, /会话已不存在或已关闭，请新建会话/, '历史端点 404 须提示重建')
  // 跨会话防护：过期响应不得覆盖已切换的新会话
  assert.match(drawer, /cur\.session_id !== targetSessionId\) return/, '会话已切换须丢弃过期历史响应')
  // 本地缓存仍持久化（离线兜底），但不再作为恢复事实源
  assert.match(drawer, /scope_key: session\.scope_key/, '存储须含范围关键集（供比对）')
})

test('流中 type=error 范围失效帧：本流终止 + 冲突态重建入口（配合后端工具层中止）', () => {
  assert.match(drawer, /typeof frame\.detail === 'string' && frame\.detail/, '范围失效帧从 detail 取错误信息')
  assert.match(drawer, /\/范围\/\.test\(msg\)/, '须识别含"范围"的失效帧')
  assert.match(drawer, /会话范围已变化，本流已终止/, '须提示本流已终止')
  // 置冲突态：输入禁言 + 横幅重建按钮（复用 409 冲突 UI）
  assert.match(drawer, /setScopeConflict\(true\)\s*\n\s*setStreamError\('会话范围已变化，本流已终止'\)/, '范围帧须置冲突态并给重建入口')
})

test('error 帧优先认机器码 code，旧文字匹配保留为无 code 时的兜底', () => {
  // 新后端帧带可选机器码：优先按 code 判定错误类型
  assert.match(drawer, /const code = typeof frame\.code === 'string' \? frame\.code : ''/, 'error 帧须读取机器码字段 frame.code')
  assert.match(drawer, /code === 'scope_drift'/, 'scope_drift 机器码判定')
  assert.match(drawer, /code === 'key_not_configured'/, 'key_not_configured 机器码判定')
  // 旧后端/未重启进程的帧没有 code：文字匹配降级为兜底，兼容不回退
  assert.match(drawer, /\(!code && \/范围\/\.test\(msg\)\)/, '无 code 时保留 /范围/ 文字兜底（旧帧兼容）')
  assert.match(drawer, /\(!code && \/未配置\/\.test\(msg\)\)/, '无 code 时保留 /未配置/ 文字兜底（旧帧兼容）')
  // scope_drift（机器码或兜底命中）路径仍置冲突态 + 重建入口
  assert.match(drawer, /code === 'scope_drift' \|\| \(!code && \/范围\/\.test\(msg\)\)\)[\s\S]{0,200}setScopeConflict\(true\)/, 'scope_drift 路径须置冲突态')
  // 有 code 无 message 的帧回落到按 code 给中文文案
  assert.match(drawer, /'模型 Key 未配置'/, 'key_not_configured 无文案帧回落中文文案')
})

test('中止逻辑：关闭抽屉/切换 mode/重建会话均 abort 在途流', () => {
  assert.match(drawer, /AbortController/, '须用 AbortController 中止')
  assert.match(drawer, /abortStream/, '须有统一中止入口')
  assert.match(drawer, /if \(!next\) abortStream\(\)/, '关闭抽屉即中止')
  assert.match(drawer, /controller\.signal/, 'fetch 须携带 abort signal')
  // 本地缓存兜底：sessionStorage 持久化 + 新建会话清空（恢复事实源是服务端，见上）
  assert.match(drawer, /chat-p6:session/, 'sessionStorage 键固定')
  assert.match(drawer, /sessionStorage\.setItem/, '须持久化会话与消息数组')
  assert.match(drawer, /sessionStorage\.removeItem/, '会话清空即移除存储')
  assert.match(drawer, /loadStoredChat/, '刷新后须恢复旧会话')
  // 恢复后经 GET 核对，404 丢弃本地残影
  assert.match(drawer, /getChatSession\(/, '恢复时须 GET 快照核对')
  assert.match(drawer, /err\.status === 404/, '会话已不存在则丢弃本地状态')
})

test('新会话显式关闭旧会话；创建失败不留半开状态（契约 §1 close 语义）', () => {
  assert.match(drawer, /closeChatSession\(current\.session_id\)/, '新建会话前须显式关闭旧会话')
  assert.match(drawer, /setSession\(null\)/, '创建失败须清空本地会话态')
  assert.match(drawer, /setEntries\(\[\]\)/, '失败/新建均清空消息数组')
})

test('移动端可用、宽度响应式、打印隐藏（契约 §6）', () => {
  assert.match(drawer, /window\.innerWidth < 640/, '小屏恒占满屏宽')
  assert.match(drawer, /clampDrawerWidth/, '桌面端宽度钳制')
  assert.match(drawer, /print:hidden/, '打印时隐藏抽屉')
})
