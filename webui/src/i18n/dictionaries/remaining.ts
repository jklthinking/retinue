/** Additional shared system messages. User-authored content is never translated. */
const remainingTranslations: Record<string, string> = {
  "{greeting}，{name}": "{greeting}, {name}",
  "会话已过期，请重新登录后重试。": "Your session expired. Sign in again and retry.",
  "无法连接服务器，请稍后重试。": "Unable to connect to the server. Please retry later.",
  "无法加载演示数据索引": "Unable to load the demo data index",
  "公开演示为只读，无法修改数据。": "The public demo is read-only; data cannot be changed.",
  "演示数据未收录: {key}": "Demo snapshot unavailable: {key}",
  "分页响应缺少 next_cursor": "Paginated response is missing next_cursor",
};
export default remainingTranslations;
