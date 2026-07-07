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
