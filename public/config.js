// API 配置 - 根据部署环境自动切换
// Cloudflare Pages 静态托管 + Vercel API 是最优组合

const API_BASES = {
  // 本地开发
  local: 'http://localhost:8766',
  // Vercel 部署
  vercel: 'https://mom-index-xxxx.vercel.app',
  // Cloudflare Pages 下的 Workers（未来扩展）
  cloudflare: ''
};

// 自动检测当前环境
function detectEnv() {
  const host = window.location.hostname;
  if (host === 'localhost' || host === '127.0.0.1') return 'local';
  if (host.includes('vercel.app')) return 'vercel';
  if (host.includes('pages.dev')) return 'vercel'; // Cloudflare Pages 也用 Vercel API
  return 'vercel'; // 默认
}

// 导出 API 基地址
window.API_BASE = API_BASES[detectEnv()] || API_BASES.local;
