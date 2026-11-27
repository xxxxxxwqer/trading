# AI Trading Arena 일일 리포트 — Day 2/30 (2026-11-27)

## 1. 오늘의 핵심 요약 (Executive Summary)
- **시장 레짐**: 미국 증시는 `RISK-ON / BULLISH MOMENTUM` 기조를 유지하였으나, 당일 세션은 **Early Close (09:30-13:00 ET)** 조기 마감으로 진행되어 총 7개 사이클(30분봉 기준)만 실행되었습니다.
- **벤치마크 성과**: SPY 종가는 `726.88`(`+1.43% 1D`, `+4.57% 5D`), 누적 수익률 `+3.54%`(`equity: $10,354.13`)를 기록하며 벤치마크 1위를 수성했습니다.
- **에이전트 현황**: 총 34개 에이전트 모두 `ACTIVE` 상태를 유지하였으며, 서킷 브레이커 발동(`DAILY_HALTED` 또는 `BLOWN_UP`) 에이전트는 0개입니다. 벤치마크(SPY) 수익률을 상회한 에이전트는 0개이며, 랜덤 대조군(`ctrl_random`)을 상회한 에이전트는 13개입니다.
- **주요 특징**: 공시 스타일 학습 그룹(`11. Berkshire Style`, `12. Congress Style`)이 각각 누적 수익률 `+1.91%`, `+1.75%`로 상위권을 형성하며 강세를 보였습니다. 반면, 14번 `claude_rmp`는 레짐 필터 조건 불만족으로 전 사이클 `SKIP_REGIME_OFF`를 기록하며 현금 대기를 유지했습니다.

---

## 2. 시장 매크로 및 기술적 레짐 브리핑

### 2.1 주요 자산별 가격 및 기술적 지표
- **SPY (Broad Market)**: 종가 `726.88` (`+1.43% 1D`, `+4.57% 5D`). ORB Low 이탈(`-0.16%`), SMA 20 대비 `+1.22%`, SMA 50 대비 `-4.10%`, RSI(14) `67.6`, ATR(14) `3.33`, 볼린저 밴드 내부, 거래량 비율 `1.41`, 52주 고점 대비 `-11.75%`.
- **QQQ (Technology)**: 종가 `646.98` (`+2.06% 1D`, `+4.23% 5D`). ORB 범위 내, SMA 20 대비 `+2.62%`, SMA 50 대비 `-8.50%`, RSI(14) `77.9`, ATR(14) `5.44`, 볼린저 밴드 내부, 거래량 비율 `0.66`, 52주 고점 대비 `-20.24%`.
- **TLT (Fixed Income ETF)**: 종가 `81.31` (`-1.03% 1D`, `+0.02% 5D`). ORB Low 이탈(`-0.61%`), SMA 20 대비 `-1.01%`, SMA 50 대비 `+1.47%`, RSI(14) `10.6`(극단적 과매도), ATR(14) `0.16`, 볼린저 밴드 내부, 거래량 비율 `0.77`.
- **GLD (Commodity ETF)**: 종가 `333.26` (`+1.77% 1D`, `+2.44% 5D`). ORB Low 이탈(`-0.19%`), SMA 20 대비 `+2.36%`, SMA 50 대비 `-10.18%`, RSI(14) `83.1`, ATR(14) `2.10`, 볼린저 밴드 내부, 거래량 비율 `0.84`.

### 2.2 시장 내부 지표 (Market Internals)
- **Market Breadth**: 50일 이내 이동평균 상단 위치 종목 비율(`breadth_above_sma50_pct`)은 `49.1%`로 중립 수준을 기록했습니다.
- **변동성 비율**: RV10/RV60 비율은 `1.67`로 단기 변동성이 확대되는 국면입니다.
- **Relative Strength 상위 5**: PLTR (`+7.56`), TSLA (`+6.66`), NVDA (`+5.84`), QCOM (`+4.45`), META (`+4.35`).
- **Relative Strength 하위 5**: MSFT (`-3.04`), MS (`-3.46`), AMD (`-3.64`), TMO (`-4.27`), GS (`-5.10`).
- **RSI 과매수 종목**: AMZN (`75.8`), META (`72.6`), TSLA (`84.1`), AVGO (`85.8`), NFLX (`88.8`).
- **RSI 과매도 종목**: 없음 (`N/A`).
- **52주 신고가 근접 종목**: COST (`-1.92%`), PG (`-2.43%`), KO (`-3.55%`), DIS (`-4.37%`), BRK.B (`-4.51%`).

---

## 3. 전체 에이전트 성과 리더보드 (34개 에이전트)

| Rank | Agent ID | Name | Group | Equity ($) | Cash ($) | Daily PnL ($) | Daily PnL (%) | Cum Return (%) | vs SPY (pp) | MDD (%) | Fills | Status |
|:---:|:---|:---|:---|---:|---:|---:|---:|---:|---:|---:|---:|:---|
| 1 | `ctrl_spy` | 9. Buy-and-Hold SPY (Benchmark) | SPY / 랜덤 / 대조군 (3) | 10,354.13 | 193.17 | 169.94 | +1.67% | +3.54% | -0.00 | -0.79% | 1 | ACTIVE |
| 2 | `megacap5_hold` | 25. Mega-Cap Top-5 Buy & Hold (Control) | SPY / 랜덤 / 대조군 (3) | 10,347.68 | 493.42 | 127.04 | +1.24% | +3.48% | -0.06 | -1.99% | 5 | ACTIVE |
| 3 | `value_momentum` | 28. Value + 63D Momentum Combo | 코딩 기반 (25) | 10,192.60 | 493.41 | 98.77 | +0.98% | +1.93% | -1.61 | -0.28% | 5 | ACTIVE |
| 4 | `style_berkshire` | 11. Berkshire Style (13F Pattern) | 공시 스타일 학습 (2) | 10,190.90 | 0.01 | 166.88 | +1.66% | +1.91% | -1.63 | -0.40% | 5 | ACTIVE |
| 5 | `style_congress` | 12. Congress Style (House PTR Pattern) | 공시 스타일 학습 (2) | 10,175.05 | 0.00 | 150.13 | +1.50% | +1.75% | -1.79 | -0.57% | 5 | ACTIVE |
| 6 | `coded_mean_rev` | 2. Mean Reversion (RSI / Bollinger) | 코딩 기반 (25) | 10,160.07 | 2,061.62 | 47.42 | +0.47% | +1.60% | -1.94 | -0.35% | 8 | ACTIVE |
| 7 | `xs_short_reversal` | 26. Cross-Sectional 5D Short Reversal | 코딩 기반 (25) | 10,143.51 | 493.57 | 143.51 | +1.44% | +1.44% | -2.10 | -0.31% | 5 | ACTIVE |
| 8 | `volume_accumulation` | 29. 20D Up/Down Volume Accumulation | 코딩 기반 (25) | 10,099.45 | 493.48 | 73.64 | +0.73% | +0.99% | -2.55 | -0.22% | 5 | ACTIVE |
| 9 | `risk_appetite_ratio` | 33. Cross-Asset Risk Appetite Ratio | 코딩 기반 (25) | 10,062.10 | 3,995.83 | 28.93 | +0.29% | +0.62% | -2.92 | -0.19% | 4 | ACTIVE |
| 10 | `min_variance_etf` | 23. Minimum-Variance ETF Portfolio | 코딩 기반 (25) | 10,060.06 | 2,697.91 | 41.60 | +0.42% | +0.60% | -2.94 | -0.16% | 5 | ACTIVE |
| 11 | `regime_beta_switch` | 30. Regime-Adaptive Beta Switch | 코딩 기반 (25) | 10,053.41 | 493.41 | 34.59 | +0.35% | +0.53% | -3.01 | -0.28% | 5 | ACTIVE |
| 12 | `coded_sector` | 4. Sector ETF Rotation | 코딩 기반 (25) | 10,051.00 | 1,987.92 | 56.51 | +0.57% | +0.51% | -3.03 | -0.12% | 4 | ACTIVE |
| 13 | `risk_parity_3` | 22. Risk Parity 3-Asset (8% Vol) | 코딩 기반 (25) | 10,039.08 | 6,402.53 | 13.30 | +0.13% | +0.39% | -3.15 | -0.18% | 3 | ACTIVE |
| 14 | `ctrl_random` | 10. Random Trader (Luck Control) | SPY / 랜덤 / 대조군 (3) | 10,034.01 | 2,012.46 | 34.01 | +0.34% | +0.34% | -3.20 | -0.23% | 6 | ACTIVE |
| 15 | `llm_catalyst` | 6. News & Catalyst Trader (Gemini Flash) | Gemini Flash LLM (4) | 10,028.74 | 8,498.95 | 28.74 | +0.29% | +0.29% | -3.25 | -0.30% | 1 | ACTIVE |
| 16 | `coded_trend` | 1. Trend Follower (MA / Breakout) | 코딩 기반 (25) | 10,016.27 | 5,992.67 | 16.27 | +0.16% | +0.16% | -3.38 | -0.25% | 2 | ACTIVE |
| 17 | `overnight_drift` | 16. Overnight Drift (Close→Open) | 코딩 기반 (25) | 10,014.09 | 6,097.39 | 14.09 | +0.14% | +0.14% | -3.40 | -0.06% | 2 | ACTIVE |
| 18 | `high52_anchor` | 27. 70D High Anchoring (George-Hwang) | 코딩 기반 (25) | 10,012.70 | 493.39 | 16.28 | +0.16% | +0.13% | -3.41 | -0.29% | 5 | ACTIVE |
| 19 | `coded_orb` | 3. Intraday ORB (Flat by Close) | 코딩 기반 (25) | 10,004.44 | 10,004.44 | 4.44 | +0.04% | +0.04% | -3.50 | -0.05% | 2 | ACTIVE |
| 20 | `llm_technical` | 5. Technical Analyst (Gemini Flash) | Gemini Flash LLM (4) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 21 | `llm_cautious` | 8. Cautious Risk-First Trader (Gemini Flash) | Gemini Flash LLM (4) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 22 | `claude_rmp` | 14. Claude Residual-Momentum Pullback | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 23 | `eod_loser_overnight` | 17. EOD Loser Overnight Reversal | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 24 | `turn_of_month` | 19. Turn-of-Month Calendar | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 25 | `gap_down_fill` | 20. Intraday Gap-Down Fill | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 26 | `dual_momentum_macro` | 21. Dual Momentum Macro ETF | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 27 | `breadth_timing` | 31. Market Breadth Equity Timing | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 28 | `panic_dip_index` | 32. Panic-Dip Index Buyer | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 29 | `vwap_reversion` | 34. Intraday VWAP -2σ Reversion | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 30 | `squeeze_breakout` | 35. Volatility Squeeze Breakout | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | 0.00% | 0.00% | -3.54 | 0.00% | 0 | ACTIVE |
| 31 | `kiro_pair_reversion` | 15. Kiro Pair-Spread Reversion (PSR) | 코딩 기반 (25) | 9,997.15 | 8,885.95 | -2.85 | -0.03% | -0.03% | -3.57 | -0.06% | 1 | ACTIVE |
| 32 | `low_vol_anomaly` | 24. Low-Volatility Stock Anomaly | 코딩 기반 (25) | 9,995.57 | 493.49 | 13.33 | +0.13% | -0.04% | -3.58 | -0.36% | 5 | ACTIVE |
| 33 | `intraday_momentum` | 18. Intraday Momentum (Gao 2018) | 코딩 기반 (25) | 9,994.83 | 9,994.83 | -5.17 | -0.05% | -0.05% | -3.59 | -0.07% | 4 | ACTIVE |
| 34 | `llm_macro` | 7. Macro & Sector Strategist (Gemini Flash) | Gemini Flash LLM (4) | 9,983.46 | 8,498.95 | -16.54 | -0.17% | -0.17% | -3.71 | -0.59% | 1 | ACTIVE |

---

## 4. 전략군별 성과 비교

| 전략군 (Group) | 에이전트 수 | 평균 일간 수익률 (%) | 평균 누적 수익률 (%) | 중간값 누적 수익률 (%) | 총 체결 건수 | 평균 마찰 비용 ($) | 총 거부 건수 |
|:---|---:|---:|---:|---:|---:|---:|---:|
| **공시 스타일 학습 (2)** | 2 | +1.58% | +1.83% | +1.83% | 10 | $8.66 | 0 |
| **SPY / 랜덤 / 대조군 (3)** | 3 | +1.08% | +2.45% | +3.48% | 12 | $7.31 | 0 |
| **코딩 기반 (25)** | 25 | +0.24% | +0.36% | +0.04% | 65 | $3.33 | 0 |
| **Gemini Flash LLM (4)** | 4 | +0.03% | +0.03% | +0.00% | 2 | $0.53 | 0 |

- **그룹별 분석**: 공시 스타일 학습 그룹(`11`, `12`)이 평균 일간 수익률 `+1.58%`, 누적 수익률 `+1.83%`로 대조군을 제외하고 가장 우수한 성과를 보였습니다. 반면, 코딩 기반 그룹은 25개 에이전트가 다양한 하위 전략(모멘텀, 평균회귀, 팩터 등)을 구동하며 평균 `+0.36%`의 누적 수익률을 기록했습니다. LLM 그룹은 소극적인 포지션 진입으로 인해 전반적으로 보합세(`+0.03%`)를 나타냈습니다.

---

## 5. 스페셜 분석: 14번 `claude_rmp` 텔레메트리 및 가설 검증

- **레짐 점수 및 플래그**: `regime_score = 0`, 플래그 `C1: false`, `C2: false`, `C3: false`, `C4: false`.
- **슬롯 및 자본 할당**: 활성 슬롯 `0개`, 헤트(Heat) 금액 `$0.0` (한도 `$350.0`), 자기자본 대비 헤트 비율 `0.0%` (한도 `3.5%`).
- **행동 코드 이력**: 당일 7개 사이클 모두 `SKIP_REGIME_OFF` 코드를 기록하며 신규 진입을 엄격히 차단했습니다.
- **성과 검증**: 시장이 `RISK-ON` 상태임에도 불구하고 레짐 필터의 엄격한 조건(C1~C4)을 충족하지 못해 현금 보유 상태를 유지하였으며, 이는 시스템이 의도한 리스크 관리 로직에 정확히 부합합니다.

---

## 6. 당일 매매 복기, 리스크 거부 및 마찰 감사

### 6.1 주요 체결 내역
- **2. Mean Reversion (`coded_mean_rev`)**: AVGO 매도(익절, 실현 손익 `+$181.15`, `+9.05%`), ABBV, TLT, KO, PEP, MCD 신규 매수 체결.
- **11. Berkshire Style (`style_berkshire`)**: QCOM, PG, PEP, MCD 신규 매수 체결 (버크셔 13F 패턴 스코어링 기반).
- **12. Congress Style (`style_congress`)**: WFC, TSLA, TMO, QCOM 신규 매수 체결 (의회 PTR 패턴 기반).

### 6.2 리스크 거부 (Rejections)
- 당일 에이전트들의 주문 거부 건수는 **0건**으로, 모든 주문이 정상적으로 실행되었습니다.

### 6.3 마찰 비용(Friction) 상위 에이전트
1. **2. Mean Reversion (`coded_mean_rev`)**: 총 마찰 비용 `$11.46` (초기 자금 대비 `0.11%`, 총 8회 체결).
2. **12. Congress Style (`style_congress`)**: 총 마찰 비용 `$9.98` (초기 자금 대비 `0.10%`, 총 5회 체결).
3. **10. Random Trader (`ctrl_random`)**: 총 마찰 비용 `$8.41` (초기 자금 대비 `0.08%`, 총 6회 체결).

---

## 7. 다음 거래일 관전 포인트 (모니터링 전용 · 전략 수정 금지)

### 7.1 시장 및 매크로 모니터링
- **2026-11-30 정규장 재개**: Early Close 직후 맞이하는 첫 정규장(09:30-16:00 ET)에서 기관 수급의 연속성과 단기 차익실현 물량 출회 여부를 모니터링합니다.
- **주요 지표 추적**: SPY 50일선 회복 여부 및 과매수 구간(RSI > 70)에 진입한 대형 기술주(QQQ, TSLA, AVGO 등)의 가격 탄력성을 관찰합니다.

### 7.2 에이전트별 리스크 관전 포인트
- **공시 스타일 에이전트(`11`, `12`)**: 대형주 포트폴리오의 편입 비중과 변동성 대응 모니터링.
- **평균회귀 및 모멘텀 에이전트(`2`, `28`)**: 활성 포지션들의 손절가(Stop-Loss) 및 익절가(Take-Profit) 도달 여부 점검.
