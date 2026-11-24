# AI Trading Arena 일일 리포트 — Day 1/30 (2026-11-24)

> **세션 정보**: Regular (09:30-16:00 ET) (실행 사이클: 4/13) | **시장 레짐(14번 기준)**: Score `0/4` | **SPY 일일/누적**: `-0.04%` / `-0.04%` | **활성 에이전트**: `34/34` (정지 `0`, 탈락 `0`)

---

## 1. 오늘의 핵심 요약 (Executive Summary)
- **일일 성과 Top / Bottom**: 최고 수익은 **11. Berkshire Style (13F Pattern)** (`+0.40%`, `+$39.77`), 최저 수익은 **3. Intraday ORB (Flat by Close)** (`-0.46%`, `-$46.04`)입니다.
- **벤치마크 비교**: 누적 기준 `9. Buy-and-Hold SPY` 상회 에이전트 `26/33명`, `10. Random Trader` 상회 에이전트 `24/33명`입니다.
- **전략군 흐름**: 당일 가장 강세인 전략군은 **공시 스타일 학습 (2)** (당일 평균 `+0.04%`), 가장 약세인 전략군은 **SPY / 랜덤 / 대조군 (3)** (당일 평균 `-0.05%`)입니다.
- **리스크·이상 징후**: 당일 신규 체결 `67건`, 리스크 관리자 거부 `0건`, 일일 정지(`DAILY_HALTED`) `0명`, 영구 탈락(`BLOWN_UP`) `0명`입니다.

---

## 2. 시장 매크로 및 기술적 레짐 브리핑

### 2.1 주요 벤치마크 및 매크로 자산 흐름
| Ticker | 종가 ($) | 일일 등락 (%) | ORB(첫 30분) 대비 위치 | SMA(20) / SMA(50) 대비 | RSI(14) | Volume Ratio | 핵심 흐름 요약 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **SPY** | $715.78 | -1.06% | ORB 범위 내 | -0.71% / -5.75% | 40.2 | 1.70x | 밴드 내부 (5일 -2.35%) |
| **QQQ** | $635.50 | -2.46% | ORB 범위 내 | -1.82% / -10.47% | 31.3 | 1.06x | 밴드 내부 (5일 -3.60%) |
| **TLT** | $81.90 | +0.32% | ORB 범위 내 | +0.38% / +2.26% | 69.7 | 0.96x | 밴드 내부 (5일 +2.41%) |
| **GLD** | $326.94 | -1.82% | ORB Low 이탈 (-1.63%) | -2.11% / -12.51% | 2.7 | 0.50x | BB 하단 이탈 (5일 -5.03%) |

### 2.2 시장 내부 구조 (Breadth · 변동성 · 모멘텀 · 뉴스)
- **시장 폭(Breadth) 및 변동성**: 개별주 중 `SMA(50)` 상회 종목 비율 `43.4%`, `RV10/RV60` 변동성 비율 `1.74`.
- **상대강도(`RS vs SPY`) 상위 / 63일 잔차 모멘텀 상위**: RS 상위 `PANW(+6.46), WMT(+4.50), ABBV(+4.26), HD(+3.90), CRM(+3.87)` | 잔차 모멘텀 상위 `BRK.B(2.45), WMT(1.78), PG(1.68), COST(1.54), AAPL(1.46)`.
- **과열/과매도 및 거래량 급증**: RSI(14)≥70 `CRM(RSI 83.5), ADBE(RSI 83.2), MA(RSI 72.0), UNH(RSI 72.8), JNJ(RSI 71.7)` / RSI(14)≤30 `NVDA(RSI 21.7), AMD(RSI 8.1), MU(RSI 17.0), GS(RSI 19.3), MS(RSI 27.6)` / 거래량 비율 상위 `BAC(1.77x), INTC(1.66x), AMD(1.62x), PLTR(1.62x), NKE(1.58x)`.
- **52주 고점 근접 종목 (11·12번 기준)**: `COST(-1.9%), PG(-2.4%), KO(-3.5%), DIS(-4.4%), BRK.B(-4.5%)`.
- **당일 주요 뉴스(Alpaca Headlines)**: [MRK] Merck Executives to Participate in 9th Annual Evercore Healthcare Conference; [AVGO] BNP Paribas Adjusts Broadcom Price Target; [LMT] Mizuho Initiates Lockheed Martin at Outperform With $765 Price Target; [DIS] The Walt Disney Studios Partners with Kaleidescape; [AAPL] J.P. Morgan Maintains Buy Rating and $355 Price Target for Apple

---

## 3. 전체 에이전트 성과 리더보드 (34개 에이전트)

| 순위 | 에이전트 (전략군) | 평가액 ($) | 일일 손익 ($ / %) | 누적 수익률 (%) | vs SPY (%p) | vs Random (%p) | MDD (%) | 체결 / 청산 | 승률 (%) | PF | 평균익 / 평균손 ($) | 마찰비용 ($) | 거부 | 상태 |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | 11. Berkshire Style (13F Pattern) (공시 스타일 학습 (2)) | $10,039.77 | +$39.77 / +0.40% | +0.40% | +0.44%p | +0.43%p | -0.01% | 3 / 0 | N/A | N/A | N/A / N/A | $4.21 | 0 | `ACTIVE` |
| 2 | 27. 70D High Anchoring (George-Hwang) (코딩 기반 (25)) | $10,036.58 | +$36.58 / +0.37% | +0.37% | +0.41%p | +0.40%p | 0.00% | 5 / 0 | N/A | N/A | N/A / N/A | $8.79 | 0 | `ACTIVE` |
| 3 | 2. Mean Reversion (RSI / Bollinger) (코딩 기반 (25)) | $10,019.61 | +$19.61 / +0.20% | +0.20% | +0.24%p | +0.23%p | -0.06% | 3 / 0 | N/A | N/A | N/A / N/A | $4.20 | 0 | `ACTIVE` |
| 4 | 28. Value + 63D Momentum Combo (코딩 기반 (25)) | $10,017.24 | +$17.24 / +0.17% | +0.17% | +0.21%p | +0.20%p | 0.00% | 5 / 0 | N/A | N/A | N/A / N/A | $6.54 | 0 | `ACTIVE` |
| 5 | 20. Intraday Gap-Down Fill (코딩 기반 (25)) | $10,005.31 | +$5.31 / +0.05% | +0.05% | +0.09%p | +0.08%p | -0.05% | 3 / 0 | N/A | N/A | N/A / N/A | $8.35 | 0 | `ACTIVE` |
| 6 | 23. Minimum-Variance ETF Portfolio (코딩 기반 (25)) | $10,000.32 | +$0.32 / +0.00% | +0.00% | +0.04%p | +0.03%p | -0.05% | 5 / 0 | N/A | N/A | N/A / N/A | $5.10 | 0 | `ACTIVE` |
| 7 | 5. Technical Analyst (Gemini Flash) (Gemini Flash LLM (4)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 8 | 6. News & Catalyst Trader (Gemini Flash) (Gemini Flash LLM (4)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 9 | 7. Macro & Sector Strategist (Gemini Flash) (Gemini Flash LLM (4)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 10 | 8. Cautious Risk-First Trader (Gemini Flash) (Gemini Flash LLM (4)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 11 | 14. Claude Residual-Momentum Pullback (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 12 | 15. Kiro Pair-Spread Reversion (PSR) (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 13 | 16. Overnight Drift (Close→Open) (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 14 | 17. EOD Loser Overnight Reversal (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 15 | 18. Intraday Momentum (Gao 2018) (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 16 | 19. Turn-of-Month Calendar (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 17 | 21. Dual Momentum Macro ETF (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 18 | 31. Market Breadth Equity Timing (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 19 | 32. Panic-Dip Index Buyer (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 20 | 33. Cross-Asset Risk Appetite Ratio (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 21 | 34. Intraday VWAP -2σ Reversion (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 22 | 35. Volatility Squeeze Breakout (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | +0.04%p | +0.03%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 23 | 4. Sector ETF Rotation (코딩 기반 (25)) | $9,998.19 | -$1.81 / -0.02% | -0.02% | +0.02%p | +0.01%p | -0.02% | 3 / 0 | N/A | N/A | N/A / N/A | $4.20 | 0 | `ACTIVE` |
| 24 | 1. Trend Follower (MA / Breakout) (코딩 기반 (25)) | $9,997.82 | -$2.18 / -0.02% | -0.02% | +0.02%p | +0.01%p | -0.02% | 3 / 0 | N/A | N/A | N/A / N/A | $4.21 | 0 | `ACTIVE` |
| 25 | 26. Cross-Sectional 5D Short Reversal (코딩 기반 (25)) | $9,997.36 | -$2.64 / -0.03% | -0.03% | +0.01%p | +0.00%p | -0.13% | 5 / 0 | N/A | N/A | N/A / N/A | $8.31 | 0 | `ACTIVE` |
| 26 | **10. Random Trader (Luck Control)** (SPY / 랜덤 / 대조군 (3)) | $9,997.20 | -$2.80 / -0.03% | -0.03% | +0.01%p | +0.00%p | -0.03% | 2 / 0 | N/A | N/A | N/A / N/A | $2.80 | 0 | `ACTIVE` |
| 27 | **9. Buy-and-Hold SPY (Benchmark)** (SPY / 랜덤 / 대조군 (3)) | $9,996.29 | -$3.71 / -0.04% | -0.04% | +0.00%p | -0.01%p | -0.07% | 1 / 0 | N/A | N/A | N/A / N/A | $6.86 | 0 | `ACTIVE` |
| 28 | 22. Risk Parity 3-Asset (8% Vol) (코딩 기반 (25)) | $9,996.21 | -$3.79 / -0.04% | -0.04% | +0.00%p | -0.01%p | -0.04% | 3 / 0 | N/A | N/A | N/A / N/A | $2.51 | 0 | `ACTIVE` |
| 29 | 25. Mega-Cap Top-5 Buy & Hold (Control) (SPY / 랜덤 / 대조군 (3)) | $9,993.05 | -$6.95 / -0.07% | -0.07% | -0.03%p | -0.04%p | -0.07% | 5 / 0 | N/A | N/A | N/A / N/A | $6.65 | 0 | `ACTIVE` |
| 30 | 30. Regime-Adaptive Beta Switch (코딩 기반 (25)) | $9,983.47 | -$16.53 / -0.17% | -0.17% | -0.13%p | -0.14%p | -0.28% | 5 / 0 | N/A | N/A | N/A / N/A | $6.56 | 0 | `ACTIVE` |
| 31 | 29. 20D Up/Down Volume Accumulation (코딩 기반 (25)) | $9,982.02 | -$17.98 / -0.18% | -0.18% | -0.14%p | -0.15%p | -0.23% | 5 / 0 | N/A | N/A | N/A / N/A | $8.73 | 0 | `ACTIVE` |
| 32 | 24. Low-Volatility Stock Anomaly (코딩 기반 (25)) | $9,980.79 | -$19.21 / -0.19% | -0.19% | -0.15%p | -0.16%p | -0.28% | 5 / 0 | N/A | N/A | N/A / N/A | $8.73 | 0 | `ACTIVE` |
| 33 | 12. Congress Style (House PTR Pattern) (공시 스타일 학습 (2)) | $9,966.51 | -$33.49 / -0.33% | -0.33% | -0.29%p | -0.30%p | -0.33% | 3 / 0 | N/A | N/A | N/A / N/A | $4.21 | 0 | `ACTIVE` |
| 34 | 3. Intraday ORB (Flat by Close) (코딩 기반 (25)) | $9,953.96 | -$46.04 / -0.46% | -0.46% | -0.42%p | -0.43%p | -0.46% | 3 / 0 | N/A | N/A | N/A / N/A | $4.19 | 0 | `ACTIVE` |

---

## 4. 전략군별 성과 비교

| 전략군 (인원) | 당일 평균 수익률 (%) | 누적 평균 수익률 (%) | 누적 중앙값 (%) | 최고 / 최저 에이전트 | 총 체결 수 | 평균 마찰비용 ($) | 총 리스크 거부 |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: |
| 코딩 기반 (25) | -0.01% | -0.01% | +0.00% | 27. 70D High Anchoring (George-Hwang) (+0.37%) / 3. Intraday ORB (Flat by Close) (-0.46%) | 53 | $3.22 | 0 |
| Gemini Flash LLM (4) | +0.00% | +0.00% | +0.00% | 5. Technical Analyst (Gemini Flash) (+0.00%) / 5. Technical Analyst (Gemini Flash) (+0.00%) | 0 | $0.00 | 0 |
| 공시 스타일 학습 (2) | +0.04% | +0.04% | +0.04% | 11. Berkshire Style (13F Pattern) (+0.40%) / 12. Congress Style (House PTR Pattern) (-0.33%) | 6 | $4.21 | 0 |
| SPY / 랜덤 / 대조군 (3) | -0.05% | -0.05% | -0.04% | 10. Random Trader (Luck Control) (-0.03%) / 25. Mega-Cap Top-5 Buy & Hold (Control) (-0.07%) | 8 | $5.44 | 0 |

- **그룹 간 격차 분석**: 당일 `공시 스타일 학습 (2)`이 평균 `+0.04%`로 선두를 기록했고, `SPY / 랜덤 / 대조군 (3)`은 평균 `-0.05%`를 기록했습니다. 누적 마찰비용은 체결 빈도가 높은 전략군일수록 수익률 차감 폭이 크게 나타났습니다.

---

## 5. 스페셜 분석: 14번 `claude_rmp` 텔레메트리 및 가설 검증

| 항목 | 당일 수치 / 상태 | 비고 |
| :--- | :--- | :--- |
| **레짐 점수 (`0~4`) 및 허용 슬롯** | `Score 0/4` (허용 슬롯: `0`개) | C1~C4: `{'C1': False, 'C2': False, 'C3': False, 'C4': False}` |
| **포트폴리오 리스크 (`Heat / HeatCap`)** | `0.00% / 3.50%` | `$0.00 / $350.00` |
| **거래당 평균 R (`Average R`)** | `N/A` (청산 `0`건 기준) | 누적 기준 |
| **필터·선정 지표 작동** | 당일 사유 코드: `SKIP_REGIME_OFF` | Top-5 리더: `BRK.B(2.45), WMT(1.78), PG(1.68), COST(1.54), AAPL(1.46)` |

- **가설 H1~H3 반증 트래커 (SPY 상승일 vs 하락일 노출도)**: 누적 `1일` 표본 기준 SPY 상승일 평균 노출도 `N/A` vs SPY 하락일 평균 노출도 `0.0%`. 단기 표본(`Day 1/30`)이므로 통계적 확정은 30거래일 종료 후 판정합니다.

---

## 6. 당일 매매 복기, 리스크 거부 및 마찰 감사

### 6.1 주요 진입·청산 복기 (Best & Worst / 특이 체결)
| 에이전트 | 종목 | 구분 | 결정→체결 시각 (ET) | 기준가→체결가 ($) | 실현손익 ($ / %) | 신뢰도 | 근거 및 지표 정합성 평가 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 1. Trend Follower (MA / Breakout) | MRK | `BUY` | 10:56→10:56 | $138.42→$138.52 | - | 0.71 | Buying MRK on momentum breakout: price $138.42 is at/near 20-bar high ($138.37) with 10-SMA ($136.46) > 30-SMA ($135.47), RSI(14) at 73.1, a |
| 1. Trend Follower (MA / Breakout) | PFE | `BUY` | 10:58→10:58 | $28.30→$28.32 | - | 0.74 | Buying PFE on momentum breakout: price $28.30 is at/near 20-bar high ($28.25) with 10-SMA ($27.72) > 30-SMA ($27.63), RSI(14) at 74.9, and v |
| 1. Trend Follower (MA / Breakout) | XLRE | `BUY` | 10:58→10:58 | $43.13→$43.16 | - | 0.74 | Buying XLRE on momentum breakout: price $43.13 is at/near 20-bar high ($43.31) with 10-SMA ($42.95) > 30-SMA ($42.94), RSI(14) at 56.2, and  |
| 2. Mean Reversion (RSI / Bollinger) | GLD | `BUY` | 10:56→10:56 | $327.48→$327.71 | - | 0.90 | Buying oversold dip in GLD: RSI(14) is 2.8 with price $327.48 near lower Bollinger Band ($329.12) while holding structural support above 94% |
| 2. Mean Reversion (RSI / Bollinger) | SMH | `BUY` | 10:58→10:58 | $423.66→$423.96 | - | 0.90 | Buying oversold dip in SMH: RSI(14) is 15.1 with price $423.66 near lower Bollinger Band ($426.52) while holding structural support above 94 |
| 2. Mean Reversion (RSI / Bollinger) | CAT | `BUY` | 10:58→10:58 | $526.48→$526.84 | - | 0.90 | Buying oversold dip in CAT: RSI(14) is 15.6 with price $533.62 near lower Bollinger Band ($510.85) while holding structural support above 94 |
| 3. Intraday ORB (Flat by Close) | XLP | `BUY` | 10:56→10:56 | $91.98→$92.05 | - | 0.84 | Buying XLP on 30-min Opening Range Breakout: price $91.98 cleared ORH $91.58 (ORL $90.44) on 1.98x relative volume. Stop placed at opening r |
| 3. Intraday ORB (Flat by Close) | ISRG | `BUY` | 10:58→10:58 | $443.25→$443.56 | - | 0.79 | Buying ISRG on 30-min Opening Range Breakout: price $433.44 cleared ORH $428.52 (ORL $411.24) on 1.48x relative volume. Stop placed at 1.2%  |
| 3. Intraday ORB (Flat by Close) | XLV | `BUY` | 10:58→10:58 | $183.98→$184.11 | - | 0.78 | Buying XLV on 30-min Opening Range Breakout: price $183.98 cleared ORH $183.24 (ORL $179.31) on 1.40x relative volume. Stop placed at openin |
| 4. Sector ETF Rotation | XLP | `BUY` | 10:56→10:56 | $91.98→$92.05 | - | 0.78 | Rotating into top-ranked sector ETF XLP (rank #1 of 11, composite RS score +1.42%, RS vs SPY +2.49%, 5d change -0.18%) trading at $91.98 abo |
| 4. Sector ETF Rotation | XLV | `BUY` | 10:58→10:58 | $183.98→$184.11 | - | 0.76 | Rotating into top-ranked sector ETF XLV (rank #2 of 11, composite RS score +1.03%, RS vs SPY +2.36%, 5d change -0.96%) trading at $183.98 ab |
| 4. Sector ETF Rotation | XLY | `BUY` | 10:58→10:58 | $117.17→$117.25 | - | 0.76 | Rotating into top-ranked sector ETF XLY (rank #3 of 11, composite RS score +0.91%, RS vs SPY +1.69%, 5d change -0.26%) trading at $117.17 ab |
| 9. Buy-and-Hold SPY (Benchmark) | SPY | `BUY` | 10:56→10:56 | $715.55→$716.05 | - | 1.00 | Allocating passive S&P 500 benchmark capital into SPY at $715.55 (requesting 13.9054 shares with catastrophic stop floor at $357.77) to hold |
| 10. Random Trader (Luck Control) | XLK | `BUY` | 10:58→10:58 | $156.12→$156.23 | - | 0.50 | Null-hypothesis random control: deterministic cycle seed (05cb9216) rolled 0.380 to randomly buy XLK at $156.12 with a 1.69% stop ($153.48)  |
| 10. Random Trader (Luck Control) | TMO | `BUY` | 10:58→10:58 | $695.59→$696.08 | - | 0.50 | Null-hypothesis random control: deterministic cycle seed (e0aa742a) rolled 0.658 to randomly buy TMO at $695.59 with a 1.73% stop ($683.54)  |

- **주요 손실 원인 진단**: 당일 실현 손실 청산 거래는 없습니다.

### 6.2 리스크 관리자 거부 로그 (`rejections`) 감사
- 당일 리스크 관리자 거부 건수 없음.

### 6.3 거래 마찰 비용 (스프레드 + 5 bps 슬리피지) 영향
- **27. 70D High Anchoring (George-Hwang)**: 누적 마찰 비용 `$8.79` (초기 자금 `$10,000` 대비 `0.09%`, 총 `5회` 체결)
- **29. 20D Up/Down Volume Accumulation**: 누적 마찰 비용 `$8.73` (초기 자금 `$10,000` 대비 `0.09%`, 총 `5회` 체결)
- **24. Low-Volatility Stock Anomaly**: 누적 마찰 비용 `$8.73` (초기 자금 `$10,000` 대비 `0.09%`, 총 `5회` 체결)

---

## 7. 다음 거래일 관전 포인트 (모니터링 전용 · 전략 수정 금지)

### 7.1 현재 보유(오픈) 포지션 손절·익절 근접도
| 에이전트 | 종목 | 진입가 ($) | 현재가 ($) | 미실현 손익 ($ / %) | 손절가 (잔여 거리 %) | 익절가 (잔여 거리 %) | 주시 포인트 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 3. Intraday ORB (Flat by Close) | XLP | $92.05 | $92.09 | +$0.99 (+0.05%) | $91.01 (-1.18%) | $93.93 (+1.99%) | 손절선 근접 주시 |
| 3. Intraday ORB (Flat by Close) | ISRG | $443.56 | $433.44 | -$45.64 (-2.28%) | $428.24 (-1.20%) | $443.84 (+2.40%) | 손절선 근접 주시 |
| 3. Intraday ORB (Flat by Close) | XLV | $184.11 | $183.98 | -$1.39 (-0.07%) | $181.28 (-1.47%) | $189.38 (+2.94%) | 손절선 근접 주시 |
| 1. Trend Follower (MA / Breakout) | PFE | $28.32 | $28.30 | -$1.40 (-0.07%) | $27.88 (-1.48%) | $29.15 (+3.00%) | 손절선 근접 주시 |
| 4. Sector ETF Rotation | XLV | $184.11 | $183.98 | -$1.40 (-0.07%) | $181.22 (-1.50%) | $191.34 (+4.00%) | 손절선 근접 주시 |
| 4. Sector ETF Rotation | XLY | $117.25 | $117.17 | -$1.40 (-0.07%) | $115.41 (-1.50%) | $121.86 (+4.00%) | 손절선 근접 주시 |
| 1. Trend Follower (MA / Breakout) | XLRE | $43.16 | $43.13 | -$1.40 (-0.07%) | $42.48 (-1.51%) | $44.42 (+2.99%) | 비중 20.0% |
| 1. Trend Follower (MA / Breakout) | MRK | $138.52 | $138.56 | +$0.62 (+0.03%) | $136.34 (-1.60%) | $142.57 (+2.89%) | 비중 20.0% |
| 4. Sector ETF Rotation | XLP | $92.05 | $92.09 | +$0.99 (+0.05%) | $90.61 (-1.61%) | $95.66 (+3.87%) | 비중 20.0% |
| 2. Mean Reversion (RSI / Bollinger) | GLD | $327.71 | $326.94 | -$4.70 (-0.23%) | $321.59 (-1.64%) | $335.67 (+2.67%) | 비중 19.9% |
| 10. Random Trader (Luck Control) | XLK | $156.23 | $156.12 | -$1.40 (-0.07%) | $153.48 (-1.69%) | $161.40 (+3.38%) | 비중 20.0% |
| 10. Random Trader (Luck Control) | TMO | $696.08 | $695.59 | -$1.40 (-0.07%) | $683.54 (-1.73%) | $719.69 (+3.46%) | 비중 20.0% |
| 20. Intraday Gap-Down Fill | QCOM | $162.05 | $162.65 | +$6.99 (+0.37%) | $159.80 (-1.75%) | $165.47 (+1.73%) | 비중 19.0% |
| 20. Intraday Gap-Down Fill | PANW | $351.22 | $351.74 | +$2.84 (+0.15%) | $345.53 (-1.77%) | $353.49 (+0.50%) | 비중 19.0% |
| 2. Mean Reversion (RSI / Bollinger) | CAT | $526.84 | $533.62 | +$25.71 (+1.29%) | $524.01 (-1.80%) | $546.96 (+2.50%) | 비중 20.2% |

### 7.2 서킷 브레이커 여유폭 및 레짐 분기점
- **서킷 브레이커 경계 에이전트**: 현재 `-10%` 탈락(`BLOWN_UP`)까지 여유폭 3.0%p 이내 또는 `-2%` 이하 일일 손실에 근접한 에이전트 없음.
- **레짐 점수 변화 가능성**: 14번 `claude_rmp` 현재 레짐 `0/4` (허용 슬롯 `0`개). 다음 거래일 `SPY` 종가 vs `SMA(50)` (`$759.45`), 개별주 Breadth `50%` 경계(`43.4%`), `RV10/RV60` `1.25` 임계치(`1.74`) 변동 여부를 주시합니다.
- **일정 체크**: 다음 미국 거래일 — **2026-11-25 Regular Session (09:30-16:00 ET)**.
