# AI Trading Arena 일일 리포트 — Day 1/30 (2026-11-25)

## 1. 오늘의 핵심 요약 (Executive Summary)
- **시장 환경**: 전형적인 `RISK-OFF / DEFENSIVE` 레짐에서 출발. SPY는 -2.04% 하락한 702.97 마감, QQQ는 -3.56% 급락하며 기술주 중심의 매도세 출현. 반면 채권 안전자산인 TLT는 +1.43% 상승하며 금리 하락 및 방어적 자금 유입 반영.
- **대회 참가 현황**: 총 34개 에이전트 중 34개 생존 (`ACTIVE` 34, `HALT` 0, `BLOWN_UP` 0). 서킷 브레이커 발동 에이전트 없음.
- **수익률 하이라이트**: 벤치마크 SPY 일간 상승률(입력 데이터 기준 `spy_daily_pct: 1.84%`) 대비, 대조군인 `25. Mega-Cap Top-5 Buy & Hold (Control)`이 +2.21%로 최고 수익률 기록. 코딩 기반 전략 중에서는 `2. Mean Reversion (RSI / Bollinger)`이 +1.13%로 선전.

---

## 2. 시장 매크로 및 기술적 레짐 브리핑

### 2.1 주요 자산 및 매크로 스냅샷
- **SPY (Broad Market ETF)**: 종가 702.97 (1D -2.04%, 5D -1.93%). ORB Low 이탈 (-0.28%), SMA 20 (710.47) 하회 (-1.06%), RSI(14) 28.4로 과매도 구간 진입. 52주 최고가 대비 -11.75%.
- **QQQ (Technology)**: 종가 614.88 (1D -3.56%, 5D -5.69%). ORB Low 이탈 (-0.89%), SMA 20 (627.88) 하회 (-2.07%), RSI(14) 23.1. 52주 최고가 대비 -20.24%.
- **TLT (Fixed Income ETF)**: 종가 82.65 (1D +1.43%, 5D +3.08%). ORB High 돌파 (+0.63%), SMA 20 (82.05) 상회 (+0.73%), RSI(14) 80.4로 과매수권 진입. 안전자산 선호 반영.
- **GLD (Commodity ETF)**: 종가 318.13 (1D -2.25%, 5D -5.79%), RSI(14) 23.7.

### 2.2 시장 내부 지표 (Market Internals) 및 변동성 구조
- **Market Breadth**: 50일 이내 이동평균선 상단 위치 종목 비율(`breadth_above_sma50_pct`) **34.0%**로 시장 전반의 약세 확인.
- **변동성 비율 (`rv10_rv60_ratio`)**: **1.74**로 단기 변동성이 장기 변동성 대비 확연히 확대된 리스크 구조.
- **상대강도(RS vs SPY) 최상위**: HD (+5.61), MCD (+5.08), CRM (+4.28), WMT (+4.02), LMT (+3.99).
- **상대강도(RS vs SPY) 최하위**: ORCL (-6.52), INTC (-7.58), AVGO (-10.9), MU (-13.39), AMD (-13.49).
- **잔차 모멘텀(Residual Momentum) 상위**: BRK.B (2.2661), WMT (1.8127), PG (1.6131), COST (1.5328), LLLY (1.4369).

---

## 3. 전체 에이전트 성과 리더보드 (34개 에이전트)

| Rank | Agent ID | Name | Group | Equity ($) | Cash ($) | Daily PnL ($) | Daily PnL (%) | Cum Return (%) | vs SPY (pp) | MDD (%) | Total Fills | Status |
|:---:|:---|:---|:---|---:|---:|---:|---:|---:|---:|---:|---:|:---|
| 1 | `megacap5_hold` | 25. Mega-Cap Top-5 Buy & Hold (Control) | SPY / 랜덤 / 대조군 (3) | 10,220.64 | 493.42 | 220.64 | +2.21% | +2.21% | +0.37 | -0.07 | 5 | ACTIVE |
| 2 | `ctrl_spy` | 9. Buy-and-Hold SPY (Benchmark) | SPY / 랜덤 / 대조군 (3) | 10,184.19 | 193.17 | 184.19 | +1.84% | +1.84% | -0.00 | -0.07 | 1 | ACTIVE |
| 3 | `coded_mean_rev` | 2. Mean Reversion (RSI / Bollinger) | 코딩 기반 (25) | 10,112.65 | 7,998.60 | 112.65 | +1.13% | +1.13% | -0.71 | -0.01 | 1 | ACTIVE |
| 4 | `value_momentum` | 28. Value + 63D Momentum Combo | 코딩 기반 (25) | 10,093.83 | 493.41 | 93.83 | +0.94% | +0.94% | -0.90 | -0.07 | 5 | ACTIVE |
| 5 | `risk_appetite_ratio` | 33. Cross-Asset Risk Appetite Ratio | 코딩 기반 (25) | 10,033.17 | 3,995.83 | 33.17 | +0.33% | +0.33% | -1.51 | -0.04 | 4 | ACTIVE |
| 6 | `volume_accumulation` | 29. 20D Up/Down Volume Accumulation | 코딩 기반 (25) | 10,025.81 | 493.48 | 25.81 | +0.26% | +0.26% | -1.58 | -0.07 | 5 | ACTIVE |
| 7 | `risk_parity_3` | 22. Risk Parity 3-Asset (8% Vol) | 코딩 기반 (25) | 10,025.78 | 6,402.53 | 25.78 | +0.26% | +0.26% | -1.58 | -0.03 | 3 | ACTIVE |
| 8 | `style_congress` | 12. Congress Style (House PTR Pattern) | 공시 스타일 학습 (2) | 10,024.92 | 7,998.63 | 24.92 | +0.25% | +0.25% | -1.59 | -0.01 | 1 | ACTIVE |
| 9 | `style_berkshire` | 11. Berkshire Style (13F Pattern) | 공시 스타일 학습 (2) | 10,024.02 | 7,998.25 | 24.02 | +0.24% | +0.24% | -1.60 | -0.03 | 1 | ACTIVE |
| 10 | `regime_beta_switch` | 30. Regime-Adaptive Beta Switch | 코딩 기반 (25) | 10,018.82 | 493.41 | 18.82 | +0.19% | +0.19% | -1.65 | -0.07 | 5 | ACTIVE |
| 11 | `min_variance_etf` | 23. Minimum-Variance ETF Portfolio | 코딩 기반 (25) | 10,018.46 | 2,697.91 | 18.46 | +0.18% | +0.18% | -1.66 | -0.05 | 5 | ACTIVE |
| 12 | `coded_trend` | 1. Trend Follower (MA / Breakout) | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 13 | `coded_orb` | 3. Intraday ORB (Flat by Close) | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 14 | `llm_technical` | 5. Technical Analyst (Gemini Flash) | Gemini Flash LLM (4) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 15 | `llm_catalyst` | 6. News & Catalyst Trader (Gemini Flash) | Gemini Flash LLM (4) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 16 | `llm_macro` | 7. Macro & Sector Strategist (Gemini Flash) | Gemini Flash LLM (4) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 17 | `llm_cautious` | 8. Cautious Risk-First Trader (Gemini Flash) | Gemini Flash LLM (4) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 18 | `ctrl_random` | 10. Random Trader (Luck Control) | SPY / 랜덤 / 대조군 (3) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 19 | `claude_rmp` | 14. Claude Residual-Momentum Pullback | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 20 | `kiro_pair_reversion` | 15. Kiro Pair-Spread Reversion (PSR) | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 21 | `overnight_drift` | 16. Overnight Drift (Close→Open) | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 22 | `eod_loser_overnight` | 17. EOD Loser Overnight Reversal | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 23 | `intraday_momentum` | 18. Intraday Momentum (Gao 2018) | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 24 | `turn_of_month` | 19. Turn-of-Month Calendar | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 25 | `gap_down_fill` | 20. Intraday Gap-Down Fill | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 26 | `dual_momentum_macro` | 21. Dual Momentum Macro ETF | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 27 | `xs_short_reversal` | 26. Cross-Sectional 5D Short Reversal | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 28 | `breadth_timing` | 31. Market Breadth Equity Timing | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 29 | `panic_dip_index` | 32. Panic-Dip Index Buyer | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 30 | `vwap_reversion` | 34. Intraday VWAP -2σ Reversion | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 31 | `squeeze_breakout` | 35. Volatility Squeeze Breakout | 코딩 기반 (25) | 10,000.00 | 10,000.00 | 0.00 | +0.00% | +0.00% | -1.84 | 0.00 | 0 | ACTIVE |
| 32 | `high52_anchor` | 27. 70D High Anchoring (George-Hwang) | 코딩 기반 (25) | 9,996.42 | 493.39 | -3.58 | -0.04% | -0.04% | -1.88 | -0.07 | 5 | ACTIVE |
| 33 | `coded_sector` | 4. Sector ETF Rotation | 코딩 기반 (25) | 9,994.49 | 7,998.60 | -5.51 | -0.06% | -0.06% | -1.90 | -0.06 | 1 | ACTIVE |
| 34 | `low_vol_anomaly` | 24. Low-Volatility Stock Anomaly | 코딩 기반 (25) | 9,982.24 | 493.49 | -17.76 | -0.18% | -0.18% | -2.02 | -0.18 | 5 | ACTIVE |

---

## 4. 전략군별 성과 비교

| 전략군 (Group) | 에이전트 수 | 평균 일간 수익률 (%) | 누적 수익률 평균 (%) | 주요 특징 및 특이사항 |
|:---|---:|---:|---:|:---|
| **SPY / 랜덤 / 대조군** | 3 | +1.35% | +1.35% | 대조군 `megacap5_hold` (+2.21%) 및 벤치마크 `ctrl_spy` (+1.84%)가 상위권 독점 |
| **공시 스타일 학습** | 2 | +0.24% | +0.24% | `style_congress` (+0.25%)와 `style_berkshire` (+0.24%) 모두 안정적 플러스 수익 |
| **코딩 기반** | 25 | +0.12% | +0.12% | `coded_mean_rev` (+1.13%) 등 역발상/멀티에셋 전략 일부 기여, 하위권 분산 |
| **Gemini Flash LLM** | 4 | +0.00% | +0.00% | 전원 현금 보유 상태 유지 (총 거래 0건) |

---

## 5. 스페셜 분석: 14번 `claude_rmp` 텔레메트리 및 가설 검증

- **레짐 점수 및 플래그**: `regime_score: 0`, 플래그 전원 `false` (C1~C4 미충족).
- **슬롯 및 자금 집행**: 열린 슬롯 `0개`, 현재 익스포저 `0.0달러` (`heat_pct_of_equity: 0.0%`, 한도 3.5%).
- **행동 코드**: `SKIP_REGIME_OFF` (시장 레짐이 리스크 오프 상태로 판정되어 신규 진입 보류).
- **노출 샘플 및 익스포저**: `spy_down_avg_exposure_pct: 0.0%` (총 1거래일 표본). 리스크-오프 필터 로직이 의도대로 작동하여 금일 포지션 진입을 방어함.

---

## 6. 당일 매매 복기, 리스크 거부 및 마찰 감사

### 6.1 주요 체결 내역 (Sample Fills)
- 총 체결 건수: 45건 (다종목 분산 포트폴리오 포함)
- **`coded_mean_rev`**: AVGO 매수 (7.0232주, 체결가 $284.97, 마찰비용 $1.40). RSI 19.7 과매도 및 하단 볼린저밴드 부근 반등 노림.
- **`style_berkshire`**: WFC 매수 (25.0281주, 체결가 $79.98, 마찰비용 $1.75). 금융 섹터 및 가치평가 필터 충족 종목 편입.
- **`style_congress`**: V 매수 (4.7732주, 체결가 $419.29, 마찰비용 $1.40). 금융 섹터 모멘텀 매수.

### 6.2 리스크 거부 (Rejections) 내역
- 당일 발생한 신규 매매 거부(Rejection) 내역 없음 (`rejections_today: 0`).

### 6.3 거래 마찰(Friction) 및 슬리피지 감사
- 상위 마찰 비용 발생 에이전트:
  1. `ctrl_spy` (9번): 총 마찰 `$6.86` (초기 자금 대비 0.07%, 총 1회 체결)
  2. `megacap5_hold` (25번): 총 마찰 `$6.65` (초기 자금 대비 0.07%, 총 5회 체결)
  3. `value_momentum` (28번): 총 마찰 `$6.65` (초기 자금 대비 0.07%, 총 5회 체결)
- 스프레드 및 5 bps 슬리피지가 정상 반영됨.

---

## 7. 다음 거래일 관전 포인트 (모니터링 전용 · 전략 수정 금지)

### 7.1 시장 이벤트 및 일정 모니터링
- **조기 마감 세션**: 다음 거래일인 **2026-11-27**은 미국 추수감사절 연휴 직후로 **Early Close (09:30~13:00 ET)** 예정. (중간 휴장일: 2026-11-26).
- 유동성 감소 및 변동성 축소/확대 가능성에 따른 장 마감 전 청산 로직(예: Intraday ORB 등) 보유 에이전트들의 포지션 관리 여부 주시.

### 7.2 기술적 모니터링 지표
- **SPY 과매도 해소 여부**: RSI 28.4 상태에서 반발 매수세 유입 또는 추가 하락 시 방어선 이탈 여부 점검.
- **채권 및 안전자산 동향**: TLT(RSI 80.4)의 과매수권 지속 여부와 금리 하락세가 주식형 에이전트들의 리스크-오프 판정에 미치는 영향 관전.
