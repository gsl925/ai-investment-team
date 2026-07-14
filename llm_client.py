"""
Provider-agnostic LLM wrapper.

settings.json controls whether LLM is active and which provider to use:
  { "llm": { "enabled": true, "provider": "anthropic" | "openai", ... } }

When enabled=false (default) all public functions return immediately without
making any network calls or requiring any LLM package to be installed.
"""
import json
import pathlib
from typing import Any

SETTINGS_PATH = pathlib.Path(__file__).parent / "settings.json"

_AGENT_KEYS = [
    "data_retrieval",
    "technical",
    "macro_news",
    "flow",
    "risk",
    "chief_strategist",
]


def load_settings() -> dict[str, Any]:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def get_llm_config() -> dict[str, Any]:
    return load_settings().get("llm", {})


def llm_enabled() -> bool:
    cfg = get_llm_config()
    if not cfg.get("enabled"):
        return False
    provider = cfg.get("provider", "anthropic")
    return bool(cfg.get(provider, {}).get("api_key"))


def _call_llm(prompt: str, system: str) -> str:
    cfg = get_llm_config()
    provider = cfg.get("provider", "anthropic")
    max_tokens = int(cfg.get("max_tokens", 1500))
    temperature = float(cfg.get("temperature", 0))

    if provider == "anthropic":
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise RuntimeError("執行 pip install anthropic 後才能使用 Anthropic LLM") from exc
        api_key = cfg.get("anthropic", {}).get("api_key", "")
        model = cfg.get("anthropic", {}).get("model", "claude-haiku-4-5-20251001")
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    if provider == "openai":
        try:
            import openai  # type: ignore
        except ImportError as exc:
            raise RuntimeError("執行 pip install openai 後才能使用 OpenAI LLM") from exc
        api_key = cfg.get("openai", {}).get("api_key", "")
        model = cfg.get("openai", {}).get("model", "gpt-4o-mini")
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content

    raise ValueError(f"不支援的 LLM provider：{provider}")


def _build_context(item: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    rec = item.get("recommendation", {})
    metrics = rec.get("metrics", {})
    factors = rec.get("factors", {})
    news = item.get("news", [])[:5]
    alerts = item.get("monitoring", {}).get("alerts", [])
    fund = item.get("fundamentals") or {}
    signals = [s for s in rec.get("signals", [])[:6]]

    lines = [
        f"標的：{item.get('symbol')} {item.get('name', '')}",
        f"資產類別：{item.get('asset_type')}  產業：{item.get('industry') or '未取得'}",
        f"現價：{item.get('price')}  1日：{item.get('change_1d', 0):.2f}%  1月：{metrics.get('change_1m', 0):.2f}%  3月：{metrics.get('change_3m', 0):.2f}%",
        f"投研分數：{rec.get('score')}  系統動作：{rec.get('action')}  評級：{rec.get('grade')}",
        "",
        "【技術指標】",
        f"  ADX：{metrics.get('adx_14', '—')}  RSI14：{metrics.get('rsi_14', '—')}  MACD slope：{metrics.get('macd_hist_slope', '—')}",
        f"  RS-3M 百分位：{metrics.get('rs_3m_percentile', '—')}  波動率：{metrics.get('volatility', '—')}  Hurst60：{metrics.get('hurst_60', '—')}",
        f"  MA20：{metrics.get('ma_20', '—')}  MA60：{metrics.get('ma_60', '—')}",
    ]

    if fund:
        lines += [
            "",
            "【基本面（Aegis）】",
            f"  EPS TTM：{fund.get('eps_ttm', '—')}  EPS QoQ：{fund.get('eps_qoq', '—')}",
            f"  Revenue YoY：{fund.get('revenue_yoy', '—')}  GM：{fund.get('gross_margin', '—')}  OM：{fund.get('operating_margin', '—')}",
        ]

    if factors:
        lines += ["", "【因子分數】"]
        for k, v in factors.items():
            if isinstance(v, dict):
                lines.append(f"  {k}：{v.get('score', '—')}（信心：{v.get('confidence', '—')}）")

    if signals:
        lines += ["", "【主要信號】"]
        for s in signals:
            lines.append(f"  [{s.get('factor', '')}] {s.get('label', '')}：{s.get('detail', '')}")

    if alerts:
        lines += ["", "【警示】"]
        for a in alerts[:3]:
            lines.append(f"  [{a.get('severity', '')}] {a.get('title', '')}：{a.get('detail', '')}")

    if news:
        lines += ["", "【新聞（最近 5 則）】"]
        for n in news:
            lines.append(f"  [{n.get('sentiment', '')}] {n.get('source', '')}：{n.get('title', '')}")

    lines += ["", "【規則式 Agent 結論摘要】"]
    agent_label_map = {
        "Data Retrieval Agent": "data_retrieval",
        "Technical Analyst Agent": "technical",
        "Macro & News Analyst Agent": "macro_news",
        "Flow / On-chain Agent": "flow",
        "Risk Officer Agent": "risk",
        "Chief Strategist Agent": "chief_strategist",
    }
    for r in reports:
        key = agent_label_map.get(r.get("agent", ""), r.get("agent", ""))
        lines.append(f"  {key}：{r.get('verdict', '')}")

    return "\n".join(lines)


def generate_research_insights(
    item: dict[str, Any], reports: list[dict[str, Any]]
) -> dict[str, str]:
    """
    Call LLM once per symbol and return a dict keyed by agent name with LLM insights.
    Returns empty dict if LLM is disabled or on any error.
    """
    if not llm_enabled():
        return {}

    system = (
        "你是一位專業的量化投資研究助理，擅長分析台灣股市與國際市場。"
        "根據提供的數據給出精確、有根據的投研觀點，言簡意賅，不臆測缺乏數據支撐的結論。"
        "用繁體中文回覆。"
    )

    context = _build_context(item, reports)

    prompt = f"""以下是 {item.get('symbol')} {item.get('name', '')} 的完整分析資料：

{context}

請針對以下六個 Agent，各提供 2-3 句精煉的 LLM 投研觀點（不要重複規則式結論，要補充推理或風險角度）：

1. data_retrieval：資料完整性與關鍵缺口的影響
2. technical：技術結構的主要訊號與風險
3. macro_news：新聞事件與總經背景的判讀
4. flow：從量價結構推測的資金行為
5. risk：最需要優先關注的風險點
6. chief_strategist：綜合所有角度的最終建議與觸發條件

請只回傳 JSON，不要其他文字：
{{"data_retrieval": "...", "technical": "...", "macro_news": "...", "flow": "...", "risk": "...", "chief_strategist": "..."}}"""

    raw = _call_llm(prompt, system)

    # parse JSON from response (handle markdown code fences)
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # fallback: try to find JSON object in response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end])
            except json.JSONDecodeError:
                return {}
        else:
            return {}

    return {k: str(parsed.get(k, "")) for k in _AGENT_KEYS if parsed.get(k)}


def generate_industry_insight(
    l1_data: dict[str, Any],
    news: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Level 2 industry comparison: LLM narrative on WHY the stock stands out vs peers
    and whether recent news corroborates the quantitative signals.
    Returns {"llm_insight": str, "news_used": int} or {"llm_error": str}.
    Always returns {} if LLM is disabled.
    """
    if not llm_enabled():
        return {}

    symbol = l1_data.get("symbol", "")
    name = l1_data.get("name", "")
    industry = l1_data.get("industry_group", "")
    data_count = l1_data.get("data_count", 0)
    target = l1_data.get("target", {})
    percentiles = l1_data.get("percentiles", {})
    medians = l1_data.get("medians", {})
    outlier_flags = l1_data.get("outlier_flags", [])

    def fmt_pct(v: Any) -> str:
        if v is None:
            return "N/A"
        return f"{float(v):.1%}"

    def fmt_signed_pct(v: Any) -> str:
        if v is None:
            return "N/A"
        return f"{float(v):+.1%}"

    def pct_desc(pct: Any) -> str:
        if pct is None:
            return "資料不足"
        p = int(pct)
        if p >= 75:
            return f"前 {100 - p}%（同業前段）"
        if p <= 25:
            return f"後 {p}%（同業末段）"
        return f"中段（第 {p} 百分位）"

    metric_lines = [
        f"  毛利率：{fmt_pct(target.get('gross_margin'))}  {pct_desc(percentiles.get('gross_margin'))}  同業中位 {fmt_pct(medians.get('gross_margin'))}",
        f"  營業利益率：{fmt_pct(target.get('operating_margin'))}  {pct_desc(percentiles.get('operating_margin'))}  同業中位 {fmt_pct(medians.get('operating_margin'))}",
        f"  EPS QoQ：{fmt_signed_pct(target.get('eps_qoq'))}  {pct_desc(percentiles.get('eps_qoq'))}  同業中位 {fmt_signed_pct(medians.get('eps_qoq'))}",
        f"  營收 YoY：{fmt_signed_pct(target.get('revenue_yoy'))}  {pct_desc(percentiles.get('revenue_yoy'))}  同業中位 {fmt_signed_pct(medians.get('revenue_yoy'))}",
        f"  EPS TTM：{target.get('eps_ttm') if target.get('eps_ttm') is not None else 'N/A'}  {pct_desc(percentiles.get('eps_ttm'))}  同業中位 {medians.get('eps_ttm') if medians.get('eps_ttm') is not None else 'N/A'}",
    ]

    outlier_lines = [f"  - {f['insight']}" for f in outlier_flags] if outlier_flags else ["  - 無顯著偏離同業中位的指標"]

    news_lines: list[str] = []
    for n in news[:8]:
        sentiment = n.get("sentiment", "neutral")
        source = n.get("source", "")
        title = n.get("title", "")
        flag = "▲" if sentiment == "positive" else ("▼" if sentiment == "negative" else "─")
        news_lines.append(f"  {flag} [{source}] {title}")

    prompt = f"""以下是台股 {symbol} {name} 在 {industry} 產業中的同業財務比較（共 {data_count} 家同業有資料）：

【財務指標同業位階】
{chr(10).join(metric_lines)}

【顯著特徵（偏離同業中位 ≥ P80 或 ≤ P20）】
{chr(10).join(outlier_lines)}

【近期新聞標題】
{chr(10).join(news_lines) if news_lines else "  （無可取得新聞）"}

請以 4-5 句繁體中文回答：
1. 這家公司在同業中財務位階的可能結構性原因（產品定位、成本結構、客戶集中度等推理，非重複數字）
2. 上述新聞是否與財務特徵吻合？有無佐證或矛盾之處？
3. {industry} 產業目前可能面臨的主要順風或逆風
4. 基於同業比較，本股最值得關注的一個機會或風險點

回覆純文字，不要 JSON 或標題，直接輸出分析段落。"""

    system = (
        "你是專業的台股產業分析師，擅長從同業比較中辨識競爭優勢與結構性風險。"
        "根據財務數據與新聞，提供有邏輯根據的觀點，不做無依據的預測。"
        "用繁體中文回覆，言簡意賅。"
    )

    try:
        raw = _call_llm(prompt, system)
        return {"llm_insight": raw.strip(), "news_used": len(news_lines)}
    except Exception as exc:
        return {"llm_error": str(exc)}
