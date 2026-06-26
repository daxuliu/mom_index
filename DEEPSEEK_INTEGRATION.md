# DeepSeek LLM 集成指南

## 是什么

`analyzer/llm_analyzer.py` 已经从"纯关键词匹配"升级为**双模式**：

- **LLM 模式**（推荐）：调 DeepSeek-V4-Flash 做深度判定
- **关键词模式**（降级）：无 key 时自动降级，旧逻辑保留

新文件 `analyzer/interpret_generator.py` 提供 `/api/interpret` 端点，用 LLM 生成"今日市场情绪解读"。

## 5 分钟接入

### 1. 申请 key
访问 https://platform.deepseek.com/api_keys，新用户送 500 万 token（30 天）。

### 2. 填环境变量

**本地**（项目根目录）：
```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY=sk-xxx
```

**生产**（Vercel Dashboard）：
- Settings → Environment Variables
- Name: `DEEPSEEK_API_KEY`
- Value: `sk-xxx`
- 三个环境都勾上

### 3. 装依赖
```bash
pip install -r requirements.txt   # 新增 openai>=1.0.0
```

### 4. 跑测试
```bash
python test_llm_analyzer.py
# 不依赖真实 key 也能跑（自动走关键词模式）
# 输出 8/8 通过
```

### 5. 验证 LLM 模式
```bash
export DEEPSEEK_API_KEY=sk-xxx
python -c "
from analyzer.llm_analyzer import analyze_post, llm_available
print('LLM enabled:', llm_available())
r = analyze_post({
  'title': '小白第一次买 ETF，求大佬们看看',
  'content': '刚入股市什么都不懂，听同事说涨了就跟买了 1000 块',
  'platform': 'guba'
}, 'nasdaq')
print(f'分数: {r.newbie_score}, 等级: {r.level}, 模式: {r.analysis_mode}')
"
```

## 关键设计

### 双模式自动路由
```python
# analyzer/llm_analyzer.py:541
if use_llm and llm_available():
    try:
        # 调 DeepSeek
    except Exception as e:
        # 降级到关键词
        return _keyword_analyze(post, sector)
```

- ✅ 有 key + SDK → LLM 模式
- ✅ 无 key → 关键词模式
- ✅ 有 key 但 LLM 调用失败 → 降级 + 标记 `analysis_mode="keyword_fallback"`

### OpenAI 兼容
DeepSeek API 完全兼容 OpenAI SDK：
```python
from openai import OpenAI
client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com/v1"  # 唯一区别
)
```

### 强制 JSON 输出
用 `response_format={"type": "json_object"}` 强制 LLM 返回合法 JSON，免去解析出错。

### 分析结果 schema
LLM 输出（_call_deepseek）：
```json
{
  "is_newbie": true,
  "score": 78.5,
  "confidence": "high",
  "level": "纯小白",
  "signals": [
    {"name": "身份自述", "weight": 8, "evidence": "自称'小白'"},
    {"name": "决策依赖", "weight": 7, "evidence": "求大佬们看看"}
  ],
  "reasoning": "明显的小白身份自述加求建议...",
  "sentiment": 0.6,
  "intent": "buy",
  "intent_strength": 0.4
}
```

转成 [AnalysisResult](file:///Users/xuliu/Downloads/mom-index-master/analyzer/llm_analyzer.py#L115-L145) dataclass 后，下游 `pipeline.py` / `api_server.py` / `api/index.py` **完全无感**——只是分数更准了。

## 费用估算

每次 LLM 调用：
- 输入：~500 tokens（system prompt + post）
- 输出：~150 tokens
- 单次成本：~$0.00009（V4-Flash $0.14/1M input + $0.28/1M output）
- **11 板块全量**：~1 分钱（人民币）
- **每天 10 次全量刷新**：~1 毛/天
- **500 万 token 免费额度**：够用 **数月**

## 调试

### 看当前是否走 LLM
```python
from analyzer.llm_analyzer import llm_available
print("LLM 模式:", llm_available())
```

### 强制走关键词
```python
r = analyze_post(post, sector, use_llm=False)
```

### 看 LLM 调用的延迟
```python
r = analyze_post(post, sector, use_llm=True)
print(f"耗时: {r.llm_latency_ms}ms, 模式: {r.analysis_mode}")
```

## 切换模型

```bash
# 默认走 deepseek-chat alias（→ V4-Flash）
# 2026-07-24 后会弃用，建议显式设：
export DEEPSEEK_MODEL=deepseek-v4-flash      # 性价比第一
# export DEEPSEEK_MODEL=deepseek-v4-pro      # 旗舰，强推理
# export DEEPSEEK_MODEL=deepseek-reasoner    # 思考型（也即将弃用）
```

Vercel Dashboard → Environment Variables → 加 `DEEPSEEK_MODEL=deepseek-v4-flash`。

## 端点

### `GET /api/query?code=XXX`
- 老接口，行为不变
- 走 `/api/query?code=513310` → 现在会触发 LLM 分析每条股吧帖子
- 单次响应 2-5 秒（取决于 LLM 延迟）

### `GET /api/interpret[?date=YYYY-MM-DD]`
- 新接口
- 无参：用 `dashboard.latest`（最新数据）
- 有参：找对应日期的 record
- 返回：
  ```json
  {
    "date": "2026-06-25",
    "interpret": "今日纳斯达克热度第一...",
    "mode": "llm",  // 或 "fallback"
    "latency_ms": 1234,
    "tokens": 856
  }
  ```

## 已知限制

1. **Vercel Function 10s 超时**：`/api/query` 分析 30 条帖子可能超时
   - 临时方案：`analyze_post` 默认只分析 30 条（api_server.py 已有）
   - 彻底方案：批量异步 + 缓存
2. **Token 用量监控**：建议给 Vercel Functions 加监控 alert
3. **prompt injection 风险**：用户帖子可能试图覆盖 system prompt——目前依赖 DeepSeek 的 alignment

## 后续优化方向

- [ ] 加 `/api/qa` AI 问答端点（用户问"现在该不该买纳指"）
- [ ] LLM 结果缓存（同标题帖子 1 小时内复用）
- [ ] prompt 迭代：把判定案例放进 few-shot examples
- [ ] 多模型对比：同时调 DeepSeek + Qwen，看哪个判定更准
